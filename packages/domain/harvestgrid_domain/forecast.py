from __future__ import annotations

from datetime import datetime
from typing import Any


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _ordered(start: str, end: str) -> bool:
    return _time(start) < _time(end)


def validate_harvest_window(document: dict[str, Any]) -> list[str]:
    issues: set[str] = set()
    ready = document["ready_interval"]
    prediction = document["prediction_interval"]
    visible = document["visible_yield"]
    capability = document["visual_capability"]

    if not _ordered(document["issued_at"], document["valid_until"]):
        issues.add("forecast_validity_interval_invalid")
    if not _ordered(ready["start_at"], ready["end_at"]):
        issues.add("ready_interval_invalid")
    if (
        not _time(prediction["start_lower_at"])
        <= _time(ready["start_at"])
        <= _time(prediction["start_upper_at"])
    ):
        issues.add("ready_start_outside_prediction_interval")
    if (
        not _time(prediction["end_lower_at"])
        <= _time(ready["end_at"])
        <= _time(prediction["end_upper_at"])
    ):
        issues.add("ready_end_outside_prediction_interval")
    if not visible["count_lower"] <= visible["visible_fruit_count"] <= visible["count_upper"]:
        issues.add("visible_count_outside_interval")

    weights = (
        visible["estimated_weight_kg"],
        visible["weight_lower_kg"],
        visible["weight_upper_kg"],
    )
    if any(weight is None for weight in weights) and any(weight is not None for weight in weights):
        issues.add("weight_estimate_fields_must_be_all_present_or_all_unknown")
    if all(weight is not None for weight in weights):
        estimate, lower, upper = weights
        if not lower <= estimate <= upper:
            issues.add("weight_estimate_outside_interval")
    if visible["estimation_method"] == "visible_count_only" and any(
        weight is not None for weight in weights
    ):
        issues.add("visible_count_only_cannot_estimate_weight")

    if capability["internal_ripeness_inferred"] or capability["food_safety_inferred"]:
        issues.add("unsupported_visual_capability_claim")
    if not capability["human_readiness_review_required"]:
        issues.add("human_readiness_review_must_remain_required")
    if document["model"]["kind"] == "seasonal_baseline" and document["status"] != "experimental":
        issues.add("seasonal_baseline_must_be_experimental")
    if document["status"] == "coordinator_confirmed":
        review = document.get("human_review")
        if not review or not review["readiness_confirmed"]:
            issues.add("coordinator_confirmation_requires_readiness_review")
    return sorted(issues)
