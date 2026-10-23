from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from .operations import AssignmentState, GleanWorkflow, RestrictedLocation


class ArtifactKind(StrEnum):
    RAW_MEDIA = "raw_media"
    DERIVED_FEATURE = "derived_feature"
    FORECAST = "forecast"
    ACCESS_NOTES = "access_notes"
    ANALYTICS_IDENTIFIER = "analytics_identifier"
    SESSION_SNAPSHOT = "session_snapshot"


class CameraLinkState(StrEnum):
    LINKED = "linked"
    UNLINKED = "unlinked"
    REVOKED = "revoked"


@dataclass(frozen=True)
class PrivacyArtifact:
    artifact_id: str
    tree_id: str
    kind: ArtifactKind
    delete_after: datetime
    camera_id: str | None = None


@dataclass(frozen=True)
class CameraLink:
    camera_id: str
    state: CameraLinkState


@dataclass(frozen=True)
class TreePrivacySnapshot:
    tree_id: str
    owner_id: str
    organization_id: str
    cultivar: str
    coarse_location: str
    version: int
    consent_active: bool
    collection_allowed: bool
    deletion_pending: bool
    deleted: bool
    cameras: tuple[CameraLink, ...]
    artifact_ids: tuple[str, ...]


@dataclass(frozen=True)
class LocationAccess:
    access_token: str
    tree_id: str
    glean_id: str
    volunteer_id: str
    exact_address: str
    access_notes: str
    expires_at: datetime


@dataclass(frozen=True)
class DeletionReceipt:
    stage: str
    artifact_ids: tuple[str, ...]
    completed_at: datetime
    verified: bool


@dataclass(frozen=True)
class DeletionJobSnapshot:
    job_id: str
    tree_id: str
    organization_id: str
    reason: str
    camera_id: str | None
    status: str
    stages: tuple[str, ...]
    receipts: tuple[DeletionReceipt, ...]


@dataclass(frozen=True)
class RevocationResult:
    tree: TreePrivacySnapshot
    deletion_job: DeletionJobSnapshot
    duplicate: bool


@dataclass(frozen=True)
class OffboardingResult:
    organization_id: str
    offboarded: bool
    deletion_jobs: tuple[DeletionJobSnapshot, ...]
    duplicate: bool


@dataclass
class _Tree:
    tree_id: str
    owner_id: str
    organization_id: str
    cultivar: str
    coarse_location: str
    restricted_location: RestrictedLocation | None = None
    version: int = 0
    consent_active: bool = True
    collection_allowed: bool = True
    deletion_pending: bool = False
    deleted: bool = False
    cameras: dict[str, CameraLink] = field(default_factory=dict)
    artifacts: dict[str, PrivacyArtifact] = field(default_factory=dict)


@dataclass
class _Grant:
    token_hash: str
    tree_id: str
    glean_id: str
    volunteer_id: str
    location: RestrictedLocation
    expires_at: datetime
    revoked: bool = False


@dataclass
class _DeletionJob:
    job_id: str
    tree_id: str
    organization_id: str
    reason: str
    camera_id: str | None
    artifact_ids: tuple[str, ...]
    stages: tuple[str, ...] = (
        "raw_media",
        "derived_features",
        "forecasts",
        "access_and_analytics",
        "verification",
    )
    receipts: list[DeletionReceipt] = field(default_factory=list)
    status: str = "pending"


