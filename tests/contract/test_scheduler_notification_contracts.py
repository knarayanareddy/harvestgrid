"""Contracts for the weather scheduler, ripening curves, SMS dispatch, and ONNX artifacts."""

import json
import shutil
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from harvestgrid_forecasting import (
    CelerySettings,
    DailyTemperature,
    PhenologyPrior,
    RipeningStage,
    TreeSubscription,
    project_ripening,
    serialized_tick,
    validate_coarse_geohash,
)
from harvestgrid_notifications import (
    AlertKind,
    Channel,
    DispatchAuthorization,
    InMemoryNotificationLedger,
    NotificationDispatchService,
    PickupAlert,
    RecordingSmsTransport,
    VolunteerContact,
    render_alert,
)
from harvestgrid_vision.weights import load_manifest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 7, 2, 8, 0, tzinfo=UTC)
STAGES: tuple[tuple[RipeningStage, float], ...] = (
    (RipeningStage.DORMANT, 0.0),
    (RipeningStage.BLOOM, 120.0),
    (RipeningStage.FRUIT_SET, 200.0),
    (RipeningStage.VERAISON, 700.0),
    (RipeningStage.HARVEST_RIPE, 1000.0),
)


def validator(name: str) -> Draft202012Validator:
    schema = json.loads((ROOT / "packages/contracts" / name).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def projection():
    observed = tuple(
        DailyTemperature(
            day=date(2026, 5, 1) + timedelta(days=offset),
            temperature_min_c=9.0,
            temperature_max_c=19.0,
        )
        for offset in range(60)
    )
    return project_ripening(
        prior=PhenologyPrior(
            cultivar_id="european_plum",
            content_version="european_plum_v1",
            base_temperature_c=5.0,
            stage_gdd=STAGES,
            review_status="draft",
        ),
        coarse_geohash="u173z",
        as_of=observed[-1].day,
        observed=observed,
        forecast=tuple(
            DailyTemperature(
                day=observed[-1].day + timedelta(days=offset),
                temperature_min_c=12.0,
                temperature_max_c=22.0,
            )
            for offset in range(1, 11)
        ),
    )


def test_projection_document_matches_its_schema() -> None:
    document = projection().as_dict()
    validator("ripening-projection.schema.json").validate(document)
    assert document["internal_ripeness_inferred"] is False
    assert document["food_safety_inferred"] is False
    assert document["human_readiness_review_required"] is True
    assert document["prior_review_status"] == "draft"
    assert document["experimental"] is True
    assert document["confidence"] == "low"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document.update({"latitude": 52.0}),
        lambda document: document.update({"address": "FIXTURE 1 Example Street"}),
        lambda document: document.update({"raw_media_uri": "s3://fixture/frame.jpg"}),
        lambda document: document.update({"internal_ripeness_inferred": True}),
        lambda document: document.update({"food_safety_inferred": True}),
        lambda document: document.update({"human_readiness_review_required": False}),
        lambda document: document.update({"confidence": "high"}),
        lambda document: document.update({"coarse_geohash": "52.0067,4.3556"}),
        lambda document: document.update({"stage": "ripe_enough"}),
        lambda document: document["reason_codes"].append("DUPLICATE CASE"),
        lambda document: document.update({"accumulated_gdd": -1}),
    ],
)
def test_projection_schema_rejects_scope_and_privacy_violations(mutation) -> None:
    document = json.loads(json.dumps(projection().as_dict()))
    mutation(document)
    with pytest.raises(ValidationError):
        validator("ripening-projection.schema.json").validate(document)


def test_tick_payload_is_serializable_and_free_of_location_detail() -> None:
    payload = {
        "started_at": T0.isoformat(),
        "trees_considered": 3,
        "processed": [{"tree_id": "tree-1", "status": "updated", "version": 2}],
        "observations_new": 2,
    }
    text = serialized_tick(payload)
    assert json.loads(text)["trees_considered"] == 3
    for forbidden in ("address", "latitude", "longitude", "phone", "access_notes"):
        assert forbidden not in text.casefold()


def test_celery_beat_schedule_is_pinned() -> None:
    settings = CelerySettings(broker_url="redis://fixture-broker:6379/0")
    schedule = settings.beat_schedule()
    entry = schedule["harvestgrid-weather-poll"]
    assert entry["task"] == "harvestgrid.forecasting.poll_weather_tick"
    assert entry["schedule"] == 900.0
    assert entry["options"]["expires"] == 900.0
    assert settings.as_dict()["beat_seconds"] == 900.0


