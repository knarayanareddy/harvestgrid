import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from harvestgrid_domain_matching import Candidate, HardConstraints, RankingFeatures

from .evaluation import ReviewedEvaluationSet, evaluate_model_data_gate
from .forecast import validate_harvest_window
from .operations import GleanWorkflow, RestrictedLocation
from .outcomes import (
    CompletionEntry,
    RejectionReason,
    WeightEntry,
    WeightRecord,
    WeightSource,
)
from .privacy import ArtifactKind, FixturePrivacyService

ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def candidate(volunteer_id: str, score: float) -> Candidate:
    return Candidate(
        volunteer_id=volunteer_id,
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
        ranking=RankingFeatures(score, score, score, score, score),
    )


def main() -> None:
    document: dict[str, Any] = json.loads(
        (ROOT / "examples" / "harvest-window.example.json").read_text()
    )
    issues = validate_harvest_window(document)
    if issues:
        raise ValueError(f"Invalid harvest-window fixture: {issues}")

    workflow = GleanWorkflow.new(
        glean_id="glean-fixture",
        forecast_id=document["window_id"],
        owner_id="owner-fixture",
        coordinator_id="coordinator-fixture",
    )
    workflow.request_glean(actor_id="owner-fixture", expected_version=0, at=T0)
    workflow.confirm_readiness(
        actor_id="coordinator-fixture",
        expected_version=1,
        at=T0 + timedelta(minutes=1),
    )
    workflow.propose_assignments(
        actor_id="coordinator-fixture",
        candidates=[candidate("volunteer-a", 0.8), candidate("volunteer-b", 0.9)],
        required_count=1,
        expected_version=2,
        at=T0 + timedelta(minutes=2),
    )
    reveal_start = T0 + timedelta(minutes=10)
    reveal_end = T0 + timedelta(hours=2)
    workflow.approve_assignments(
        actor_id="coordinator-fixture",
        reveal_not_before=reveal_start,
        reveal_until=reveal_end,
        expected_version=3,
        at=T0 + timedelta(minutes=3),
    )
    selected = workflow.snapshot.proposed_assignments[0].volunteer_id
    workflow.confirm_assignment(actor_id=selected, expected_version=4, at=reveal_start)
    restricted = RestrictedLocation(
        "FIXTURE ONLY — not a residential address", "Fixture access note"
    )
    privacy = FixturePrivacyService(clock=lambda: T0)
    privacy.register_organization("org-fixture", ["admin-fixture"])
    privacy.register_tree(
        tree_id="tree-fixture",
        owner_id="owner-fixture",
        organization_id="org-fixture",
        cultivar="Prunus domestica",
        coarse_location="fixture-coarse-area",
        camera_ids=["camera-fixture"],
        restricted_location=restricted,
    )
    for index, kind in enumerate(
        (
            ArtifactKind.RAW_MEDIA,
            ArtifactKind.DERIVED_FEATURE,
            ArtifactKind.FORECAST,
            ArtifactKind.ACCESS_NOTES,
            ArtifactKind.ANALYTICS_IDENTIFIER,
        )
    ):
        privacy.add_artifact(
            tree_id="tree-fixture",
            artifact_id=f"fixture-artifact-{index}",
            kind=kind,
            delete_after=T0 + timedelta(days=1),
            camera_id="camera-fixture" if kind is ArtifactKind.RAW_MEDIA else None,
        )
    privacy.bind_glean(
        tree_id="tree-fixture",
        glean_id="glean-fixture",
        coordinator_id="coordinator-fixture",
    )
    access = privacy.reveal_location(
        workflow=workflow,
        location=restricted,
        actor_id=selected,
        at=reveal_start,
    )
    privacy.validate_location_access(
        access_token=access.access_token,
        actor_id=selected,
        at=reveal_start,
    )
    minimized_export = privacy.export_tree(
        tree_id="tree-fixture",
        actor_id="owner-fixture",
        recent_authentication=True,
    )
    outcome_workflow = GleanWorkflow(deepcopy(workflow.snapshot))
    cancelled = workflow.cancel(
        actor_id="owner-fixture",
        reason="owner_consent_revoked",
        expected_version=5,
        at=T0 + timedelta(minutes=20),
    )
    try:
        workflow.reveal_location(
            actor_id=selected,
            location=restricted,
            at=T0 + timedelta(minutes=20),
        )
    except PermissionError:
        reveal_after_cancellation = False
    else:
        raise AssertionError("Cancelled workflow must not reveal a restricted location")
    privacy.revoke_glean_access(glean_id="glean-fixture", actor_id="owner-fixture")
    try:
        privacy.validate_location_access(
            access_token=access.access_token,
            actor_id=selected,
            at=T0 + timedelta(minutes=20),
        )
    except PermissionError:
        privacy_access_after_cancellation = False
    else:
        raise AssertionError("Cancelled privacy grant must not reveal a restricted location")
    revocation = privacy.revoke_consent(
        tree_id="tree-fixture",
        actor_id="owner-fixture",
        expected_version=0,
    )
    for index in range(5):
        privacy.advance_deletion(
            job_id=revocation.deletion_job.job_id,
            at=T0 + timedelta(minutes=30 + index),
        )
    deletion = privacy.deletion_job(revocation.deletion_job.job_id)

    entry = CompletionEntry(
        recipient_id="recipient-fixture",
        actual_harvested_at=T0 + timedelta(minutes=15),
        visible_fruit_count=40,
        weight=WeightEntry(
            gross_weight_kg=Decimal("10.0"),
            net_weight_kg=Decimal("8.5"),
            accepted_weight_kg=Decimal("8.0"),
            rejected_weight_kg=Decimal("0.5"),
            source=WeightSource.CALIBRATED_SCALE,
            scale_reference="fixture-scale-01",
            rejection_reasons=(RejectionReason.SPOILED,),
            evidence_sha256="a" * 64,
        ),
    )
    outcome_workflow.record_completion(
        actor_id=selected,
        entry=entry,
        expected_version=5,
        at=T0 + timedelta(minutes=20),
    )
    verified = outcome_workflow.verify_completion(
        actor_id="coordinator-fixture",
        forecast_document=document,
        expected_version=6,
        at=T0 + timedelta(minutes=25),
    )
    closed = outcome_workflow.close_outcome(
        actor_id="coordinator-fixture",
        expected_version=7,
        at=T0 + timedelta(minutes=30),
    )
    if verified.weight_record is None:
        raise AssertionError("Verified weight record is required")
    if verified.donation_receipt is None:
        raise AssertionError("Donation receipt is required")
    if verified.forecast_evaluation is None:
        raise AssertionError("Forecast evaluation is required")

    evaluation_set = ReviewedEvaluationSet(
        manifest_sha256="d" * 64,
        review_attested=True,
    )
    evaluation_set.add(
        example_id="fixture-outcome-1",
        forecast_document=document,
        record=verified.weight_record,
        actual_harvested_at=entry.actual_harvested_at,
        visible_fruit_count=entry.visible_fruit_count,
    )
    second_harvest = datetime(2026, 11, 5, 8, 0, tzinfo=UTC)
    second_record = WeightRecord(
        glean_id="glean-fixture-2",
        entry=entry.weight,
        entered_by="coordinator-fixture",
        entered_at=second_harvest,
        coordinator_verified=True,
        verified_by="coordinator-fixture",
        verified_at=second_harvest + timedelta(minutes=5),
    )
    evaluation_set.add(
        example_id="fixture-outcome-2",
        forecast_document=document,
        record=second_record,
        actual_harvested_at=second_harvest,
        visible_fruit_count=42,
    )
    aggregate = evaluation_set.aggregate()
    data_gate = evaluate_model_data_gate(
        tree_season_sequences=2,
        site_count=1,
        held_out_site_split=False,
        held_out_season_split=False,
    )

    print(
        json.dumps(
            {
                "mode": (
                    "FIXTURE ONLY — no Ring, weather, CV, notification, database, "
                    "or coordinator connection"
                ),
                "forecast_status": document["status"],
                "model_kind": document["model"]["kind"],
                "human_readiness_review_required": document["visual_capability"][
                    "human_readiness_review_required"
                ],
                "internal_ripeness_inferred": document["visual_capability"][
                    "internal_ripeness_inferred"
                ],
                "food_safety_inferred": document["visual_capability"]["food_safety_inferred"],
                "selected_volunteer": selected,
                "workflow_state_after_revocation": cancelled.state.value,
                "assignment_count": len(cancelled.assignments),
                "location_reveal_before_cancellation": True,
                "location_reveal_after_cancellation": reveal_after_cancellation,
                "privacy_access_after_cancellation": privacy_access_after_cancellation,
                "privacy_export_minimized": (
                    minimized_export["raw_media_included"] is False
                    and minimized_export["exact_address_included"] is False
                    and minimized_export["access_notes_included"] is False
                ),
                "privacy_deletion_status": deletion.status,
                "privacy_deletion_receipt_count": len(deletion.receipts),
                "privacy_raw_artifact_remaining": privacy.artifact_present(
                    "tree-fixture", "fixture-artifact-0"
                ),
                "privacy_tree_deleted": privacy.tree_snapshot("tree-fixture").deleted,
                "notification_adapter_connected": False,
                "durable_workflow_claimed": False,
                "outcome_workflow_state": closed.state.value,
                "verified_accepted_weight_kg": str(verified.weight_record.entry.accepted_weight_kg),
                "donation_receipt": verified.donation_receipt.as_non_sensitive_dict(),
                "visible_count_absolute_error": (
                    verified.forecast_evaluation.visible_count_absolute_error
                ),
                "forecast_evaluation_reasons": verified.forecast_evaluation.reason_codes,
                "automatic_model_promotion": (
                    verified.forecast_evaluation.automatic_model_promotion
                ),
                "evaluation_set": aggregate.as_non_sensitive_dict(),
                "non_experimental_model_review_eligible": (
                    data_gate.eligible_for_non_experimental_model_review
                ),
                "model_data_gate_reasons": data_gate.reason_codes,
                "notice": (
                    "Separate in-memory revocation, minimized export, fixture deletion receipts, "
                    "coordinator-verified outcomes, and synthetic evaluation aggregation only; "
                    "no actual invitation, residential location, glean, donation, human manifest "
                    "review, held-out model evaluation, production deletion job, model promotion, "
                    "or operational outcome is claimed."
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
