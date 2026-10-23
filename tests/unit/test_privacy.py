import json
from datetime import UTC, datetime, timedelta

import pytest
from harvestgrid_domain import (
    ArtifactKind,
    CameraLinkState,
    FixturePrivacyService,
    GleanWorkflow,
    RestrictedLocation,
)
from harvestgrid_domain_matching import Candidate, HardConstraints, RankingFeatures

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
LOCATION = RestrictedLocation("FIXTURE exact address", "FIXTURE access note")


def service_with_tree(*, camera_ids: tuple[str, ...] = ("camera-a",)) -> FixturePrivacyService:
    service = FixturePrivacyService(clock=lambda: T0)
    service.register_organization("org-fixture", ["admin-fixture"])
    service.register_tree(
        tree_id="tree-fixture",
        owner_id="owner-fixture",
        organization_id="org-fixture",
        cultivar="Prunus domestica",
        coarse_location="fixture-coarse-area",
        camera_ids=camera_ids,
        restricted_location=LOCATION,
    )
    return service


def add_artifacts(service: FixturePrivacyService, *, camera_id: str | None = "camera-a") -> None:
    for index, kind in enumerate(
        (
            ArtifactKind.RAW_MEDIA,
            ArtifactKind.DERIVED_FEATURE,
            ArtifactKind.FORECAST,
            ArtifactKind.ACCESS_NOTES,
            ArtifactKind.ANALYTICS_IDENTIFIER,
        )
    ):
        service.add_artifact(
            tree_id="tree-fixture",
            artifact_id=f"artifact-{index}",
            kind=kind,
            delete_after=T0 + timedelta(days=1),
            camera_id=camera_id if kind is ArtifactKind.RAW_MEDIA else None,
        )


