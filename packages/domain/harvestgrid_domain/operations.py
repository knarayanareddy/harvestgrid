from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from harvestgrid_domain_matching import Candidate, CandidateDecision, rank_eligible

from .outcomes import (
    CompletionEntry,
    DonationReceipt,
    ForecastOutcomeEvaluation,
    PendingCompletion,
    WeightRecord,
    WeightSource,
    evaluate_forecast_outcome,
    forecast_sha256,
)


class GleanState(StrEnum):
    OWNER_REVIEW_REQUIRED = "owner_review_required"
    COORDINATOR_REVIEW_REQUIRED = "coordinator_review_required"
    MATCHING = "matching"
    COORDINATOR_ASSIGNMENT_APPROVAL = "coordinator_assignment_approval"
    INVITED = "invited"
    CONFIRMED = "confirmed"
    COMPLETION_REVIEW = "completion_review"
    WEIGHED = "weighed"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class AssignmentState(StrEnum):
    INVITED = "invited"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class VolunteerAssignment:
    volunteer_id: str
    state: AssignmentState
    reveal_not_before: datetime
    reveal_until: datetime


@dataclass(frozen=True)
class RestrictedLocation:
    exact_address: str
    access_notes: str


@dataclass(frozen=True)
class GleanAuditEvent:
    version: int
    actor_id: str
    action: str
    at: datetime
    reason: str = ""


@dataclass(frozen=True)
class GleanSnapshot:
    glean_id: str
    forecast_id: str
    owner_id: str
    coordinator_id: str
    state: GleanState = GleanState.OWNER_REVIEW_REQUIRED
    version: int = 0
    owner_approval_current: bool = False
    coordinator_readiness_confirmed: bool = False
    proposed_assignments: tuple[CandidateDecision, ...] = ()
    assignments: tuple[VolunteerAssignment, ...] = ()
    cancellation_reason: str | None = None
    audit: tuple[GleanAuditEvent, ...] = ()
    completion: PendingCompletion | None = None
    weight_record: WeightRecord | None = None
    donation_receipt: DonationReceipt | None = None
    forecast_evaluation: ForecastOutcomeEvaluation | None = None


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return value.astimezone(UTC)


