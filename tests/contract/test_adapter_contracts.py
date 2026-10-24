"""Cross-stack adapter contracts for ROI, Ring webhooks, weather, and vision.

These tests pin the *shape* of each boundary so a refactor cannot silently drift from the
coordination web app or from the documented provider mapping.
"""

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from harvestgrid_domain import (
    CameraViewRequest,
    CameraViewWorkflow,
    MaskKind,
    MaskRegion,
    Polygon,
    view_document,
)
from harvestgrid_ring_adapter import (
    ACK_BUDGET,
    ALLOWED_EVENT_TYPES,
    FORBIDDEN_FIELDS,
    MAX_BODY_BYTES,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    InMemoryMediaJobQueue,
    InMemoryWebhookStore,
    RingWebhookService,
    compute_signature,
)
from harvestgrid_vision import (
    COLUMN_UNITS,
    FEATURES_V1_COLUMNS,
    MISSING_VALUE,
    FeatureExtractionWorker,
)
from harvestgrid_vision.worker import ExtractionRequest
from harvestgrid_weather import (
    ARCHIVE_URL,
    COARSE_DECIMALS,
    DAILY_FIELDS,
    FORECAST_URL,
    GDD_BASE_CELSIUS,
    OpenMeteoWeatherAdapter,
    coarse_point,
)
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 24, 8, 0, tzinfo=UTC)


def load_json(relative: str) -> dict[str, Any]:
    return json.loads((ROOT / relative).read_text())


def validator(name: str) -> Draft202012Validator:
    schema = load_json(f"packages/contracts/{name}")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def view_document_fixture() -> dict[str, Any]:
    workflow = CameraViewWorkflow.create(
        CameraViewRequest(
            view_id="view-contract",
            tree_id="tree-contract",
            device_id="device-contract",
            owner_id="owner-contract",
            coordinator_id="coordinator-contract",
            roi=Polygon.rectangle(0.25, 0.15, 0.75, 0.75),
            masks=(
                MaskRegion(MaskKind.NEIGHBOR_PROPERTY, Polygon.rectangle(0.0, 0.0, 0.28, 1.0)),
                MaskRegion(MaskKind.PUBLIC_SIDEWALK, Polygon.rectangle(0.0, 0.78, 1.0, 1.0)),
            ),
            reference_crop_sha256="d" * 64,
            frame_width=1920,
            frame_height=1080,
            at=T0,
        )
    )
    workflow.owner_approve(actor_id="owner-contract", expected_version=0, at=T0)
    workflow.approve(actor_id="coordinator-contract", expected_version=1, at=T0)
    return view_document(workflow.snapshot)


def test_camera_view_document_matches_its_schema() -> None:
    document = view_document_fixture()
    validator("camera-view.schema.json").validate(document)
    assert document["status"] == "approved"
    assert document["forecasting_active"] is True
    assert document["effective_roi_fraction"] < 1.0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document.update({"exact_address": "FIXTURE 1 Example Street"}),
        lambda document: document.update({"coordinates": [52.0067, 4.3556]}),
        lambda document: document.update({"raw_media_uri": "s3://fixture/raw.jpg"}),
        lambda document: document.update({"person_or_vehicle_detection_enabled": True}),
        lambda document: document.update({"exact_address_included": True}),
        lambda document: document.update({"raw_media_included": True}),
        lambda document: document.update({"status": "collecting"}),
        lambda document: document["masks"][0].update({"kind": "face_region"}),
        lambda document: document["roi"][0].__setitem__(0, 1.5),
        lambda document: document.update({"reference_crop_sha256": "not-a-digest"}),
        lambda document: document.update({"effective_roi_fraction": 4}),
        lambda document: document.update({"contract_version": "camera-view-v2"}),
    ],
)
def test_camera_view_schema_rejects_privacy_and_scope_violations(mutation: Any) -> None:
    document = json.loads(json.dumps(view_document_fixture()))
    mutation(document)
    with pytest.raises(ValidationError):
        validator("camera-view.schema.json").validate(document)


def test_webhook_contract_constants_match_the_reviewed_design() -> None:
    # Five-second acknowledgement and bounded bodies are contractual.
    assert ACK_BUDGET == timedelta(seconds=5)
    assert MAX_BODY_BYTES == 64 * 1024
    assert SIGNATURE_HEADER == "x-ring-signature"
    assert TIMESTAMP_HEADER == "x-ring-timestamp"
    assert ALLOWED_EVENT_TYPES == {
        "device.motion",
        "device.snapshot",
        "device.live_view_unavailable",
        "device.offline",
        "device.online",
    }
    # Webhook payloads may not carry surveillance or location fields.
    assert {"person", "face", "license_plate", "address", "coordinates"} <= FORBIDDEN_FIELDS