def candidate() -> Candidate:
    constraints = HardConstraints(
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
    return Candidate("volunteer-fixture", constraints, RankingFeatures(1, 1, 1, 1, 1))


def confirmed_workflow() -> GleanWorkflow:
    workflow = GleanWorkflow.new(
        glean_id="glean-fixture",
        forecast_id="forecast-fixture",
        owner_id="owner-fixture",
        coordinator_id="coordinator-fixture",
    )
    workflow.request_glean(actor_id="owner-fixture", expected_version=0, at=T0)
    workflow.confirm_readiness(
        actor_id="coordinator-fixture", expected_version=1, at=T0 + timedelta(minutes=1)
    )
    workflow.propose_assignments(
        actor_id="coordinator-fixture",
        candidates=[candidate()],
        required_count=1,
        expected_version=2,
        at=T0 + timedelta(minutes=2),
    )
    workflow.approve_assignments(
        actor_id="coordinator-fixture",
        reveal_not_before=T0 + timedelta(minutes=10),
        reveal_until=T0 + timedelta(hours=2),
        expected_version=3,
        at=T0 + timedelta(minutes=3),
    )
    workflow.confirm_assignment(
        actor_id="volunteer-fixture",
        expected_version=4,
        at=T0 + timedelta(minutes=10),
    )
    return workflow


def finish_job(service: FixturePrivacyService, job_id: str) -> None:
    for index in range(5):
        service.advance_deletion(job_id=job_id, at=T0 + timedelta(minutes=index))


def test_export_is_minimized_and_requires_owner_recent_auth() -> None:
    service = service_with_tree()
    add_artifacts(service)
    exported = service.export_tree(
        tree_id="tree-fixture", actor_id="owner-fixture", recent_authentication=True
    )
    serialized = json.dumps(exported)
    assert exported["raw_media_included"] is False
    assert exported["exact_address_included"] is False
    assert exported["access_notes_included"] is False
    assert exported["access_tokens_included"] is False
    assert LOCATION.exact_address not in serialized
    assert LOCATION.access_notes not in serialized
    assert "raw_media" in serialized  # metadata kind is allowed; bytes/references are not exported
    with pytest.raises(PermissionError):
        service.export_tree(
            tree_id="tree-fixture", actor_id="volunteer-fixture", recent_authentication=True
        )
    with pytest.raises(PermissionError):
        service.export_tree(
            tree_id="tree-fixture", actor_id="owner-fixture", recent_authentication=False
        )


def test_assignment_access_is_short_lived_and_revocation_invalidates_tokens() -> None:
    service = service_with_tree()
    workflow = confirmed_workflow()
    service.bind_glean(
        tree_id="tree-fixture", glean_id="glean-fixture", coordinator_id="coordinator-fixture"
    )
    access = service.reveal_location(
        workflow=workflow,
        location=LOCATION,
        actor_id="volunteer-fixture",
        at=T0 + timedelta(minutes=10),
    )
    assert access.exact_address == LOCATION.exact_address
    assert (
        service.validate_location_access(
            access_token=access.access_token,
            actor_id="volunteer-fixture",
            at=T0 + timedelta(minutes=11),
        )
        == LOCATION
    )
    with pytest.raises(PermissionError):
        service.validate_location_access(
            access_token=access.access_token,
            actor_id="other-volunteer",
            at=T0 + timedelta(minutes=11),
        )
    with pytest.raises(PermissionError):
        service.validate_location_access(
            access_token=access.access_token,
            actor_id="volunteer-fixture",
            at=access.expires_at,
        )
    service.revoke_glean_access(glean_id="glean-fixture", actor_id="coordinator-fixture")
    with pytest.raises(PermissionError):
        service.validate_location_access(
            access_token=access.access_token,
            actor_id="volunteer-fixture",
            at=T0 + timedelta(minutes=12),
        )
    revocation = service.revoke_consent(
        tree_id="tree-fixture", actor_id="owner-fixture", expected_version=0
    )
    assert revocation.tree.consent_active is False
    assert revocation.tree.collection_allowed is False
    assert revocation.tree.cameras[0].state is CameraLinkState.REVOKED
    with pytest.raises(PermissionError):
        service.reveal_location(
            workflow=workflow,
            location=LOCATION,
            actor_id="volunteer-fixture",
            at=T0 + timedelta(minutes=13),
        )


def test_revocation_deletion_has_stage_receipts_and_verifies_artifact_absence() -> None:
    service = service_with_tree()
    add_artifacts(service)
    result = service.revoke_consent(
        tree_id="tree-fixture", actor_id="owner-fixture", expected_version=0
    )
    job_id = result.deletion_job.job_id
    assert result.deletion_job.status == "pending"
    assert (
        service.revoke_consent(
            tree_id="tree-fixture", actor_id="owner-fixture", expected_version=999
        ).duplicate
        is True
    )
    finish_job(service, job_id)
    completed = service.deletion_job(job_id)
    assert completed.status == "completed"
    assert len(completed.receipts) == 5
    assert completed.receipts[-1].stage == "verification"
    assert all(receipt.verified for receipt in completed.receipts)
    assert service.tree_snapshot("tree-fixture").deleted is True
    assert all(not service.artifact_present("tree-fixture", f"artifact-{i}") for i in range(5))
    assert service.advance_deletion(job_id=job_id, at=T0).receipts == completed.receipts
    with pytest.raises(ValueError, match="no_export"):
        service.export_tree(
            tree_id="tree-fixture", actor_id="owner-fixture", recent_authentication=True
        )
    with pytest.raises(ValueError, match="seven_days"):
        service_with_tree().add_artifact(
            tree_id="tree-fixture",
            artifact_id="too-long",
            kind=ArtifactKind.RAW_MEDIA,
            delete_after=T0 + timedelta(days=8),
        )


def test_camera_unlink_deletes_only_that_camera_artifacts() -> None:
    service = service_with_tree(camera_ids=("camera-a", "camera-b"))
    service.add_artifact(
        tree_id="tree-fixture",
        artifact_id="raw-a",
        kind=ArtifactKind.RAW_MEDIA,
        delete_after=T0 + timedelta(days=1),
        camera_id="camera-a",
    )
    service.add_artifact(
        tree_id="tree-fixture",
        artifact_id="raw-b",
        kind=ArtifactKind.RAW_MEDIA,
        delete_after=T0 + timedelta(days=1),
        camera_id="camera-b",
    )
    job = service.unlink_camera(
        tree_id="tree-fixture",
        camera_id="camera-a",
        actor_id="owner-fixture",
        expected_version=0,
    )
    finish_job(service, job.job_id)
    snapshot = service.tree_snapshot("tree-fixture")
    assert snapshot.consent_active is True
    assert snapshot.deleted is False
    assert snapshot.cameras[0].state is CameraLinkState.UNLINKED
    assert snapshot.cameras[1].state is CameraLinkState.LINKED
    assert not service.artifact_present("tree-fixture", "raw-a")
    assert service.artifact_present("tree-fixture", "raw-b")


def test_tree_deletion_safeguard_and_organization_offboarding() -> None:
    service = service_with_tree()
    add_artifacts(service)
    with pytest.raises(PermissionError):
        service.request_tree_deletion(
            tree_id="tree-fixture",
            actor_id="owner-fixture",
            expected_version=0,
            coordinator_safeguard=False,
        )
    job = service.request_tree_deletion(
        tree_id="tree-fixture",
        actor_id="owner-fixture",
        expected_version=0,
        coordinator_safeguard=True,
    )
    finish_job(service, job.job_id)
    assert service.tree_snapshot("tree-fixture").deleted is True

    offboard = service_with_tree()
    offboard.register_tree(
        tree_id="tree-two",
        owner_id="owner-two",
        organization_id="org-fixture",
        cultivar="Prunus domestica",
        coarse_location="fixture-area-two",
    )
    result = offboard.offboard_organization(
        organization_id="org-fixture", actor_id="admin-fixture", expected_version=0
    )
    assert result.offboarded is True
    assert len(result.deletion_jobs) == 2
    assert all(
        not offboard.tree_snapshot(tree_id).consent_active
        for tree_id in ("tree-fixture", "tree-two")
    )
    assert (
        offboard.offboard_organization(
            organization_id="org-fixture", actor_id="admin-fixture", expected_version=999
        ).duplicate
        is True
    )
    with pytest.raises(PermissionError):
        offboard.register_tree(
            tree_id="tree-three",
            owner_id="owner-three",
            organization_id="org-fixture",
            cultivar="Prunus domestica",
            coarse_location="fixture-area-three",
        )
    assert (
        offboard.exportable_organization_metadata("org-fixture")["exact_addresses_included"]
        is False
    )
