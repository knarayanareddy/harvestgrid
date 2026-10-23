from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from .outcomes import WeightRecord, evaluate_forecast_outcome


@dataclass(frozen=True)
class EvaluationExample:
    example_id: str
    forecast_sha256: str
    model_name: str
    model_version: str
    start_error_days: float
    end_error_days: float
    interval_width_days: float
    harvest_within_ready_interval: bool
    visible_count_absolute_error: int | None
    accepted_weight_absolute_error_kg: Decimal | None
    accepted_weight_absolute_percentage_error: Decimal | None


@dataclass(frozen=True)
class EvaluationAggregate:
    example_count: int
    start_mae_days: float
    end_mae_days: float
    interval_coverage: float
    mean_interval_width_days: float
    visible_count_example_count: int
    visible_count_mae: float | None
    weight_example_count: int
    weight_mae_kg: Decimal | None
    weight_mape: Decimal | None
    reason_codes: tuple[str, ...]
    manifest_review_attested: bool
    automatic_model_promotion: bool = False
    human_model_approval_required: bool = True

    def as_non_sensitive_dict(self) -> dict[str, Any]:
        """Aggregate metrics only: no forecast, tree, site, recipient, or location identifiers."""
        return {
            "example_count": self.example_count,
            "start_mae_days": self.start_mae_days,
            "end_mae_days": self.end_mae_days,
            "interval_coverage": self.interval_coverage,
            "mean_interval_width_days": self.mean_interval_width_days,
            "visible_count_example_count": self.visible_count_example_count,
            "visible_count_mae": self.visible_count_mae,
            "weight_example_count": self.weight_example_count,
            "weight_mae_kg": str(self.weight_mae_kg) if self.weight_mae_kg is not None else None,
            "weight_mape": str(self.weight_mape) if self.weight_mape is not None else None,
            "reason_codes": list(self.reason_codes),
            "manifest_review_attested": self.manifest_review_attested,
            "automatic_model_promotion": self.automatic_model_promotion,
            "human_model_approval_required": self.human_model_approval_required,
        }


@dataclass(frozen=True)
class ModelDataGateDecision:
    eligible_for_non_experimental_model_review: bool
    reason_codes: tuple[str, ...]
    automatic_model_promotion: bool = False
    human_model_approval_required: bool = True


def evaluate_model_data_gate(
    *,
    tree_season_sequences: int,
    site_count: int,
    held_out_site_split: bool,
    held_out_season_split: bool,
) -> ModelDataGateDecision:
    values = (tree_season_sequences, site_count)
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
        raise ValueError("Data-gate counts must be non-negative integers")
    reasons: list[str] = []
    if tree_season_sequences < 100:
        reasons.append("insufficient_tree_season_sequences")
    if site_count < 20:
        reasons.append("insufficient_sites")
    if not held_out_site_split:
        reasons.append("held_out_site_split_required")
    if not held_out_season_split:
        reasons.append("held_out_season_split_required")
    return ModelDataGateDecision(
        eligible_for_non_experimental_model_review=not reasons,
        reason_codes=tuple(reasons),
    )


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _mean_float(values: list[float]) -> float:
    return sum(values) / len(values)


