from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .forecast import validate_harvest_window


class WeightSource(StrEnum):
    CALIBRATED_SCALE = "calibrated_scale"
    COORDINATOR_MANUAL = "coordinator_manual"


class RejectionReason(StrEnum):
    RECIPIENT_REJECTED = "recipient_rejected"
    SPOILED = "spoiled"
    DAMAGED = "damaged"
    OTHER_COORDINATOR_REVIEWED = "other_coordinator_reviewed"


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return value.astimezone(UTC)


def _weight(value: Decimal, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{field} must be a finite non-negative Decimal")
    return value


def _evidence_hash(value: str | None) -> None:
    if value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Evidence SHA-256 must be 64 lowercase hexadecimal characters")


@dataclass(frozen=True)
class WeightEntry:
    gross_weight_kg: Decimal
    net_weight_kg: Decimal
    accepted_weight_kg: Decimal
    rejected_weight_kg: Decimal
    source: WeightSource
    scale_reference: str | None = None
    rejection_reasons: tuple[RejectionReason, ...] = ()
    evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        gross = _weight(self.gross_weight_kg, "Gross weight")
        net = _weight(self.net_weight_kg, "Net weight")
        accepted = _weight(self.accepted_weight_kg, "Accepted weight")
        rejected = _weight(self.rejected_weight_kg, "Rejected weight")
        if net > gross:
            raise ValueError("Net weight cannot exceed gross weight")
        if accepted + rejected != net:
            raise ValueError("Accepted plus rejected weight must equal net weight")
        if rejected > 0 and not self.rejection_reasons:
            raise ValueError("Rejected weight requires a coarse rejection reason")
        if rejected == 0 and self.rejection_reasons:
            raise ValueError("Rejection reasons require rejected weight")
        if len(set(self.rejection_reasons)) != len(self.rejection_reasons):
            raise ValueError("Duplicate rejection reasons are invalid")
        if not isinstance(self.source, WeightSource):
            raise ValueError("Unknown weight source")
        if self.source is WeightSource.CALIBRATED_SCALE:
            if not self.scale_reference or not self.scale_reference.strip():
                raise ValueError("Calibrated-scale weights require a scale reference")
            if self.evidence_sha256 is None:
                raise ValueError("Calibrated-scale weights require an evidence hash")
        _evidence_hash(self.evidence_sha256)


@dataclass(frozen=True)
class CompletionEntry:
    recipient_id: str
    actual_harvested_at: datetime
    visible_fruit_count: int | None
    weight: WeightEntry

    def __post_init__(self) -> None:
        if not self.recipient_id.strip():
            raise ValueError("Recipient ID is required")
        _utc(self.actual_harvested_at, "Actual harvest timestamp")
        if self.visible_fruit_count is not None and (
            not isinstance(self.visible_fruit_count, int)
            or isinstance(self.visible_fruit_count, bool)
            or self.visible_fruit_count < 0
        ):
            raise ValueError("Visible fruit count must be non-negative")
        if not isinstance(self.weight, WeightEntry):
            raise ValueError("A validated weight entry is required")


@dataclass(frozen=True)
class PendingCompletion:
    entry: CompletionEntry
    entered_by: str
    entered_at: datetime

    def __post_init__(self) -> None:
        if not self.entered_by.strip():
            raise ValueError("Weight-entry actor is required")
        entered_at = _utc(self.entered_at, "Weight-entry timestamp")
        if _utc(self.entry.actual_harvested_at, "Actual harvest timestamp") > entered_at:
            raise ValueError("Harvest timestamp cannot be after its entry timestamp")


@dataclass(frozen=True)
class WeightRecord:
    glean_id: str
    entry: WeightEntry
    entered_by: str
    entered_at: datetime
    coordinator_verified: bool = False
    verified_by: str | None = None
    verified_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.glean_id.strip() or not self.entered_by.strip():
            raise ValueError("Glean and weight-entry actor IDs are required")
        _utc(self.entered_at, "Weight-entry timestamp")
        if self.coordinator_verified:
            if not self.verified_by or not self.verified_by.strip():
                raise ValueError("Coordinator verifier is required")
            if self.verified_at is None:
                raise ValueError("Coordinator verification timestamp is required")
            if _utc(self.verified_at, "Verification timestamp") < _utc(
                self.entered_at, "Weight-entry timestamp"
            ):
                raise ValueError("Verification cannot precede weight entry")
        elif self.verified_by is not None or self.verified_at is not None:
            raise ValueError("Unverified weights cannot have verification metadata")


@dataclass(frozen=True)
class DonationReceipt:
    receipt_number: str
    glean_id: str
    recipient_id: str
    accepted_weight_kg: Decimal
    donation_date: date

    def __post_init__(self) -> None:
        if not self.receipt_number.strip() or not self.glean_id.strip():
            raise ValueError("Receipt and glean IDs are required")
        if not self.recipient_id.strip():
            raise ValueError("Recipient ID is required")
        _weight(self.accepted_weight_kg, "Accepted weight")

    @classmethod
    def from_verified(
        cls,
        *,
        glean_id: str,
        recipient_id: str,
        actual_harvested_at: datetime,
        record: WeightRecord,
    ) -> DonationReceipt:
        if not record.coordinator_verified or record.glean_id != glean_id:
            raise ValueError("A coordinator-verified weight record is required")
        harvested = _utc(actual_harvested_at, "Actual harvest timestamp")
        accepted = record.entry.accepted_weight_kg
        digest = hashlib.sha256(
            f"{glean_id}|{recipient_id}|{harvested.isoformat()}|{accepted}".encode()
        ).hexdigest()
        return cls(
            receipt_number=f"HG-{digest[:20].upper()}",
            glean_id=glean_id,
            recipient_id=recipient_id,
            accepted_weight_kg=accepted,
            donation_date=harvested.date(),
        )

    def as_non_sensitive_dict(self) -> dict[str, str]:
        """No location, access note, media, volunteer, rejection, or scale evidence fields."""
        return {
            "receipt_number": self.receipt_number,
            "glean_id": self.glean_id,
            "recipient_id": self.recipient_id,
            "accepted_weight_kg": str(self.accepted_weight_kg),
            "donation_date": self.donation_date.isoformat(),
        }


@dataclass(frozen=True)
class ForecastOutcomeEvaluation:
    forecast_id: str
    forecast_sha256: str
    model_name: str
    model_version: str
    dataset_manifest_sha256: str
    actual_harvested_at: datetime
    start_error_days: float
    harvest_within_ready_interval: bool
    visible_count_absolute_error: int | None
    accepted_weight_absolute_error_kg: Decimal | None
    accepted_weight_absolute_percentage_error: Decimal | None
    reason_codes: tuple[str, ...]
    automatic_model_promotion: bool = False
    reviewed_dataset_manifest_required: bool = True


def forecast_sha256(document: dict[str, Any]) -> str:
    canonical = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def evaluate_forecast_outcome(
    document: dict[str, Any],
    *,
    record: WeightRecord,
    actual_harvested_at: datetime,
    visible_fruit_count: int | None,
) -> ForecastOutcomeEvaluation:
    issues = validate_harvest_window(document)
    if issues:
        raise ValueError(f"Invalid harvest-window forecast: {issues}")
    if not record.coordinator_verified:
        raise ValueError("Forecast evaluation requires coordinator-verified weights")
    harvested = _utc(actual_harvested_at, "Actual harvest timestamp")
    issued = datetime.fromisoformat(document["issued_at"].replace("Z", "+00:00"))
    if harvested < issued:
        raise ValueError("Actual harvest cannot precede forecast issuance")
    if visible_fruit_count is not None and (
        not isinstance(visible_fruit_count, int)
        or isinstance(visible_fruit_count, bool)
        or visible_fruit_count < 0
    ):
        raise ValueError("Visible fruit count must be non-negative")

    ready_start = datetime.fromisoformat(
        document["ready_interval"]["start_at"].replace("Z", "+00:00")
    )
    ready_end = datetime.fromisoformat(document["ready_interval"]["end_at"].replace("Z", "+00:00"))
    predicted_count = document["visible_yield"]["visible_fruit_count"]
    count_error = (
        abs(predicted_count - visible_fruit_count) if visible_fruit_count is not None else None
    )
    predicted_weight = document["visible_yield"]["estimated_weight_kg"]
    accepted = record.entry.accepted_weight_kg
    weight_error = (
        abs(Decimal(str(predicted_weight)) - accepted) if predicted_weight is not None else None
    )
    percentage_error = (
        weight_error / accepted * Decimal(100)
        if weight_error is not None and accepted > 0
        else None
    )
    reasons: list[str] = []
    if count_error is None:
        reasons.append("actual_visible_count_unavailable")
    if weight_error is None:
        reasons.append("forecast_weight_unavailable")
    if weight_error is not None and accepted == 0:
        reasons.append("accepted_weight_zero_percentage_undefined")
    return ForecastOutcomeEvaluation(
        forecast_id=document["window_id"],
        forecast_sha256=forecast_sha256(document),
        model_name=document["model"]["name"],
        model_version=document["model"]["version"],
        dataset_manifest_sha256=document["model"]["dataset_manifest_sha256"],
        actual_harvested_at=harvested,
        start_error_days=(harvested - ready_start).total_seconds() / 86_400,
        harvest_within_ready_interval=ready_start <= harvested <= ready_end,
        visible_count_absolute_error=count_error,
        accepted_weight_absolute_error_kg=weight_error,
        accepted_weight_absolute_percentage_error=percentage_error,
        reason_codes=tuple(reasons),
    )