class GleanWorkflow:
    """In-memory coordinator workflow; notification, storage, and deletion adapters are separate."""

    def __init__(self, snapshot: GleanSnapshot) -> None:
        identifiers = (
            snapshot.glean_id,
            snapshot.forecast_id,
            snapshot.owner_id,
            snapshot.coordinator_id,
        )
        if any(not identifier.strip() for identifier in identifiers):
            raise ValueError("Glean, forecast, owner, and coordinator IDs are required")
        if snapshot.version < 0 or snapshot.version != len(snapshot.audit):
            raise ValueError("Snapshot version must equal its audit length")
        outcome_values = (
            snapshot.completion,
            snapshot.weight_record,
            snapshot.donation_receipt,
            snapshot.forecast_evaluation,
        )
        if snapshot.state is GleanState.COMPLETION_REVIEW:
            if snapshot.completion is None or any(
                value is not None for value in outcome_values[1:]
            ):
                raise ValueError("Completion review requires only a pending completion")
        elif snapshot.state in {GleanState.WEIGHED, GleanState.CLOSED}:
            if any(value is None for value in outcome_values):
                raise ValueError("Verified outcome snapshots are incomplete")
            if snapshot.weight_record is None or not snapshot.weight_record.coordinator_verified:
                raise ValueError("Verified outcome requires coordinator-verified weights")
        elif any(value is not None for value in outcome_values):
            raise ValueError("Outcome records are not allowed from the current state")

        completion = snapshot.completion
        record = snapshot.weight_record
        receipt = snapshot.donation_receipt
        evaluation = snapshot.forecast_evaluation
        if record is not None and record.glean_id != snapshot.glean_id:
            raise ValueError("Weight-record glean does not match")
        if receipt is not None and receipt.glean_id != snapshot.glean_id:
            raise ValueError("Receipt glean does not match")
        if evaluation is not None and evaluation.forecast_id != snapshot.forecast_id:
            raise ValueError("Evaluation forecast does not match")
        if completion is not None and record is not None:
            if (
                record.entry != completion.entry.weight
                or record.entered_by != completion.entered_by
                or record.entered_at != completion.entered_at
            ):
                raise ValueError("Verified weights do not match the pending completion")
        if completion is not None and receipt is not None:
            harvested = _aware_utc(
                completion.entry.actual_harvested_at,
                "Actual harvest timestamp",
            )
            if (
                receipt.recipient_id != completion.entry.recipient_id
                or receipt.donation_date != harvested.date()
            ):
                raise ValueError("Receipt does not match the pending completion")
        if completion is not None and evaluation is not None:
            harvested = _aware_utc(
                completion.entry.actual_harvested_at,
                "Actual harvest timestamp",
            )
            if evaluation.actual_harvested_at != harvested:
                raise ValueError("Evaluation harvest timestamp does not match")
        if record is not None and receipt is not None:
            if receipt.accepted_weight_kg != record.entry.accepted_weight_kg:
                raise ValueError("Receipt accepted weight does not match")
        self._snapshot = snapshot

    @classmethod
    def new(
        cls, *, glean_id: str, forecast_id: str, owner_id: str, coordinator_id: str
    ) -> GleanWorkflow:
        return cls(
            GleanSnapshot(
                glean_id=glean_id,
                forecast_id=forecast_id,
                owner_id=owner_id,
                coordinator_id=coordinator_id,
            )
        )

    @property
    def snapshot(self) -> GleanSnapshot:
        return self._snapshot

    def _require_version(self, expected_version: int) -> None:
        if expected_version != self._snapshot.version:
            raise ValueError("stale_glean_version")

    def _require_active(self) -> None:
        if self._snapshot.state is GleanState.CANCELLED:
            raise ValueError("cancelled_glean_is_immutable")

    def _require_actor(self, actor_id: str, expected: str) -> None:
        if actor_id != expected:
            raise PermissionError("actor_is_not_authorized")

    def _record(
        self,
        updated: GleanSnapshot,
        *,
        actor_id: str,
        action: str,
        at: datetime,
        reason: str = "",
    ) -> GleanSnapshot:
        timestamp = _aware_utc(at, "Transition timestamp")
        version = self._snapshot.version + 1
        event = GleanAuditEvent(version, actor_id, action, timestamp, reason)
        self._snapshot = replace(
            updated,
            version=version,
            audit=self._snapshot.audit + (event,),
        )
        return self._snapshot

    def request_glean(self, *, actor_id: str, expected_version: int, at: datetime) -> GleanSnapshot:
        self._require_actor(actor_id, self._snapshot.owner_id)
        self._require_active()
        if self._snapshot.owner_approval_current:
            return self._snapshot
        self._require_version(expected_version)
        if self._snapshot.state is not GleanState.OWNER_REVIEW_REQUIRED:
            raise ValueError("owner_approval_not_allowed_from_current_state")
        return self._record(
            replace(
                self._snapshot,
                state=GleanState.COORDINATOR_REVIEW_REQUIRED,
                owner_approval_current=True,
            ),
            actor_id=actor_id,
            action="owner_glean_approved",
            at=at,
        )

    def confirm_readiness(
        self, *, actor_id: str, expected_version: int, at: datetime
    ) -> GleanSnapshot:
        self._require_actor(actor_id, self._snapshot.coordinator_id)
        self._require_active()
        if self._snapshot.coordinator_readiness_confirmed:
            return self._snapshot
        self._require_version(expected_version)
        if (
            self._snapshot.state is not GleanState.COORDINATOR_REVIEW_REQUIRED
            or not self._snapshot.owner_approval_current
        ):
            raise ValueError("current_owner_approval_is_required_before_readiness")
        return self._record(
            replace(
                self._snapshot,
                state=GleanState.MATCHING,
                coordinator_readiness_confirmed=True,
            ),
            actor_id=actor_id,
            action="coordinator_readiness_confirmed",
            at=at,
        )

    def propose_assignments(
        self,
        *,
        actor_id: str,
        candidates: list[Candidate],
        required_count: int,
        expected_version: int,
        at: datetime,
    ) -> GleanSnapshot:
        self._require_actor(actor_id, self._snapshot.coordinator_id)
        self._require_active()
        self._require_version(expected_version)
        if self._snapshot.state is not GleanState.MATCHING:
            raise ValueError("matching_is_not_allowed_from_current_state")
        if not self._snapshot.owner_approval_current:
            raise PermissionError("current_owner_approval_is_required")
        if required_count < 1:
            raise ValueError("At least one eligible volunteer is required")
        eligible = rank_eligible(self._snapshot.glean_id, candidates)
        if len(eligible) < required_count:
            raise ValueError("Not enough eligible volunteers; no assignment was proposed")
        proposed = tuple(eligible[:required_count])
        return self._record(
            replace(
                self._snapshot,
                state=GleanState.COORDINATOR_ASSIGNMENT_APPROVAL,
                proposed_assignments=proposed,
            ),
            actor_id=actor_id,
            action="assignments_proposed",
            at=at,
        )

    def approve_assignments(
        self,
        *,
        actor_id: str,
        reveal_not_before: datetime,
        reveal_until: datetime,
        expected_version: int,
        at: datetime,
    ) -> GleanSnapshot:
        self._require_actor(actor_id, self._snapshot.coordinator_id)
        self._require_active()
        start = _aware_utc(reveal_not_before, "Reveal start")
        end = _aware_utc(reveal_until, "Reveal expiry")
        if start >= end:
            raise ValueError("Location reveal interval must be ordered")
        if self._snapshot.state in {GleanState.INVITED, GleanState.CONFIRMED}:
            existing = self._snapshot.assignments
            if all(
                assignment.reveal_not_before == start and assignment.reveal_until == end
                for assignment in existing
            ):
                return self._snapshot
            raise ValueError("Approved assignment reveal windows cannot be changed")
        self._require_version(expected_version)
        if self._snapshot.state is not GleanState.COORDINATOR_ASSIGNMENT_APPROVAL:
            raise ValueError("coordinator_assignment_approval_not_allowed_from_current_state")
        if not self._snapshot.owner_approval_current or not self._snapshot.proposed_assignments:
            raise PermissionError("Current owner approval and proposed assignments are required")
        assignments = tuple(
            VolunteerAssignment(decision.volunteer_id, AssignmentState.INVITED, start, end)
            for decision in self._snapshot.proposed_assignments
        )
        return self._record(
            replace(
                self._snapshot,
                state=GleanState.INVITED,
                assignments=assignments,
            ),
            actor_id=actor_id,
            action="coordinator_assignments_approved_and_invitation_recorded",
            at=at,
        )

    def confirm_assignment(
        self, *, actor_id: str, expected_version: int, at: datetime
    ) -> GleanSnapshot:
        self._require_active()
        assignment = next(
            (item for item in self._snapshot.assignments if item.volunteer_id == actor_id),
            None,
        )
        if assignment is None:
            raise PermissionError("volunteer_is_not_assigned")
        if assignment.state is AssignmentState.CONFIRMED:
            return self._snapshot
        self._require_version(expected_version)
        if self._snapshot.state not in {GleanState.INVITED, GleanState.CONFIRMED}:
            raise ValueError("assignment_confirmation_not_allowed_from_current_state")
        if not self._snapshot.owner_approval_current:
            raise PermissionError("current_owner_approval_is_required")
        assignments = tuple(
            replace(item, state=AssignmentState.CONFIRMED)
            if item.volunteer_id == actor_id
            else item
            for item in self._snapshot.assignments
        )
        return self._record(
            replace(
                self._snapshot,
                state=GleanState.CONFIRMED,
                assignments=assignments,
            ),
            actor_id=actor_id,
            action="volunteer_assignment_confirmed",
            at=at,
        )

    def record_completion(
        self,
        *,
        actor_id: str,
        entry: CompletionEntry,
        expected_version: int,
        at: datetime,
    ) -> GleanSnapshot:
        assigned = any(
            assignment.volunteer_id == actor_id and assignment.state is AssignmentState.CONFIRMED
            for assignment in self._snapshot.assignments
        )
        if actor_id != self._snapshot.coordinator_id and not assigned:
            raise PermissionError("completion_requires_a_coordinator_or_confirmed_volunteer")
        existing = self._snapshot.completion
        if existing is not None and existing.entered_by == actor_id and existing.entry == entry:
            return self._snapshot
        self._require_active()
        self._require_version(expected_version)
        if self._snapshot.state is not GleanState.CONFIRMED:
            raise ValueError("completion_is_not_allowed_from_current_state")
        if not self._snapshot.owner_approval_current or not any(
            assignment.state is AssignmentState.CONFIRMED
            for assignment in self._snapshot.assignments
        ):
            raise PermissionError("Current approvals and a confirmed volunteer are required")
        if (
            entry.weight.source is WeightSource.COORDINATOR_MANUAL
            and actor_id != self._snapshot.coordinator_id
        ):
            raise PermissionError("Coordinator-manual weights require the coordinator")
        pending = PendingCompletion(entry=entry, entered_by=actor_id, entered_at=at)
        return self._record(
            replace(
                self._snapshot,
                state=GleanState.COMPLETION_REVIEW,
                completion=pending,
            ),
            actor_id=actor_id,
            action="completion_and_unverified_weights_recorded",
            at=at,
        )

    def verify_completion(
        self,
        *,
        actor_id: str,
        forecast_document: dict[str, Any],
        expected_version: int,
        at: datetime,
    ) -> GleanSnapshot:
        self._require_actor(actor_id, self._snapshot.coordinator_id)
        if self._snapshot.state in {GleanState.WEIGHED, GleanState.CLOSED}:
            evaluation = self._snapshot.forecast_evaluation
            if evaluation is not None and evaluation.forecast_sha256 == forecast_sha256(
                forecast_document
            ):
                return self._snapshot
            raise ValueError("Verified forecast evaluation is immutable")
        self._require_active()
        self._require_version(expected_version)
        if self._snapshot.state is not GleanState.COMPLETION_REVIEW:
            raise ValueError("verification_is_not_allowed_from_current_state")
        pending = self._snapshot.completion
        if pending is None:
            raise ValueError("Pending completion is required")
        timestamp = _aware_utc(at, "Verification timestamp")
        record = WeightRecord(
            glean_id=self._snapshot.glean_id,
            entry=pending.entry.weight,
            entered_by=pending.entered_by,
            entered_at=pending.entered_at,
            coordinator_verified=True,
            verified_by=actor_id,
            verified_at=timestamp,
        )
        evaluation = evaluate_forecast_outcome(
            forecast_document,
            record=record,
            actual_harvested_at=pending.entry.actual_harvested_at,
            visible_fruit_count=pending.entry.visible_fruit_count,
        )
        if evaluation.forecast_id != self._snapshot.forecast_id:
            raise ValueError("Forecast does not match the glean")
        receipt = DonationReceipt.from_verified(
            glean_id=self._snapshot.glean_id,
            recipient_id=pending.entry.recipient_id,
            actual_harvested_at=pending.entry.actual_harvested_at,
            record=record,
        )
        return self._record(
            replace(
                self._snapshot,
                state=GleanState.WEIGHED,
                weight_record=record,
                donation_receipt=receipt,
                forecast_evaluation=evaluation,
            ),
            actor_id=actor_id,
            action="coordinator_weights_verified_receipt_and_evaluation_recorded",
            at=timestamp,
        )

    def close_outcome(self, *, actor_id: str, expected_version: int, at: datetime) -> GleanSnapshot:
        self._require_actor(actor_id, self._snapshot.coordinator_id)
        if self._snapshot.state is GleanState.CLOSED:
            return self._snapshot
        self._require_active()
        self._require_version(expected_version)
        if self._snapshot.state is not GleanState.WEIGHED:
            raise ValueError("A verified weighed outcome is required before closure")
        return self._record(
            replace(self._snapshot, state=GleanState.CLOSED),
            actor_id=actor_id,
            action="coordinator_outcome_closed",
            at=at,
        )

    def reveal_location(
        self, *, actor_id: str, location: RestrictedLocation, at: datetime
    ) -> RestrictedLocation:
        timestamp = _aware_utc(at, "Location access timestamp")
        if (
            self._snapshot.state is not GleanState.CONFIRMED
            or not self._snapshot.owner_approval_current
        ):
            raise PermissionError("location_is_not_available_from_current_state")
        assignment = next(
            (
                item
                for item in self._snapshot.assignments
                if item.volunteer_id == actor_id and item.state is AssignmentState.CONFIRMED
            ),
            None,
        )
        if assignment is None:
            raise PermissionError("location_requires_an_assigned_confirmed_volunteer")
        if not assignment.reveal_not_before <= timestamp < assignment.reveal_until:
            raise PermissionError("location_reveal_window_is_inactive")
        if not location.exact_address.strip():
            raise ValueError("Restricted location is unavailable")
        return location

    def cancel(
        self,
        *,
        actor_id: str,
        reason: Literal["owner_consent_revoked", "coordinator_cancelled"],
        expected_version: int,
        at: datetime,
    ) -> GleanSnapshot:
        if reason not in {"owner_consent_revoked", "coordinator_cancelled"}:
            raise ValueError("Unknown cancellation reason")
        if actor_id not in {self._snapshot.owner_id, self._snapshot.coordinator_id}:
            raise PermissionError("actor_is_not_authorized")
        if reason == "owner_consent_revoked":
            self._require_actor(actor_id, self._snapshot.owner_id)
        else:
            self._require_actor(actor_id, self._snapshot.coordinator_id)
        if self._snapshot.state is GleanState.CANCELLED:
            if self._snapshot.cancellation_reason == reason:
                return self._snapshot
            raise ValueError("Cancellation reason is already committed")
        if self._snapshot.state in {
            GleanState.COMPLETION_REVIEW,
            GleanState.WEIGHED,
            GleanState.CLOSED,
        }:
            raise ValueError("A recorded harvest outcome cannot be cancelled")
        self._require_version(expected_version)
        assignments = tuple(
            replace(assignment, state=AssignmentState.CANCELLED)
            for assignment in self._snapshot.assignments
        )
        return self._record(
            replace(
                self._snapshot,
                state=GleanState.CANCELLED,
                owner_approval_current=(
                    False
                    if reason == "owner_consent_revoked"
                    else self._snapshot.owner_approval_current
                ),
                assignments=assignments,
                cancellation_reason=reason,
            ),
            actor_id=actor_id,
            action=reason,
            at=at,
            reason=reason,
        )
