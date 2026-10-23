from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from harvestgrid_domain import (
    AssignmentState,
    GleanState,
    GleanWorkflow,
    RestrictedLocation,
)
from harvestgrid_domain_matching import (
    Candidate,
    HardConstraints,
    RankingFeatures,
)

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
REVEAL_START = T0 + timedelta(minutes=10)
REVEAL_END = T0 + timedelta(hours=2)


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


def candidate(volunteer_id: str, score: float = 1.0) -> Candidate:
    return Candidate(
        volunteer_id,
        constraints(),
        RankingFeatures(score, score, score, score, score),
    )


def ready_workflow() -> GleanWorkflow:
    workflow = GleanWorkflow.new(
        glean_id="glean-fixture",
        forecast_id="forecast-fixture",
        owner_id="owner-fixture",
        coordinator_id="coordinator-fixture",
    )
    workflow.request_glean(actor_id="owner-fixture", expected_version=0, at=T0)
    workflow.confirm_readiness(
        actor_id="coordinator-fixture",
        expected_version=1,
        at=T0 + timedelta(minutes=1),
    )
    return workflow


def invited_workflow() -> GleanWorkflow:
    workflow = ready_workflow()
    workflow.propose_assignments(
        actor_id="coordinator-fixture",
        candidates=[
            candidate("volunteer-a", 0.8),
            candidate("volunteer-b", 0.9),
            replace(
                candidate("volunteer-blocked"),
                constraints=replace(constraints(), trained_leader_available=False),
            ),
        ],
        required_count=1,
        expected_version=2,
        at=T0 + timedelta(minutes=2),
    )
    workflow.approve_assignments(
        actor_id="coordinator-fixture",
        reveal_not_before=REVEAL_START,
        reveal_until=REVEAL_END,
        expected_version=3,
        at=T0 + timedelta(minutes=3),
    )
    return workflow


def confirmed_workflow() -> GleanWorkflow:
    workflow = invited_workflow()
    assert workflow.snapshot.proposed_assignments[0].volunteer_id == "volunteer-b"
    workflow.confirm_assignment(
        actor_id="volunteer-b",
        expected_version=4,
        at=REVEAL_START,
    )
    return workflow


def test_approval_to_confirmed_assignment_and_bounded_location_access() -> None:
    workflow = confirmed_workflow()
    assert workflow.snapshot.state is GleanState.CONFIRMED
    assert workflow.snapshot.assignments[0].state is AssignmentState.CONFIRMED
    restricted = RestrictedLocation("Fixture exact address", "Fixture access note")
    assert (
        workflow.reveal_location(actor_id="volunteer-b", location=restricted, at=REVEAL_START)
        is restricted
    )
    assert "Fixture exact address" not in repr(workflow.snapshot)
    with pytest.raises(PermissionError):
        workflow.reveal_location(actor_id="volunteer-a", location=restricted, at=REVEAL_START)
    with pytest.raises(PermissionError):
        workflow.reveal_location(actor_id="volunteer-b", location=restricted, at=REVEAL_END)


def test_matching_and_location_access_cannot_start_before_owner_approval() -> None:
    workflow = GleanWorkflow.new(
        glean_id="glean-fixture",
        forecast_id="forecast-fixture",
        owner_id="owner-fixture",
        coordinator_id="coordinator-fixture",
    )
    before = workflow.snapshot
    with pytest.raises(ValueError, match="matching_is_not_allowed"):
        workflow.propose_assignments(
            actor_id="coordinator-fixture",
            candidates=[candidate("volunteer-a")],
            required_count=1,
            expected_version=0,
            at=T0,
        )
    with pytest.raises(PermissionError):
        workflow.reveal_location(
            actor_id="volunteer-a",
            location=RestrictedLocation("Fixture exact address", "Fixture access note"),
            at=T0,
        )
    assert workflow.snapshot == before
    assert workflow.snapshot.assignments == ()


def test_no_eligible_candidate_creates_no_assignment_or_transition() -> None:
    workflow = ready_workflow()
    before = workflow.snapshot
    blocked = replace(
        candidate("volunteer-blocked"),
        constraints=replace(constraints(), unresolved_safety_blockers=1),
    )
    with pytest.raises(ValueError, match="Not enough eligible"):
        workflow.propose_assignments(
            actor_id="coordinator-fixture",
            candidates=[blocked],
            required_count=1,
            expected_version=2,
            at=T0 + timedelta(minutes=2),
        )
    assert workflow.snapshot == before
    assert workflow.snapshot.assignments == ()


def test_duplicate_approval_confirmation_and_owner_request_are_idempotent() -> None:
    workflow = invited_workflow()
    invited = workflow.snapshot
    assert (
        workflow.approve_assignments(
            actor_id="coordinator-fixture",
            reveal_not_before=REVEAL_START,
            reveal_until=REVEAL_END,
            expected_version=3,
            at=T0 + timedelta(minutes=4),
        )
        == invited
    )
    workflow.confirm_assignment(actor_id="volunteer-b", expected_version=4, at=REVEAL_START)
    confirmed = workflow.snapshot
    assert (
        workflow.confirm_assignment(actor_id="volunteer-b", expected_version=4, at=REVEAL_START)
        == confirmed
    )
    assert (
        workflow.request_glean(actor_id="owner-fixture", expected_version=0, at=REVEAL_START)
        == confirmed
    )
    assert len(workflow.snapshot.audit) == 5


