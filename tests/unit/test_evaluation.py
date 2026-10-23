import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from harvestgrid_domain import (
    ReviewedEvaluationSet,
    WeightEntry,
    WeightRecord,
    WeightSource,
    evaluate_model_data_gate,
)

ROOT = Path(__file__).resolve().parents[2]
START = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
END = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)


@pytest.fixture
def forecast_document() -> dict[str, Any]:
    return json.loads((ROOT / "examples" / "harvest-window.example.json").read_text())


def record(accepted: str, at: datetime, glean_id: str = "glean-fixture") -> WeightRecord:
    weight = Decimal(accepted)
    return WeightRecord(
        glean_id=glean_id,
        entry=WeightEntry(
            gross_weight_kg=weight,
            net_weight_kg=weight,
            accepted_weight_kg=weight,
            rejected_weight_kg=Decimal(0),
            source=WeightSource.COORDINATOR_MANUAL,
        ),
        entered_by="coordinator-fixture",
        entered_at=at,
        coordinator_verified=True,
        verified_by="coordinator-fixture",
        verified_at=at + timedelta(minutes=5),
    )


def evaluation_set(attested: bool = True) -> ReviewedEvaluationSet:
    return ReviewedEvaluationSet(manifest_sha256="d" * 64, review_attested=attested)


def add_example(
    service: ReviewedEvaluationSet,
    document: dict[str, Any],
    *,
    example_id: str,
    accepted: str = "8",
    actual: datetime = START,
    visible_count: int | None = 40,
) -> None:
    service.add(
        example_id=example_id,
        forecast_document=document,
        record=record(accepted, actual, f"glean-{example_id}"),
        actual_harvested_at=actual,
        visible_fruit_count=visible_count,
    )


def weighted(document: dict[str, Any]) -> dict[str, Any]:
    weighted_document = deepcopy(document)
    weighted_document["visible_yield"].update(
        estimated_weight_kg=9.0,
        weight_lower_kg=7.0,
        weight_upper_kg=11.0,
        estimation_method="cultivar_weight_model",
    )
    return weighted_document


def test_duplicate_examples_and_manifest_guards(forecast_document: dict[str, Any]) -> None:
    service = evaluation_set()
    first = service.add(
        example_id="one",
        forecast_document=forecast_document,
        record=record("8", START),
        actual_harvested_at=START,
        visible_fruit_count=40,
    )
    duplicate = service.add(
        example_id="one",
        forecast_document=forecast_document,
        record=record("8", START),
        actual_harvested_at=START,
        visible_fruit_count=40,
    )
    assert duplicate == first
    assert service.example_count == 1
    with pytest.raises(ValueError, match="replay_conflict"):
        service.add(
            example_id="one",
            forecast_document=forecast_document,
            record=record("8", START),
            actual_harvested_at=START,
            visible_fruit_count=41,
        )
    with pytest.raises(ValueError, match="Not enough"):
        service.aggregate()
    with pytest.raises(ValueError, match="SHA-256"):
        ReviewedEvaluationSet(manifest_sha256="bad", review_attested=True)
    with pytest.raises(ValueError, match="at least two"):
        ReviewedEvaluationSet(manifest_sha256="d" * 64, review_attested=True, minimum_examples=1)
    unattested = evaluation_set(attested=False)
    add_example(unattested, forecast_document, example_id="one")
    add_example(unattested, forecast_document, example_id="two", actual=END)
    with pytest.raises(ValueError, match="review attestation"):
        unattested.aggregate()
    unverified = record("8", START)
    unverified = WeightRecord(
        glean_id=unverified.glean_id,
        entry=unverified.entry,
        entered_by=unverified.entered_by,
        entered_at=unverified.entered_at,
    )
    with pytest.raises(ValueError, match="coordinator-verified"):
        service.add(
            example_id="unverified",
            forecast_document=forecast_document,
            record=unverified,
            actual_harvested_at=START,
            visible_fruit_count=40,
        )