def _mean_decimal(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


class ReviewedEvaluationSet:
    """In-memory coordinator-verified examples; an attestation is not a real human review."""

    def __init__(
        self,
        *,
        manifest_sha256: str,
        review_attested: bool,
        minimum_examples: int = 2,
    ) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None:
            raise ValueError(
                "Evaluation manifest SHA-256 must be 64 lowercase hexadecimal characters"
            )
        if not isinstance(minimum_examples, int) or isinstance(minimum_examples, bool):
            raise ValueError("Minimum example count must be an integer")
        if minimum_examples < 2:
            raise ValueError("An aggregate requires at least two examples")
        self._manifest_sha256 = manifest_sha256
        self._review_attested = review_attested
        self._minimum_examples = minimum_examples
        self._examples: dict[str, tuple[EvaluationExample, WeightRecord]] = {}

    @property
    def example_count(self) -> int:
        return len(self._examples)

    def add(
        self,
        *,
        example_id: str,
        forecast_document: dict[str, Any],
        record: WeightRecord,
        actual_harvested_at: datetime,
        visible_fruit_count: int | None,
    ) -> EvaluationExample:
        if not example_id.strip():
            raise ValueError("Evaluation example ID is required")
        evaluation = evaluate_forecast_outcome(
            forecast_document,
            record=record,
            actual_harvested_at=actual_harvested_at,
            visible_fruit_count=visible_fruit_count,
        )
        ready_start = _time(forecast_document["ready_interval"]["start_at"])
        ready_end = _time(forecast_document["ready_interval"]["end_at"])
        example = EvaluationExample(
            example_id=example_id,
            forecast_sha256=evaluation.forecast_sha256,
            model_name=evaluation.model_name,
            model_version=evaluation.model_version,
            start_error_days=evaluation.start_error_days,
            end_error_days=(evaluation.actual_harvested_at - ready_end).total_seconds() / 86_400,
            interval_width_days=(ready_end - ready_start).total_seconds() / 86_400,
            harvest_within_ready_interval=evaluation.harvest_within_ready_interval,
            visible_count_absolute_error=evaluation.visible_count_absolute_error,
            accepted_weight_absolute_error_kg=evaluation.accepted_weight_absolute_error_kg,
            accepted_weight_absolute_percentage_error=(
                evaluation.accepted_weight_absolute_percentage_error
            ),
        )
        prior = self._examples.get(example_id)
        if prior is not None:
            if prior != (example, record):
                raise ValueError("evaluation_example_replay_conflict")
            return prior[0]
        self._examples[example_id] = (example, record)
        return example

    def aggregate(self) -> EvaluationAggregate:
        if not self._review_attested:
            raise ValueError("Evaluation manifest review attestation is required")
        if len(self._examples) < self._minimum_examples:
            raise ValueError("Not enough coordinator-verified examples for an aggregate")
        examples = [example for example, _record in self._examples.values()]
        model_versions = {(example.model_name, example.model_version) for example in examples}
        if len(model_versions) != 1:
            raise ValueError("Mixed model versions require separate evaluation sets")

        visible = [
            float(example.visible_count_absolute_error)
            for example in examples
            if example.visible_count_absolute_error is not None
        ]
        weight = [
            example.accepted_weight_absolute_error_kg
            for example in examples
            if example.accepted_weight_absolute_error_kg is not None
        ]
        percentage = [
            example.accepted_weight_absolute_percentage_error
            for example in examples
            if example.accepted_weight_absolute_percentage_error is not None
        ]
        reasons: list[str] = []
        if len(visible) != len(examples):
            reasons.append("visible_count_metrics_incomplete")
        if not weight:
            reasons.append("weight_metrics_unavailable")
        elif len(percentage) != len(weight):
            reasons.append("weight_mape_partially_undefined")
        return EvaluationAggregate(
            example_count=len(examples),
            start_mae_days=_mean_float([abs(example.start_error_days) for example in examples]),
            end_mae_days=_mean_float([abs(example.end_error_days) for example in examples]),
            interval_coverage=_mean_float(
                [1.0 if example.harvest_within_ready_interval else 0.0 for example in examples]
            ),
            mean_interval_width_days=_mean_float(
                [example.interval_width_days for example in examples]
            ),
            visible_count_example_count=len(visible),
            visible_count_mae=_mean_float(visible) if visible else None,
            weight_example_count=len(weight),
            weight_mae_kg=_mean_decimal(weight) if weight else None,
            weight_mape=_mean_decimal(percentage) if percentage else None,
            reason_codes=tuple(reasons),
            manifest_review_attested=self._review_attested,
        )