def test_owner_revocation_cancels_assignments_and_blocks_location() -> None:
    workflow = confirmed_workflow()
    cancelled = workflow.cancel(
        actor_id="owner-fixture",
        reason="owner_consent_revoked",
        expected_version=5,
        at=T0 + timedelta(minutes=20),
    )
    assert cancelled.state is GleanState.CANCELLED
    assert not cancelled.owner_approval_current
    assert all(
        assignment.state is AssignmentState.CANCELLED for assignment in cancelled.assignments
    )
    with pytest.raises(PermissionError):
        workflow.reveal_location(
            actor_id="volunteer-b",
            location=RestrictedLocation("Fixture exact address", "Fixture access note"),
            at=T0 + timedelta(minutes=20),
        )
    assert (
        workflow.cancel(
            actor_id="owner-fixture",
            reason="owner_consent_revoked",
            expected_version=5,
            at=T0 + timedelta(minutes=21),
        )
        == cancelled
    )


def test_stale_versions_actors_and_invalid_times_fail_closed() -> None:
    workflow = GleanWorkflow.new(
        glean_id="glean-fixture",
        forecast_id="forecast-fixture",
        owner_id="owner-fixture",
        coordinator_id="coordinator-fixture",
    )
    with pytest.raises(ValueError, match="stale_glean_version"):
        workflow.request_glean(actor_id="owner-fixture", expected_version=1, at=T0)
    with pytest.raises(PermissionError):
        workflow.request_glean(actor_id="coordinator-fixture", expected_version=0, at=T0)
    with pytest.raises(ValueError, match="timezone"):
        workflow.request_glean(
            actor_id="owner-fixture",
            expected_version=0,
            at=datetime(2026, 10, 23, 12, 0),
        )

    ready = ready_workflow()
    with pytest.raises(ValueError, match="At least one"):
        ready.propose_assignments(
            actor_id="coordinator-fixture",
            candidates=[candidate("volunteer-a")],
            required_count=0,
            expected_version=2,
            at=T0,
        )
    ready.propose_assignments(
        actor_id="coordinator-fixture",
        candidates=[candidate("volunteer-a")],
        required_count=1,
        expected_version=2,
        at=T0,
    )
    before = ready.snapshot
    with pytest.raises(ValueError, match="must be ordered"):
        ready.approve_assignments(
            actor_id="coordinator-fixture",
            reveal_not_before=REVEAL_END,
            reveal_until=REVEAL_START,
            expected_version=3,
            at=T0,
        )
    assert ready.snapshot == before


def test_every_assigned_volunteer_can_confirm_in_a_multi_person_glean() -> None:
    workflow = ready_workflow()
    workflow.propose_assignments(
        actor_id="coordinator-fixture",
        candidates=[candidate("volunteer-a", 0.8), candidate("volunteer-b", 0.9)],
        required_count=2,
        expected_version=2,
        at=T0,
    )
    workflow.approve_assignments(
        actor_id="coordinator-fixture",
        reveal_not_before=REVEAL_START,
        reveal_until=REVEAL_END,
        expected_version=3,
        at=T0,
    )
    workflow.confirm_assignment(actor_id="volunteer-b", expected_version=4, at=REVEAL_START)
    workflow.confirm_assignment(actor_id="volunteer-a", expected_version=5, at=REVEAL_START)
    assert workflow.snapshot.version == 6
    assert all(
        assignment.state is AssignmentState.CONFIRMED
        for assignment in workflow.snapshot.assignments
    )


def test_snapshot_and_remaining_authorization_guards() -> None:
    ready = ready_workflow()
    assert (
        ready.confirm_readiness(actor_id="coordinator-fixture", expected_version=0, at=T0)
        == ready.snapshot
    )
    with pytest.raises(ValueError, match="Snapshot version"):
        GleanWorkflow(replace(ready.snapshot, version=3))
    with pytest.raises(ValueError, match="IDs are required"):
        GleanWorkflow(replace(ready.snapshot, glean_id=" "))
    with pytest.raises(PermissionError):
        ready.cancel(
            actor_id="volunteer-a",
            reason="coordinator_cancelled",
            expected_version=2,
            at=T0,
        )
    cancelled = ready.cancel(
        actor_id="coordinator-fixture",
        reason="coordinator_cancelled",
        expected_version=2,
        at=T0,
    )
    assert cancelled.state is GleanState.CANCELLED
    with pytest.raises(ValueError, match="cancelled_glean_is_immutable"):
        ready.request_glean(actor_id="owner-fixture", expected_version=3, at=T0)
    with pytest.raises(ValueError, match="already committed"):
        ready.cancel(
            actor_id="owner-fixture",
            reason="owner_consent_revoked",
            expected_version=3,
            at=T0,
        )

    invited = invited_workflow()
    with pytest.raises(PermissionError, match="not_assigned"):
        invited.confirm_assignment(actor_id="volunteer-unknown", expected_version=4, at=T0)
    with pytest.raises(ValueError, match="cannot be changed"):
        invited.approve_assignments(
            actor_id="coordinator-fixture",
            reveal_not_before=REVEAL_START,
            reveal_until=REVEAL_END + timedelta(seconds=1),
            expected_version=4,
            at=T0,
        )
    confirmed = confirmed_workflow()
    with pytest.raises(ValueError, match="unavailable"):
        confirmed.reveal_location(
            actor_id="volunteer-b",
            location=RestrictedLocation(" ", "No address"),
            at=REVEAL_START,
        )
