from dataclasses import replace

from harvestgrid_domain_matching import (
    Candidate,
    HardConstraints,
    RankingFeatures,
    evaluate_candidate,
)


def test_high_score_cannot_waive_owner_or_safety_controls() -> None:
    constraints = HardConstraints(
        owner_approval_current=False,
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
    candidate = Candidate("fixture-volunteer", constraints, RankingFeatures(1, 1, 1, 1, 1))
    decision = evaluate_candidate("fixture-glean", candidate)
    assert not decision.eligible
    assert decision.score is None

    revoked = replace(constraints, owner_approval_current=True, unresolved_safety_blockers=1)
    assert not evaluate_candidate("fixture-glean", replace(candidate, constraints=revoked)).eligible