def test_subscription_accepts_only_coarse_geohashes() -> None:
    assert validate_coarse_geohash("u173z") == "u173z"
    with pytest.raises(Exception, match="coarse_location_invalid"):
        validate_coarse_geohash("u173zxy")
    subscription = TreeSubscription(
        tree_id="tree-1",
        organization_id="org-fixture",
        cultivar_id="european_plum",
        coarse_geohash="u173z",
        prior=PhenologyPrior(
            cultivar_id="european_plum",
            content_version="european_plum_v1",
            base_temperature_c=5.0,
            stage_gdd=STAGES,
            review_status="draft",
        ),
    )
    assert subscription.collection_active is True
    assert not hasattr(subscription, "latitude")


def dispatch_report():
    alert = PickupAlert(
        glean_id="glean-contract",
        crop_label="european plum",
        coarse_area_label="Delft, coarse cell u173z",
        window_start=datetime(2026, 7, 3, 9, 0, tzinfo=UTC),
        window_end=datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
        access_constraints=("street parking only",),
        kind=AlertKind.INVITATION,
    )
    service = NotificationDispatchService(
        transport=RecordingSmsTransport(),
        ledger=InMemoryNotificationLedger(),
        clock=lambda: T0,
    )
    return service.dispatch(
        alert=alert,
        recipients=(
            VolunteerContact(
                volunteer_id="volunteer-a",
                phone_e164="+31612345678",
                sms_opt_in=True,
                sms_opt_in_version="sms-opt-in-v1",
            ),
            VolunteerContact(
                volunteer_id="volunteer-b",
                channel=Channel.EMAIL,
            ),
        ),
        authorization=DispatchAuthorization(
            glean_id="glean-contract",
            coordinator_id="coordinator-fixture",
            approved_by="coordinator-fixture",
            approved_at=T0,
            owner_approval_current=True,
            glean_state="invited",
        ),
        restricted_markers=("FIXTURE 1 Example Street", "gate code 0000"),
    )


def test_dispatch_audit_matches_its_schema() -> None:
    document = dispatch_report().as_dict()
    validator("notification-dispatch.schema.json").validate(document)
    assert document["sent"] == 1
    assert {outcome["status"] for outcome in document["outcomes"]} == {"sent", "skipped"}


@pytest.mark.parametrize(
    "leak",
    [
        {"phone": "+31612345678"},
        {"phone_e164": "+31612345678"},
        {"body": "HarvestGrid New glean: european plum"},
        {"address": "FIXTURE 1 Example Street"},
        {"access_notes": "gate code 0000"},
    ],
)
def test_dispatch_schema_rejects_contact_detail(leak: dict[str, str]) -> None:
    document = dispatch_report().as_dict()
    document.update(leak)
    with pytest.raises(ValidationError):
        validator("notification-dispatch.schema.json").validate(document)


def test_message_body_never_contains_address_or_phone() -> None:
    body = render_alert(
        PickupAlert(
            glean_id="glean-contract",
            crop_label="european plum",
            coarse_area_label="Delft, coarse cell u173z",
            window_start=datetime(2026, 7, 3, 9, 0, tzinfo=UTC),
            window_end=datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
            access_constraints=("bring a ladder",),
        ),
        restricted_markers=("FIXTURE 1 Example Street", "gate code 0000"),
    )
    assert "FIXTURE 1 Example Street" not in body
    assert "gate code 0000" not in body
    assert "+31612345678" not in body
    assert "u173z" in body


def test_manifest_declares_no_operational_fruit_detector() -> None:
    manifest = load_manifest()
    primary = manifest.artifact("plum-yolov8n-int8-v1")
    assert primary.status == "blocked"
    assert primary.approved_for_operational_use is False
    assert primary.expected_sha256 is None
    assert primary.quantization == "int8"
    assert primary.architecture == "yolov8n"
    assert manifest.artifact("synthetic-shape-smoke-v1").approved_for_operational_use is False
    # A verified local install may exist; what the policy forbids is *committing* a binary.
    git = shutil.which("git") or "git"
    tracked = subprocess.run(  # noqa: S603
        [git, "ls-files", "--", "packages/vision/weights"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    assert [name for name in tracked if name.endswith((".onnx", ".onnx.data"))] == []
    assert "packages/vision/weights/manifest.json" in tracked


def test_gitignore_excludes_model_binaries() -> None:
    ignore = (ROOT / ".gitignore").read_text().splitlines()
    assert "*.onnx" in ignore
    assert "models/artifacts/" in ignore
    assert "datasets/private/" in ignore
