from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class HardConstraints:
    owner_approval_current: bool
    coordinator_approval_current: bool
    trained_leader_available: bool
    volunteer_training_valid: bool
    equipment_satisfied: bool
    availability_intersects: bool
    recipient_capacity_sufficient: bool
    coordinator_no_recipient_plan_approved: bool
    unresolved_safety_blockers: int
    address_reveal_policy_satisfied: bool
    insurance_waiver_satisfied: bool


@dataclass(frozen=True)
class RankingFeatures:
    travel_fit: float
    schedule_fit: float
    recipient_logistics_fit: float
    equipment_fit: float
    fairness_rotation: float


@dataclass(frozen=True)
class Candidate:
    volunteer_id: str
    constraints: HardConstraints
    ranking: RankingFeatures


@dataclass(frozen=True)
class CandidateDecision:
    volunteer_id: str
    eligible: bool
    reason_codes: tuple[str, ...]
    score: float | None
    tie_breaker: str | None


def hard_filter_reasons(constraints: HardConstraints) -> tuple[str, ...]:
    checks = {
        "owner_approval_required": constraints.owner_approval_current,
        "coordinator_approval_required": constraints.coordinator_approval_current,
        "trained_event_leader_required": constraints.trained_leader_available,
        "volunteer_training_required": constraints.volunteer_training_valid,
        "required_equipment_missing": constraints.equipment_satisfied,
        "availability_does_not_intersect": constraints.availability_intersects,
        "address_reveal_policy_unsatisfied": constraints.address_reveal_policy_satisfied,
        "insurance_or_waiver_unsatisfied": constraints.insurance_waiver_satisfied,
    }
    reasons = [code for code, passed in checks.items() if not passed]
    if (
        not constraints.recipient_capacity_sufficient
        and not constraints.coordinator_no_recipient_plan_approved
    ):
        reasons.append("recipient_capacity_or_approved_alternative_required")
    if constraints.unresolved_safety_blockers > 0:
        reasons.append("unresolved_safety_blocker")
    return tuple(reasons)


def ranking_score(features: RankingFeatures) -> float:
    values = (
        features.travel_fit,
        features.schedule_fit,
        features.recipient_logistics_fit,
        features.equipment_fit,
        features.fairness_rotation,
    )
    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in values):
        raise ValueError("Ranking components must be finite values between zero and one")
    return (
        0.30 * features.travel_fit
        + 0.25 * features.schedule_fit
        + 0.20 * features.recipient_logistics_fit
        + 0.15 * features.equipment_fit
        + 0.10 * features.fairness_rotation
    )


def evaluate_candidate(glean_id: str, candidate: Candidate) -> CandidateDecision:
    if candidate.constraints.unresolved_safety_blockers < 0:
        raise ValueError("Safety blocker count cannot be negative")
    reasons = hard_filter_reasons(candidate.constraints)
    if reasons:
        return CandidateDecision(candidate.volunteer_id, False, reasons, None, None)
    score = ranking_score(candidate.ranking)
    tie_breaker = hashlib.sha256(f"{glean_id}:{candidate.volunteer_id}".encode()).hexdigest()
    return CandidateDecision(candidate.volunteer_id, True, (), score, tie_breaker)


def rank_eligible(glean_id: str, candidates: list[Candidate]) -> list[CandidateDecision]:
    decisions = [evaluate_candidate(glean_id, candidate) for candidate in candidates]
    eligible = [decision for decision in decisions if decision.eligible]
    return sorted(
        eligible,
        key=lambda decision: (-(decision.score or 0.0), decision.tie_breaker or ""),
    )