def test_signed_webhook_round_trip_rejects_forgeries_and_deduplicates() -> None:
    now = {"value": T0}
    service = RingWebhookService(
        secret=b"contract-secret-0123456789abcdefgh",
        store=InMemoryWebhookStore(),
        queue=InMemoryMediaJobQueue(),
        clock=lambda: now["value"],
    )
    body = json.dumps(
        {
            "event_id": "evt-contract",
            "event_type": "device.snapshot",
            "device_id": "device-contract",
            "connection_id": "connection-contract",
            "occurred_at": "2026-10-24T07:59:00Z",
        },
        separators=(",", ":"),
    ).encode()
    timestamp = str(int(T0.timestamp()))

    def request(signature: str) -> Any:
        from harvestgrid_ring_adapter import WebhookRequest

        return WebhookRequest(
            method="POST",
            path="/v1/ring/webhooks",
            headers={
                "Content-Type": "application/json",
                SIGNATURE_HEADER: signature,
                TIMESTAMP_HEADER: timestamp,
            },
            body=body,
            received_at=T0,
        )

    forged = service.handle(request("sha256=" + "0" * 64))
    assert forged.status == 401
    assert forged.body["code"] == "signature_mismatch"
    assert service.pending_jobs() == ()

    accepted = service.handle(
        request(compute_signature(secret=service.secret, timestamp=timestamp, body=body))
    )
    duplicate = service.handle(
        request(compute_signature(secret=service.secret, timestamp=timestamp, body=body))
    )
    assert accepted.status == 200
    assert duplicate.duplicate is True
    assert len(service.pending_jobs()) == 1


def test_weather_contract_uses_coarse_coordinates_and_documented_endpoints() -> None:
    assert ARCHIVE_URL == "https://archive-api.open-meteo.com/v1/archive"
    assert FORECAST_URL == "https://api.open-meteo.com/v1/forecast"
    assert COARSE_DECIMALS == 1
    assert DAILY_FIELDS == (
        "temperature_2m_mean",
        "temperature_2m_min",
        "temperature_2m_max",
        "precipitation_sum",
    )
    assert GDD_BASE_CELSIUS == 5.0

    point = coarse_point("u173z")
    params = point.as_params()
    assert set(params) == {"latitude", "longitude"}
    assert all(len(value.split(".")[1]) == 1 for value in params.values())

    captured: list[dict[str, str]] = []

    class Transport:
        def get(self, url: str, params: dict[str, str], timeout: float) -> Any:
            captured.append({"url": url, **params})
            return (
                200,
                {},
                json.dumps(
                    {
                        "daily": {
                            "time": ["2026-10-20", "2026-10-21"],
                            "temperature_2m_mean": [11.0, 12.0],
                            "temperature_2m_min": [7.0, 8.0],
                            "temperature_2m_max": [15.0, 16.0],
                            "precipitation_sum": [0.0, 1.5],
                        }
                    }
                ).encode(),
            )

    adapter = OpenMeteoWeatherAdapter(transport=Transport(), clock=lambda: T0)
    observation = adapter.historical(
        geohash="u173z", start=date(2026, 10, 20), end=date(2026, 10, 21)
    )
    sent = captured[0]
    assert sent["url"] == ARCHIVE_URL
    assert sent["latitude"] == params["latitude"]
    assert sent["longitude"] == params["longitude"]
    assert "access_notes" not in sent
    assert observation.provenance["exact_coordinates_included"] is False
    assert len(observation.payload_sha256) == 64


def test_features_v1_contract_is_stable_and_complete() -> None:
    expected = (
        "visible_fruit_count",
        "count_lower",
        "count_upper",
        "detection_confidence_mean",
        "detection_area_p10",
        "detection_area_p50",
        "detection_area_p90",
        "area_change_ratio",
        "colour_saturation_mean",
        "colour_value_mean",
        "occlusion_fraction",
        "quality_score",
        "roi_coverage",
        "registration_score",
        "day_of_year",
        "observation_hour_utc",
    )
    assert FEATURES_V1_COLUMNS == expected
    assert set(COLUMN_UNITS) == set(FEATURES_V1_COLUMNS)
    assert MISSING_VALUE == -1.0


