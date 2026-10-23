import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from harvestgrid_domain import (
    CompletionEntry,
    GleanSnapshot,
    GleanState,
    GleanWorkflow,
    RejectionReason,
    WeightEntry,
    WeightSource,
    evaluate_forecast_outcome,
)
from harvestgrid_domain_matching import Candidate, HardConstraints, RankingFeatures

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
VOLUNTEER = "volunteer-fixture"
COORDINATOR = "coordinator-fixture"


def candidate() -> Candidate:
    return Candidate(
        volunteer_id=VOLUNTEER,
        constraints=HardConstraints(
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
        ),
        ranking=RankingFeatures(1, 1, 1, 1, 1),
    )


def confirmed_workflow(forecast_id: str) -> GleanWorkflow:
    workflow = GleanWorkflow.new(
        glean_id="glean-fixture",
        forecast_id=forecast_id,
        owner_id="owner-fixture",
        coordinator_id=COORDINATOR,
    )
    workflow.request_glean(actor_id="owner-fixture", expected_version=0, at=T0)
    workflow.confirm_readiness(
        actor_id=COORDINATOR, expected_version=1, at=T0 + timedelta(minutes=1)
    )
    workflow.propose_assignments(
        actor_id=COORDINATOR,
        candidates=[candidate()],
        required_count=1,
        expected_version=2,
        at=T0 + timedelta(minutes=2),
    )
    workflow.approve_assignments(
        actor_id=COORDINATOR,
        reveal_not_before=T0 + timedelta(minutes=10),
        reveal_until=T0 + timedelta(hours=2),
        expected_version=3,
        at=T0 + timedelta(minutes=3),
    )
    workflow.confirm_assignment(
        actor_id=VOLUNTEER, expected_version=4, at=T0 + timedelta(minutes=10)
    )
    return workflow


def weight(source: WeightSource = WeightSource.CALIBRATED_SCALE) -> WeightEntry:
    return WeightEntry(
        gross_weight_kg=Decimal("10.0"),
        net_weight_kg=Decimal("8.5"),
        accepted_weight_kg=Decimal("8.0"),
        rejected_weight_kg=Decimal("0.5"),
        source=source,
        scale_reference="fixture-scale-01" if source is WeightSource.CALIBRATED_SCALE else None,
        rejection_reasons=(RejectionReason.SPOILED,),
        evidence_sha256="a" * 64 if source is WeightSource.CALIBRATED_SCALE else None,
    )


def completion(source: WeightSource = WeightSource.CALIBRATED_SCALE) -> CompletionEntry:
    return CompletionEntry(
        recipient_id="recipient-fixture",
        actual_harvested_at=T0 + timedelta(minutes=15),
        visible_fruit_count=40,
        weight=weight(source),
    )


def test_verified_weights_receipt_evaluation_and_close_are_idempotent(
    forecast_document: dict[str, Any],
) -> None:
    workflow = confirmed_workflow(forecast_document["window_id"])
    entry = completion()
    pending = workflow.record_completion(
        actor_id=VOLUNTEER, entry=entry, expected_version=5, at=T0 + timedelta(minutes=20)
    )
    assert pending.state is GleanState.COMPLETION_REVIEW
    assert pending.weight_record is None
    assert (
        workflow.record_completion(
            actor_id=VOLUNTEER, entry=entry, expected_version=5, at=T0 + timedelta(minutes=21)
        )
        == pending
    )

    verified = workflow.verify_completion(
        actor_id=COORDINATOR,
        forecast_document=forecast_document,
        expected_version=6,
        at=T0 + timedelta(minutes=25),
    )
    assert verified.state is GleanState.WEIGHED
    assert verified.weight_record is not None
    assert verified.weight_record.coordinator_verified is True
    assert verified.donation_receipt is not None
    assert verified.donation_receipt.accepted_weight_kg == Decimal("8.0")
    assert verified.forecast_evaluation is not None
    assert verified.forecast_evaluation.visible_count_absolute_error == 2
    assert verified.forecast_evaluation.accepted_weight_absolute_error_kg is None
    assert verified.forecast_evaluation.reason_codes == ("forecast_weight_unavailable",)
    assert verified.forecast_evaluation.automatic_model_promotion is False
    assert verified.forecast_evaluation.reviewed_dataset_manifest_required is True
    assert (
        workflow.verify_completion(
            actor_id=COORDINATOR,
            forecast_document=forecast_document,
            expected_version=6,
            at=T0 + timedelta(minutes=26),
        )
        == verified
    )

    closed = workflow.close_outcome(
        actor_id=COORDINATOR, expected_version=7, at=T0 + timedelta(minutes=30)
    )
    assert closed.state is GleanState.CLOSED
    assert closed.version == 8
    assert (
        workflow.close_outcome(
            actor_id=COORDINATOR, expected_version=7, at=T0 + timedelta(minutes=31)
        )
        == closed
    )
    assert (
        workflow.record_completion(
            actor_id=VOLUNTEER, entry=entry, expected_version=5, at=T0 + timedelta(minutes=20)
        )
        == closed
    )