def test_aggregate_computes_interval_and_visible_count_metrics(
    forecast_document: dict[str, Any],
) -> None:
    service = evaluation_set()
    add_example(service, forecast_document, example_id="start", actual=START, visible_count=40)
    add_example(
        service,
        forecast_document,
        example_id="after-end",
        actual=END + timedelta(days=1),
        visible_count=44,
    )
    aggregate = service.aggregate()
    assert aggregate.example_count == 2
    assert aggregate.start_mae_days == 5.0
    assert aggregate.end_mae_days == 5.0
    assert aggregate.interval_coverage == 0.5
    assert aggregate.mean_interval_width_days == 9.0
    assert aggregate.visible_count_mae == 2.0
    assert aggregate.weight_mae_kg is None
    assert aggregate.weight_mape is None
    assert aggregate.reason_codes == ("weight_metrics_unavailable",)
    assert aggregate.automatic_model_promotion is False
    assert aggregate.human_model_approval_required is True


def test_weight_mae_and_defined_mape_are_aggregated(forecast_document: dict[str, Any]) -> None:
    document = weighted(forecast_document)
    service = evaluation_set()
    add_example(service, document, example_id="eight", accepted="8", actual=START)
    add_example(service, document, example_id="ten", accepted="10", actual=END)
    aggregate = service.aggregate()
    assert aggregate.weight_example_count == 2
    assert aggregate.weight_mae_kg == Decimal(1)
    assert aggregate.weight_mape == Decimal("11.25")
    assert aggregate.reason_codes == ()


def test_zero_weight_percentage_is_excluded_without_being_invented(
    forecast_document: dict[str, Any],
) -> None:
    document = weighted(forecast_document)
    service = evaluation_set()
    add_example(service, document, example_id="zero", accepted="0", actual=START)
    add_example(service, document, example_id="eight", accepted="8", actual=END)
    aggregate = service.aggregate()
    assert aggregate.weight_mae_kg == Decimal(5)
    assert aggregate.weight_mape == Decimal("12.5")
    assert aggregate.reason_codes == ("weight_mape_partially_undefined",)


def test_mixed_models_require_separate_sets_and_export_has_no_identifiers(
    forecast_document: dict[str, Any],
) -> None:
    service = evaluation_set()
    add_example(service, forecast_document, example_id="private-example", actual=START)
    other = deepcopy(forecast_document)
    other["model"]["version"] = "1.0.1"
    add_example(service, other, example_id="other-private-example", actual=END)
    with pytest.raises(ValueError, match="Mixed model versions"):
        service.aggregate()

    single_model = evaluation_set()
    add_example(single_model, forecast_document, example_id="private-example", actual=START)
    add_example(single_model, forecast_document, example_id="second-private", actual=END)
    exported = single_model.aggregate().as_non_sensitive_dict()
    serialized = json.dumps(exported)
    assert "private-example" not in serialized
    assert "glean-" not in serialized
    assert forecast_document["window_id"] not in serialized
    assert forecast_document["tree_id"] not in serialized
    assert "recipient" not in serialized
    assert "location" not in serialized
    assert exported["automatic_model_promotion"] is False


def test_model_data_gate_never_promotes_automatically() -> None:
    eligible = evaluate_model_data_gate(
        tree_season_sequences=100,
        site_count=20,
        held_out_site_split=True,
        held_out_season_split=True,
    )
    assert eligible.eligible_for_non_experimental_model_review is True
    assert eligible.reason_codes == ()
    assert eligible.automatic_model_promotion is False
    assert eligible.human_model_approval_required is True
    insufficient = evaluate_model_data_gate(
        tree_season_sequences=99,
        site_count=19,
        held_out_site_split=False,
        held_out_season_split=False,
    )
    assert insufficient.eligible_for_non_experimental_model_review is False
    assert insufficient.reason_codes == (
        "insufficient_tree_season_sequences",
        "insufficient_sites",
        "held_out_site_split_required",
        "held_out_season_split_required",
    )
    with pytest.raises(ValueError, match="non-negative integers"):
        evaluate_model_data_gate(
            tree_season_sequences=-1,
            site_count=20,
            held_out_site_split=True,
            held_out_season_split=True,
        )