def test_observation_provenance_document_is_non_sensitive() -> None:
    workflow = CameraViewWorkflow.create(
        CameraViewRequest(
            view_id="view-obs",
            tree_id="tree-obs",
            device_id="device-obs",
            owner_id="owner-obs",
            coordinator_id="coordinator-obs",
            roi=Polygon.rectangle(0.2, 0.2, 0.8, 0.8),
            masks=(MaskRegion(MaskKind.NEIGHBOR_PROPERTY, Polygon.rectangle(0, 0, 0.25, 1)),),
            reference_crop_sha256="e" * 64,
            frame_width=1280,
            frame_height=720,
            at=T0,
        )
    )
    workflow.owner_approve(actor_id="owner-obs", expected_version=0, at=T0)
    view = workflow.approve(actor_id="coordinator-obs", expected_version=1, at=T0)
    width, height = 24, 16
    luminance = tuple(
        0.35 if (row in (5, 6) and 6 <= column <= 12) else 0.62
        for row in range(height)
        for column in range(width)
    )
    saturation = tuple(
        0.8 if (row in (5, 6) and 6 <= column <= 12) else 0.12
        for row in range(height)
        for column in range(width)
    )
    observation = FeatureExtractionWorker().extract(
        ExtractionRequest(
            view=view,
            frame_id="frame-obs",
            width=width,
            height=height,
            luminance=luminance,
            saturation=saturation,
            captured_at=T0,
            extracted_at=T0 + timedelta(minutes=2),
            detected_at_grid=(width, height),
            registration_score=0.9,
            provenance="fixture",
        )
    )
    document = observation.provenance_document()
    assert document["detector_model_kind"] == "heuristic_fixture"
    assert document["detector_data_version"] == "no_training_data"
    assert document["feature_version"] == "features_v1"
    assert document["internal_ripeness_inferred"] is False
    assert document["food_safety_inferred"] is False
    assert document["disease_inferred"] is False
    assert document["raw_media_included"] is False
    serialized = json.dumps(document)
    assert "address" not in serialized.lower()
    assert "latitude" not in serialized.lower()


def test_coordinator_web_weights_match_the_python_engine() -> None:
    engine_source = (ROOT / "packages/matching/harvestgrid_domain_matching/engine.py").read_text()
    web_source = (ROOT / "apps/web/src/domain/matching.ts").read_text()

    python_weights = {
        name: float(value)
        for value, name in re.findall(
            r"^\s*\+?\s*(0\.\d+) \* features\.(\w+)$", engine_source, flags=re.MULTILINE
        )
    }
    assert python_weights, "python engine must document its ranking weights"
    ts_block = re.search(
        r"RANKING_WEIGHTS = \{(?P<body>.*?)\} as const", web_source, flags=re.DOTALL
    )
    assert ts_block is not None, "coordination app must declare RANKING_WEIGHTS"
    ts_weights = {
        re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower(): float(value)
        for name, value in re.findall(r"(\w+): (0\.\d+),", ts_block.group("body"))
    }
    assert ts_weights == python_weights
    assert abs(sum(ts_weights.values()) - 1.0) < 1e-9


def test_donation_receipt_schema_matches_the_domain_projection() -> None:
    from harvestgrid_domain.outcomes import DonationReceipt

    receipt = DonationReceipt(
        receipt_number="HG-0123456789ABCDEF0123",
        glean_id="glean-contract",
        recipient_id="recipient-contract",
        accepted_weight_kg=__import__("decimal").Decimal("8.250"),
        donation_date=date(2026, 10, 30),
    )
    projection = receipt.as_non_sensitive_dict()
    validator("donation-receipt.schema.json").validate(projection)
    assert set(projection) == {
        "receipt_number",
        "glean_id",
        "recipient_id",
        "accepted_weight_kg",
        "donation_date",
    }

    for leaked in (
        {"address": "FIXTURE 1 Example Street"},
        {"access_notes": "gate code 0000"},
        {"raw_media_uri": "s3://fixture/raw.jpg"},
        {"volunteer_id": "volunteer-contract"},
        {"evidence_sha256": "f" * 64},
        {"scale_reference": "scale-01"},
        {"rejection_reasons": ["spoiled"]},
    ):
        with pytest.raises(ValidationError):
            validator("donation-receipt.schema.json").validate({**projection, **leaked})