def test_receipt_export_excludes_operational_and_location_details(
    forecast_document: dict[str, Any],
) -> None:
    workflow = confirmed_workflow(forecast_document["window_id"])
    workflow.record_completion(
        actor_id=VOLUNTEER,
        entry=completion(),
        expected_version=5,
        at=T0 + timedelta(minutes=20),
    )
    snapshot = workflow.verify_completion(
        actor_id=COORDINATOR,
        forecast_document=forecast_document,
        expected_version=6,
        at=T0 + timedelta(minutes=25),
    )
    assert snapshot.donation_receipt is not None
    exported = snapshot.donation_receipt.as_non_sensitive_dict()
    assert set(exported) == {
        "receipt_number",
        "glean_id",
        "recipient_id",
        "accepted_weight_kg",
        "donation_date",
    }
    serialized = json.dumps(exported)
    for sensitive in (
        VOLUNTEER,
        "fixture-scale-01",
        "a" * 64,
        "spoiled",
        "address",
        "access_notes",
    ):
        assert sensitive not in serialized


def test_completion_authorization_stale_versions_and_post_outcome_guards(
    forecast_document: dict[str, Any],
) -> None:
    workflow = confirmed_workflow(forecast_document["window_id"])
    with pytest.raises(PermissionError, match="confirmed_volunteer"):
        workflow.record_completion(
            actor_id="unassigned",
            entry=completion(),
            expected_version=5,
            at=T0 + timedelta(minutes=20),
        )
    with pytest.raises(ValueError, match="stale_glean_version"):
        workflow.record_completion(
            actor_id=VOLUNTEER,
            entry=completion(),
            expected_version=4,
            at=T0 + timedelta(minutes=20),
        )
    with pytest.raises(ValueError, match="verification_is_not_allowed"):
        workflow.verify_completion(
            actor_id=COORDINATOR,
            forecast_document=forecast_document,
            expected_version=5,
            at=T0 + timedelta(minutes=20),
        )
    with pytest.raises(PermissionError, match="Coordinator-manual"):
        workflow.record_completion(
            actor_id=VOLUNTEER,
            entry=completion(WeightSource.COORDINATOR_MANUAL),
            expected_version=5,
            at=T0 + timedelta(minutes=20),
        )
    assert workflow.snapshot.version == 5

    workflow.record_completion(
        actor_id=VOLUNTEER,
        entry=completion(),
        expected_version=5,
        at=T0 + timedelta(minutes=20),
    )
    with pytest.raises(PermissionError, match="not_authorized"):
        workflow.verify_completion(
            actor_id="other-coordinator",
            forecast_document=forecast_document,
            expected_version=6,
            at=T0 + timedelta(minutes=25),
        )
    with pytest.raises(ValueError, match="weighed outcome"):
        workflow.close_outcome(
            actor_id=COORDINATOR, expected_version=6, at=T0 + timedelta(minutes=25)
        )
    with pytest.raises(ValueError, match="cannot be cancelled"):
        workflow.cancel(
            actor_id=COORDINATOR,
            reason="coordinator_cancelled",
            expected_version=6,
            at=T0 + timedelta(minutes=25),
        )
    with pytest.raises(ValueError, match="current_state"):
        workflow.record_completion(
            actor_id=COORDINATOR,
            entry=completion(),
            expected_version=6,
            at=T0 + timedelta(minutes=25),
        )
    other_forecast = deepcopy(forecast_document)
    other_forecast["window_id"] = "01000000000000000000000099"
    with pytest.raises(ValueError, match="does not match"):
        workflow.verify_completion(
            actor_id=COORDINATOR,
            forecast_document=other_forecast,
            expected_version=6,
            at=T0 + timedelta(minutes=25),
        )
    assert workflow.snapshot.state is GleanState.COMPLETION_REVIEW
    assert workflow.snapshot.version == 6


