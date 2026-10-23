import json
from copy import deepcopy
from typing import Any

import pytest
from conftest import load_json
from harvestgrid_domain import validate_harvest_window, walking_skeleton


def test_canonical_example_has_valid_semantics(forecast_document: dict[str, Any]) -> None:
    assert validate_harvest_window(forecast_document) == []


def test_invalid_time_and_count_intervals_are_rejected(
    forecast_document: dict[str, Any],
) -> None:
    invalid = deepcopy(forecast_document)
    invalid["valid_until"] = invalid["issued_at"]
    invalid["ready_interval"]["start_at"] = invalid["ready_interval"]["end_at"]
    invalid["prediction_interval"]["start_upper_at"] = "2026-10-30T08:00:00.000Z"
    invalid["prediction_interval"]["end_upper_at"] = "2026-11-08T08:00:00.000Z"
    invalid["visible_yield"]["count_upper"] = 40
    assert validate_harvest_window(invalid) == [
        "forecast_validity_interval_invalid",
        "ready_end_outside_prediction_interval",
        "ready_interval_invalid",
        "ready_start_outside_prediction_interval",
        "visible_count_outside_interval",
    ]


def test_weight_estimates_must_be_complete_ordered_and_supported(
    forecast_document: dict[str, Any],
) -> None:
    partial = deepcopy(forecast_document)
    partial["visible_yield"]["estimated_weight_kg"] = 2.0
    assert "weight_estimate_fields_must_be_all_present_or_all_unknown" in validate_harvest_window(
        partial
    )

    invalid = deepcopy(forecast_document)
    invalid["visible_yield"].update(
        {
            "estimated_weight_kg": 5.0,
            "weight_lower_kg": 6.0,
            "weight_upper_kg": 4.0,
            "estimation_method": "cultivar_weight_model",
        }
    )
    assert "weight_estimate_outside_interval" in validate_harvest_window(invalid)

    unsupported = deepcopy(invalid)
    unsupported["visible_yield"]["estimation_method"] = "visible_count_only"
    assert "visible_count_only_cannot_estimate_weight" in validate_harvest_window(unsupported)


def test_capability_and_review_claims_fail_closed(forecast_document: dict[str, Any]) -> None:
    invalid = deepcopy(forecast_document)
    invalid["visual_capability"]["internal_ripeness_inferred"] = True
    invalid["visual_capability"]["human_readiness_review_required"] = False
    invalid["status"] = "owner_review_required"
    assert validate_harvest_window(invalid) == [
        "human_readiness_review_must_remain_required",
        "seasonal_baseline_must_be_experimental",
        "unsupported_visual_capability_claim",
    ]

    confirmed = deepcopy(forecast_document)
    confirmed["model"]["kind"] = "quantile_lightgbm"
    confirmed["status"] = "coordinator_confirmed"
    assert "coordinator_confirmation_requires_readiness_review" in validate_harvest_window(
        confirmed
    )
    confirmed["human_review"] = {
        "reviewer_id": "01000000000000000000000009",
        "reviewed_at": "2026-10-23T12:00:00.000Z",
        "readiness_confirmed": True,
        "notes": "Fixture review shape only.",
    }
    assert validate_harvest_window(confirmed) == []


def test_walking_skeleton_is_validation_only(capsys: Any) -> None:
    walking_skeleton.main()
    output = json.loads(capsys.readouterr().out)
    assert "FIXTURE ONLY" in output["mode"]
    assert output["forecast_status"] == "experimental"
    assert output["human_readiness_review_required"] is True
    assert output["internal_ripeness_inferred"] is False


def test_walking_skeleton_rejects_invalid_input(monkeypatch: Any) -> None:
    monkeypatch.setattr(walking_skeleton, "validate_harvest_window", lambda _document: ["bad"])
    with pytest.raises(ValueError, match="Invalid harvest-window fixture"):
        walking_skeleton.main()


def test_cultivar_content_does_not_invent_an_operational_prior() -> None:
    content = load_json("content/cultivars/european-plum-v1.json")
    assert content["seasonal_priors"] == []
    assert content["review_status"] == "draft_no_operational_prior_approved"
