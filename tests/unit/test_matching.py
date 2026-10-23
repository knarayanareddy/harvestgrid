from dataclasses import replace

import pytest
from harvestgrid_domain_matching import (
    Candidate,
    HardConstraints,
    RankingFeatures,
    evaluate_candidate,
    rank_eligible,
    ranking_score,
)


def constraints() -> HardConstraints:
    return HardConstraints(
        owner_approval_current=True,
        coordinator_approval_current=True,
        trained_leader_available=True,
        volunteer_training_valid=True,
        equipment_satisfied=True,
        availability_intersects=True,
        recipient_capacity_sufficient=True,
        coordinator_no_recipient_plan_approved=False,
        unresolved_safety_blockers=0,
        address_reveal_policy_satisfied=True,
        insurance_waiver_satisfied=True,
    )


def candidate(volunteer_id: str = "volunteer-a", score: float = 1.0) -> Candidate:
    return Candidate(
        volunteer_id=volunteer_id,
        constraints=constraints(),
        ranking=RankingFeatures(score, score, score, score, score),
    )


@pytest.mark.parametrize(
    "field",
    [
        "owner_approval_current",
        "coordinator_approval_current",
        "trained_leader_available",
        "volunteer_training_valid",
        "equipment_satisfied",
        "availability_intersects",
        "address_reveal_policy_satisfied",
        "insurance_waiver_satisfied",
    ],
)
def test_every_operational_approval_is_a_hard_filter(field: str) -> None:
    blocked = replace(candidate(), constraints=replace(constraints(), **{field: False}))
    decision = evaluate_candidate("glean-fixture", blocked)
    assert not decision.eligible
    assert decision.score is None


def test_recipient_safety_and_alternative_rules() -> None:
    insufficient = replace(
        constraints(),
        recipient_capacity_sufficient=False,
        coordinator_no_recipient_plan_approved=False,
    )
    assert (
        "recipient_capacity_or_approved_alternative_required"
        in evaluate_candidate(
            "glean-fixture", replace(candidate(), constraints=insufficient)
        ).reason_codes
    )

    alternative = replace(insufficient, coordinator_no_recipient_plan_approved=True)
    assert evaluate_candidate(
        "glean-fixture", replace(candidate(), constraints=alternative)
    ).eligible

    unsafe = replace(constraints(), unresolved_safety_blockers=1)
    assert (
        "unresolved_safety_blocker"
        in evaluate_candidate(
            "glean-fixture", replace(candidate(), constraints=unsafe)
        ).reason_codes
    )
    with pytest.raises(ValueError, match="cannot be negative"):
        evaluate_candidate(
            "glean-fixture",
            replace(candidate(), constraints=replace(constraints(), unresolved_safety_blockers=-1)),
        )


def test_score_formula_and_validation_are_deterministic() -> None:
    features = RankingFeatures(1.0, 0.8, 0.6, 0.4, 0.2)
    assert ranking_score(features) == pytest.approx(0.30 + 0.20 + 0.12 + 0.06 + 0.02)
    for invalid in [float("nan"), -0.1, 1.1]:
        with pytest.raises(ValueError, match="finite values"):
            ranking_score(replace(features, travel_fit=invalid))


def test_ranking_is_repeatable_with_seeded_stable_ties() -> None:
    tied = [candidate("volunteer-b"), candidate("volunteer-a"), candidate("volunteer-c")]
    first = rank_eligible("glean-fixture", tied)
    second = rank_eligible("glean-fixture", list(reversed(tied)))
    assert first == second
    assert [decision.score for decision in first] == [1.0, 1.0, 1.0]
    assert len({decision.tie_breaker for decision in first}) == 3

    blocked = replace(
        candidate("volunteer-blocked"),
        constraints=replace(constraints(), trained_leader_available=False),
    )
    assert "volunteer-blocked" not in {
        decision.volunteer_id for decision in rank_eligible("glean-fixture", [*tied, blocked])
    }