def test_weight_entry_validation_is_conservative() -> None:
    valid = weight()
    invalid_factories = (
        lambda: replace(valid, gross_weight_kg=Decimal("-1")),
        lambda: replace(valid, net_weight_kg=Decimal("11")),
        lambda: replace(valid, accepted_weight_kg=Decimal("7")),
        lambda: replace(valid, rejection_reasons=()),
        lambda: replace(
            valid,
            rejection_reasons=(RejectionReason.SPOILED, RejectionReason.SPOILED),
        ),
        lambda: replace(valid, scale_reference=None),
        lambda: replace(valid, evidence_sha256=None),
        lambda: replace(valid, evidence_sha256="not-a-hash"),
    )
    for invalid_factory in invalid_factories:
        with pytest.raises(ValueError):
            invalid_factory()
    with pytest.raises(ValueError, match="Unknown weight source"):
        replace(valid, source="unknown")
    with pytest.raises(ValueError, match="Rejection reasons require"):
        WeightEntry(
            Decimal(1),
            Decimal(1),
            Decimal(1),
            Decimal(0),
            WeightSource.COORDINATOR_MANUAL,
            rejection_reasons=(RejectionReason.DAMAGED,),
        )
    with pytest.raises(ValueError, match="finite"):
        replace(valid, gross_weight_kg=Decimal("NaN"))


def test_completion_and_record_timestamp_and_count_validation() -> None:
    with pytest.raises(ValueError, match="timezone"):
        replace(completion(), actual_harvested_at=datetime(2026, 10, 23))
    with pytest.raises(ValueError, match="Visible fruit count"):
        replace(completion(), visible_fruit_count=-1)
    with pytest.raises(ValueError, match="Visible fruit count"):
        replace(completion(), visible_fruit_count=True)
    with pytest.raises(ValueError, match="Recipient ID"):
        replace(completion(), recipient_id=" ")
    with pytest.raises(ValueError, match="after its entry"):
        workflow = confirmed_workflow("forecast")
        workflow.record_completion(
            actor_id=VOLUNTEER,
            entry=completion(),
            expected_version=5,
            at=T0,
        )


def test_forecast_weight_errors_and_unverified_or_invalid_inputs(
    forecast_document: dict[str, Any],
) -> None:
    workflow = confirmed_workflow(forecast_document["window_id"])
    workflow.record_completion(
        actor_id=VOLUNTEER,
        entry=completion(),
        expected_version=5,
        at=T0 + timedelta(minutes=20),
    )
    verified = workflow.verify_completion(
        actor_id=COORDINATOR,
        forecast_document=forecast_document,
        expected_version=6,
        at=T0 + timedelta(minutes=25),
    ).weight_record
    assert verified is not None

    weighted_forecast = deepcopy(forecast_document)
    weighted_forecast["visible_yield"].update(
        estimated_weight_kg=9.0,
        weight_lower_kg=8.0,
        weight_upper_kg=10.0,
        estimation_method="cultivar_weight_model",
    )
    evaluation = evaluate_forecast_outcome(
        weighted_forecast,
        record=verified,
        actual_harvested_at=T0 + timedelta(minutes=15),
        visible_fruit_count=None,
    )
    assert evaluation.visible_count_absolute_error is None
    assert evaluation.accepted_weight_absolute_error_kg == Decimal("1.0")
    assert evaluation.accepted_weight_absolute_percentage_error == Decimal("12.5")
    assert evaluation.reason_codes == ("actual_visible_count_unavailable",)
    assert evaluation.automatic_model_promotion is False

    unverified = replace(verified, coordinator_verified=False, verified_by=None, verified_at=None)
    with pytest.raises(ValueError, match="coordinator-verified"):
        evaluate_forecast_outcome(
            forecast_document,
            record=unverified,
            actual_harvested_at=T0 + timedelta(minutes=15),
            visible_fruit_count=40,
        )
    with pytest.raises(ValueError, match="precede forecast"):
        evaluate_forecast_outcome(
            forecast_document,
            record=verified,
            actual_harvested_at=datetime(2026, 10, 19, tzinfo=UTC),
            visible_fruit_count=40,
        )
    with pytest.raises(ValueError, match="Invalid harvest-window"):
        evaluate_forecast_outcome(
            {**forecast_document, "status": "coordinator_confirmed"},
            record=verified,
            actual_harvested_at=T0 + timedelta(minutes=15),
            visible_fruit_count=40,
        )