@dataclass
class _Organization:
    organization_id: str
    admin_ids: frozenset[str]
    version: int = 0
    offboarded: bool = False


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class FixturePrivacyService:
    """Metadata-only privacy lifecycle fixture; no residential media is stored."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._trees: dict[str, _Tree] = {}
        self._organizations: dict[str, _Organization] = {}
        self._glean_trees: dict[str, str] = {}
        self._glean_coordinators: dict[str, str] = {}
        self._grants: dict[str, _Grant] = {}
        self._jobs: dict[str, _DeletionJob] = {}
        self._revocation_jobs: dict[str, str] = {}
        self._organization_jobs: dict[str, tuple[str, ...]] = {}

    def _now(self) -> datetime:
        return _aware(self._clock(), "Privacy clock")

    def _tree(self, tree_id: str) -> _Tree:
        tree = self._trees.get(tree_id)
        if tree is None:
            raise ValueError("tree_not_found")
        return tree

    def _organization(self, organization_id: str) -> _Organization:
        organization = self._organizations.get(organization_id)
        if organization is None:
            raise ValueError("organization_not_found")
        return organization

    def _snapshot(self, tree: _Tree) -> TreePrivacySnapshot:
        return TreePrivacySnapshot(
            tree_id=tree.tree_id,
            owner_id=tree.owner_id,
            organization_id=tree.organization_id,
            cultivar=tree.cultivar,
            coarse_location=tree.coarse_location,
            version=tree.version,
            consent_active=tree.consent_active,
            collection_allowed=tree.collection_allowed,
            deletion_pending=tree.deletion_pending,
            deleted=tree.deleted,
            cameras=tuple(tree.cameras.values()),
            artifact_ids=tuple(sorted(tree.artifacts)),
        )

    def _job_snapshot(self, job: _DeletionJob) -> DeletionJobSnapshot:
        return DeletionJobSnapshot(
            job_id=job.job_id,
            tree_id=job.tree_id,
            organization_id=job.organization_id,
            reason=job.reason,
            camera_id=job.camera_id,
            status=job.status,
            stages=job.stages,
            receipts=tuple(job.receipts),
        )

    def register_organization(self, organization_id: str, admin_ids: Iterable[str]) -> None:
        admins = frozenset(admin_ids)
        if not organization_id.strip() or not admins or any(not item.strip() for item in admins):
            raise ValueError("organization_and_admin_required")
        if organization_id in self._organizations:
            raise ValueError("organization_already_registered")
        self._organizations[organization_id] = _Organization(organization_id, admins)

    def register_tree(
        self,
        *,
        tree_id: str,
        owner_id: str,
        organization_id: str,
        cultivar: str,
        coarse_location: str,
        camera_ids: Iterable[str] = (),
        restricted_location: RestrictedLocation | None = None,
    ) -> TreePrivacySnapshot:
        organization = self._organization(organization_id)
        if organization.offboarded:
            raise PermissionError("organization_is_offboarded")
        identifiers = (tree_id, owner_id, cultivar, coarse_location)
        if any(not item.strip() for item in identifiers) or tree_id in self._trees:
            raise ValueError("valid_unique_tree_required")
        cameras = {}
        for camera_id in camera_ids:
            if not camera_id.strip() or camera_id in cameras:
                raise ValueError("valid_unique_camera_required")
            cameras[camera_id] = CameraLink(camera_id, CameraLinkState.LINKED)
        tree = _Tree(
            tree_id=tree_id,
            owner_id=owner_id,
            organization_id=organization_id,
            cultivar=cultivar,
            coarse_location=coarse_location,
            restricted_location=restricted_location,
            cameras=cameras,
        )
        self._trees[tree_id] = tree
        return self._snapshot(tree)

    def add_artifact(
        self,
        *,
        tree_id: str,
        artifact_id: str,
        kind: ArtifactKind,
        delete_after: datetime,
        camera_id: str | None = None,
    ) -> None:
        tree = self._tree(tree_id)
        if tree.deleted or tree.deletion_pending or not tree.consent_active:
            raise PermissionError("artifact_ingestion_blocked_by_privacy_state")
        if not artifact_id.strip() or artifact_id in tree.artifacts:
            raise ValueError("unique_artifact_required")
        if any(artifact_id in other.artifacts for other in self._trees.values()):
            raise ValueError("unique_artifact_required")
        expiry = _aware(delete_after, "Artifact deletion timestamp")
        now = self._now()
        if expiry < now:
            raise ValueError("artifact_retention_is_already_expired")
        if kind is ArtifactKind.RAW_MEDIA and expiry > now + timedelta(days=7):
            raise ValueError("raw_media_retention_exceeds_seven_days")
        if camera_id is not None and camera_id not in tree.cameras:
            raise ValueError("artifact_camera_not_linked")
        tree.artifacts[artifact_id] = PrivacyArtifact(
            artifact_id=artifact_id,
            tree_id=tree_id,
            kind=kind,
            delete_after=expiry,
            camera_id=camera_id,
        )

    def bind_glean(self, *, tree_id: str, glean_id: str, coordinator_id: str) -> None:
        tree = self._tree(tree_id)
        if not glean_id.strip() or not coordinator_id.strip() or glean_id in self._glean_trees:
            raise ValueError("valid_unique_glean_binding_required")
        self._glean_trees[glean_id] = tree.tree_id
        self._glean_coordinators[glean_id] = coordinator_id

    def _require_tree_actor(self, tree: _Tree, actor_id: str) -> None:
        organization = self._organization(tree.organization_id)
        if actor_id != tree.owner_id and actor_id not in organization.admin_ids:
            raise PermissionError("privacy_actor_is_not_authorized")

    def _new_job(self, tree: _Tree, reason: str, camera_id: str | None = None) -> _DeletionJob:
        artifacts = tuple(
            sorted(
                artifact_id
                for artifact_id, artifact in tree.artifacts.items()
                if camera_id is None or artifact.camera_id == camera_id
            )
        )
        job = _DeletionJob(
            job_id=f"fixture-delete-{secrets.token_urlsafe(12)}",
            tree_id=tree.tree_id,
            organization_id=tree.organization_id,
            reason=reason,
            camera_id=camera_id,
            artifact_ids=artifacts,
        )
        self._jobs[job.job_id] = job
        return job

    def _revoke_tree(self, tree: _Tree, reason: str) -> tuple[_DeletionJob, bool]:
        existing_id = self._revocation_jobs.get(tree.tree_id)
        if existing_id:
            return self._jobs[existing_id], True
        tree.consent_active = False
        tree.collection_allowed = False
        tree.deletion_pending = True
        tree.version += 1
        tree.cameras = {
            camera_id: CameraLink(camera_id, CameraLinkState.REVOKED) for camera_id in tree.cameras
        }
        for grant in self._grants.values():
            if grant.tree_id == tree.tree_id:
                grant.revoked = True
        job = self._new_job(tree, reason)
        self._revocation_jobs[tree.tree_id] = job.job_id
        return job, False

    def revoke_consent(
        self, *, tree_id: str, actor_id: str, expected_version: int
    ) -> RevocationResult:
        tree = self._tree(tree_id)
        self._require_tree_actor(tree, actor_id)
        if tree.version != expected_version and not self._revocation_jobs.get(tree_id):
            raise ValueError("stale_privacy_version")
        job, duplicate = self._revoke_tree(tree, "consent_revoked")
        return RevocationResult(self._snapshot(tree), self._job_snapshot(job), duplicate)

    def unlink_camera(
        self, *, tree_id: str, camera_id: str, actor_id: str, expected_version: int
    ) -> DeletionJobSnapshot:
        tree = self._tree(tree_id)
        self._require_tree_actor(tree, actor_id)
        if tree.version != expected_version:
            raise ValueError("stale_privacy_version")
        camera = tree.cameras.get(camera_id)
        if camera is None:
            raise ValueError("camera_not_found")
        if camera.state is CameraLinkState.UNLINKED:
            existing = next(
                job
                for job in self._jobs.values()
                if job.tree_id == tree_id and job.camera_id == camera_id
            )
            return self._job_snapshot(existing)
        if tree.deleted or tree.deletion_pending:
            raise PermissionError("privacy_deletion_already_pending")
        tree.cameras[camera_id] = CameraLink(camera_id, CameraLinkState.UNLINKED)
        tree.version += 1
        return self._job_snapshot(self._new_job(tree, "camera_unlinked", camera_id))

    def reveal_location(
        self,
        *,
        workflow: GleanWorkflow,
        location: RestrictedLocation,
        actor_id: str,
        at: datetime,
    ) -> LocationAccess:
        glean_id = workflow.snapshot.glean_id
        tree = self._tree(self._glean_trees.get(glean_id, ""))
        if not tree.consent_active or tree.deleted or tree.deletion_pending:
            raise PermissionError("location_blocked_by_privacy_state")
        if workflow.snapshot.owner_id != tree.owner_id:
            raise PermissionError("location_owner_binding_mismatch")
        timestamp = _aware(at, "Location access timestamp")
        revealed = workflow.reveal_location(actor_id=actor_id, location=location, at=timestamp)
        assignment = next(
            item
            for item in workflow.snapshot.assignments
            if item.volunteer_id == actor_id and item.state is AssignmentState.CONFIRMED
        )
        token = secrets.token_urlsafe(32)
        expiry = min(timestamp + timedelta(hours=2), assignment.reveal_until)
        self._grants[_token_hash(token)] = _Grant(
            token_hash=_token_hash(token),
            tree_id=tree.tree_id,
            glean_id=glean_id,
            volunteer_id=actor_id,
            location=revealed,
            expires_at=expiry,
        )
        return LocationAccess(
            access_token=token,
            tree_id=tree.tree_id,
            glean_id=glean_id,
            volunteer_id=actor_id,
            exact_address=revealed.exact_address,
            access_notes=revealed.access_notes,
            expires_at=expiry,
        )

    def validate_location_access(
        self, *, access_token: str, actor_id: str, at: datetime
    ) -> RestrictedLocation:
        grant = self._grants.get(_token_hash(access_token))
        timestamp = _aware(at, "Location access timestamp")
        if (
            grant is None
            or grant.revoked
            or grant.volunteer_id != actor_id
            or timestamp >= grant.expires_at
        ):
            raise PermissionError("location_access_token_rejected")
        tree = self._tree(grant.tree_id)
        if not tree.consent_active or tree.deleted or tree.deletion_pending:
            raise PermissionError("location_access_token_rejected")
        return grant.location

    def revoke_glean_access(self, *, glean_id: str, actor_id: str) -> None:
        tree = self._tree(self._glean_trees.get(glean_id, ""))
        if actor_id not in {tree.owner_id, self._glean_coordinators[glean_id]}:
            raise PermissionError("privacy_actor_is_not_authorized")
        for grant in self._grants.values():
            if grant.glean_id == glean_id:
                grant.revoked = True

    def export_tree(
        self, *, tree_id: str, actor_id: str, recent_authentication: bool
    ) -> dict[str, Any]:
        tree = self._tree(tree_id)
        if actor_id != tree.owner_id or not recent_authentication:
            raise PermissionError("owner_recent_authentication_required")
        if tree.deleted:
            raise ValueError("deleted_tree_has_no_export")
        return {
            "tree_id": tree.tree_id,
            "owner_id": tree.owner_id,
            "organization_id": tree.organization_id,
            "cultivar": tree.cultivar,
            "coarse_location": tree.coarse_location,
            "version": tree.version,
            "consent_active": tree.consent_active,
            "collection_allowed": tree.collection_allowed,
            "cameras": [
                {"camera_id": camera.camera_id, "state": camera.state.value}
                for camera in tree.cameras.values()
            ],
            "artifacts": [
                {
                    "artifact_id": artifact.artifact_id,
                    "kind": artifact.kind.value,
                    "delete_after": artifact.delete_after.isoformat(),
                }
                for artifact in tree.artifacts.values()
            ],
            "raw_media_included": False,
            "exact_address_included": False,
            "access_notes_included": False,
            "access_tokens_included": False,
        }

    def request_tree_deletion(
        self,
        *,
        tree_id: str,
        actor_id: str,
        expected_version: int,
        coordinator_safeguard: bool,
    ) -> DeletionJobSnapshot:
        tree = self._tree(tree_id)
        if actor_id != tree.owner_id or not coordinator_safeguard:
            raise PermissionError("owner_and_coordinator_safeguard_required")
        if tree.version != expected_version and not tree.deletion_pending:
            raise ValueError("stale_privacy_version")
        job, _ = self._revoke_tree(tree, "tree_deletion_requested")
        return self._job_snapshot(job)

    def advance_deletion(self, *, job_id: str, at: datetime) -> DeletionJobSnapshot:
        job = self._jobs.get(job_id)
        if job is None:
            raise ValueError("deletion_job_not_found")
        if job.status == "completed":
            return self._job_snapshot(job)
        timestamp = _aware(at, "Deletion timestamp")
        tree = self._tree(job.tree_id)
        stage = job.stages[len(job.receipts)]
        kinds: set[ArtifactKind]
        if stage == "raw_media":
            kinds = {ArtifactKind.RAW_MEDIA}
        elif stage == "derived_features":
            kinds = {ArtifactKind.DERIVED_FEATURE, ArtifactKind.SESSION_SNAPSHOT}
        elif stage == "forecasts":
            kinds = {ArtifactKind.FORECAST}
        elif stage == "access_and_analytics":
            kinds = {ArtifactKind.ACCESS_NOTES, ArtifactKind.ANALYTICS_IDENTIFIER}
        else:
            kinds = set()
        removed: list[str] = []
        for artifact_id in job.artifact_ids:
            artifact = tree.artifacts.get(artifact_id)
            if artifact is not None and artifact.kind in kinds:
                removed.append(artifact_id)
                del tree.artifacts[artifact_id]
        if stage == "access_and_analytics" and job.camera_id is None:
            tree.restricted_location = None
            for grant in self._grants.values():
                if grant.tree_id == tree.tree_id:
                    grant.revoked = True
        if stage == "verification":
            remaining = [
                artifact_id for artifact_id in job.artifact_ids if artifact_id in tree.artifacts
            ]
            if remaining:
                job.status = "failed"
                raise RuntimeError("deletion_verification_failed")
            if job.camera_id is None:
                tree.deleted = True
                tree.deletion_pending = False
        job.receipts.append(DeletionReceipt(stage, tuple(removed), timestamp, True))
        if len(job.receipts) == len(job.stages):
            job.status = "completed"
        else:
            job.status = "in_progress"
        return self._job_snapshot(job)

    def offboard_organization(
        self, *, organization_id: str, actor_id: str, expected_version: int
    ) -> OffboardingResult:
        organization = self._organization(organization_id)
        if actor_id not in organization.admin_ids:
            raise PermissionError("organization_admin_required")
        if organization.offboarded:
            existing_jobs = tuple(
                self._jobs[job_id] for job_id in self._organization_jobs[organization_id]
            )
            return OffboardingResult(
                organization_id,
                True,
                tuple(self._job_snapshot(job) for job in existing_jobs),
                True,
            )
        if organization.version != expected_version:
            raise ValueError("stale_organization_version")
        organization.offboarded = True
        organization.version += 1
        jobs: list[_DeletionJob] = []
        for tree in self._trees.values():
            if tree.organization_id == organization_id:
                job, _ = self._revoke_tree(tree, "organization_offboarded")
                jobs.append(job)
        self._organization_jobs[organization_id] = tuple(job.job_id for job in jobs)
        return OffboardingResult(
            organization_id,
            True,
            tuple(self._job_snapshot(job) for job in jobs),
            False,
        )

    def tree_snapshot(self, tree_id: str) -> TreePrivacySnapshot:
        return self._snapshot(self._tree(tree_id))

    def deletion_job(self, job_id: str) -> DeletionJobSnapshot:
        job = self._jobs.get(job_id)
        if job is None:
            raise ValueError("deletion_job_not_found")
        return self._job_snapshot(job)

    def artifact_present(self, tree_id: str, artifact_id: str) -> bool:
        return artifact_id in self._tree(tree_id).artifacts

    def exportable_organization_metadata(self, organization_id: str) -> Mapping[str, Any]:
        organization = self._organization(organization_id)
        return deepcopy(
            {
                "organization_id": organization.organization_id,
                "offboarded": organization.offboarded,
                "version": organization.version,
                "raw_media_included": False,
                "exact_addresses_included": False,
            }
        )