def test_zero_accepted_weight_has_undefined_percentage_and_snapshot_guards(
    forecast_document: dict[str, Any],
) -> None:
    zero_weight = WeightEntry(
        Decimal(0), Decimal(0), Decimal(0), Decimal(0), WeightSource.COORDINATOR_MANUAL
    )
    zero_entry = replace(completion(), weight=zero_weight)
    workflow = confirmed_workflow(forecast_document["window_id"])
    workflow.record_completion(
        actor_id=COORDINATOR,
        entry=zero_entry,
        expected_version=5,
        at=T0 + timedelta(minutes=20),
    )
    weighted_forecast = deepcopy(forecast_document)
    weighted_forecast["visible_yield"].update(
        estimated_weight_kg=1.0,
        weight_lower_kg=0.0,
        weight_upper_kg=2.0,
        estimation_method="cultivar_weight_model",
    )
    snapshot = workflow.verify_completion(
        actor_id=COORDINATOR,
        forecast_document=weighted_forecast,
        expected_version=6,
        at=T0 + timedelta(minutes=25),
    )
    assert snapshot.forecast_evaluation is not None
    assert snapshot.forecast_evaluation.accepted_weight_absolute_percentage_error is None
    assert "accepted_weight_zero_percentage_undefined" in snapshot.forecast_evaluation.reason_codes

    with pytest.raises(ValueError, match="incomplete"):
        GleanWorkflow(replace(snapshot, donation_receipt=None))
    with pytest.raises(ValueError, match="not allowed"):
        GleanWorkflow(GleanSnapshot("g", "f", "o", "c", completion=snapshot.completion))
    assert snapshot.weight_record is not None
    assert snapshot.donation_receipt is not None
    assert snapshot.forecast_evaluation is not None
    record = snapshot.weight_record
    receipt = snapshot.donation_receipt
    evaluation = snapshot.forecast_evaluation
    mismatched_entry = replace(record.entry, evidence_sha256="b" * 64)
    with pytest.raises(ValueError, match="do not match the pending completion"):
        GleanWorkflow(replace(snapshot, weight_record=replace(record, entry=mismatched_entry)))
    with pytest.raises(ValueError, match="Weight-record glean"):
        GleanWorkflow(replace(snapshot, weight_record=replace(record, glean_id="other")))
    with pytest.raises(ValueError, match="Receipt glean"):
        GleanWorkflow(replace(snapshot, donation_receipt=replace(receipt, glean_id="other")))
    with pytest.raises(ValueError, match="Evaluation forecast"):
        GleanWorkflow(
            replace(snapshot, forecast_evaluation=replace(evaluation, forecast_id="other"))
        )
    with pytest.raises(ValueError, match="Receipt does not match the pending completion"):
        GleanWorkflow(replace(snapshot, donation_receipt=replace(receipt, recipient_id="other")))
    with pytest.raises(ValueError, match="Receipt does not match the pending completion"):
        GleanWorkflow(
            replace(
                snapshot,
                donation_receipt=replace(
                    receipt,
                    donation_date=(T0 + timedelta(days=1)).date(),
                ),
            )
        )
    with pytest.raises(ValueError, match="Receipt accepted weight"):
        GleanWorkflow(
            replace(
                snapshot,
                donation_receipt=replace(receipt, accepted_weight_kg=Decimal(1)),
            )
        )
    with pytest.raises(ValueError, match="harvest timestamp does not match"):
        GleanWorkflow(
            replace(
                snapshot,
                forecast_evaluation=replace(evaluation, actual_harvested_at=T0),
            )
        )
