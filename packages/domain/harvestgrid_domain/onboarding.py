"""Owner, organization, tree, cultivar, coarse-location, and consent registration.

This module implements the deterministic part of ``FR-HG-001``. It exposes typed domain
values, an application service, and adapter ports. Persistence, HTTP, and identity-provider
adapters live outside the domain; :mod:`harvestgrid_domain.onboarding_adapters` provides the
in-memory fixture implementation used by local tests.

Registration never authorizes media collection. Collection additionally requires an approved
camera view (``FR-HG-003``) and current operational consents, which is why every freshly
registered tree reports ``awaiting_camera_view_approval`` even when consents are complete.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

OWNER_TERMS_VERSION = "harvestgrid-owner-terms-v1"
ORGANIZATION_TERMS_VERSION = "harvestgrid-organization-terms-v1"
APPROVED_REVIEW_STATUS = "approved_operational_prior"

COARSE_LOCATION_MIN_PRECISION = 4
COARSE_LOCATION_MAX_PRECISION = 5
GEOHASH_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"

MAX_CONSENT_AGE = timedelta(days=400)
MAX_BLOOM_AGE = timedelta(days=365)
MAX_CLOCK_SKEW = timedelta(minutes=5)
MAX_IDENTIFIER_LENGTH = 200

ONBOARDING_ERROR_CODES = frozenset(
    {
        "actor_not_authorized",
        "bloom_estimate_in_future",
        "bloom_estimate_too_old",
        "coarse_location_invalid",
        "coarse_location_too_imprecise",
        "coarse_location_too_precise",
        "consent_accepted_by_wrong_actor",
        "consent_policy_outdated",
        "consent_required",
        "consent_timestamp_invalid",
        "cultivar_catalog_invalid",
        "cultivar_cohort_mismatch",
        "cultivar_not_supported",
        "cultivar_species_mismatch",
        "duplicate_consent_decision",
        "invalid_field_value",
        "member_authentication_required",
        "member_not_found",
        "member_registration_conflict",
        "organization_not_active",
        "organization_not_found",
        "organization_registration_conflict",
        "record_version_invalid",
        "region_code_invalid",
        "required_field_missing",
        "stale_record_version",
        "terms_not_accepted",
        "terms_version_outdated",
        "timestamp_requires_timezone",
        "tree_not_found",
        "tree_registration_conflict",
    }
)


class OnboardingError(ValueError):
    """Deterministic, machine-readable registration failure."""

    def __init__(self, code: str, *, detail: str = "") -> None:
        if code not in ONBOARDING_ERROR_CODES:
            raise ValueError(f"Unknown onboarding error code: {code}")
        super().__init__(code)
        self.code = code
        self.detail = detail


class OnboardingAuthorizationError(OnboardingError, PermissionError):
    """Registration failure caused by missing actor authority."""


class MembershipRole(StrEnum):
    OWNER = "owner"
    COORDINATOR = "coordinator"
    VOLUNTEER = "volunteer"
    RECIPIENT = "recipient"
    EVALUATOR = "evaluator"


class OrganizationStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    OFFBOARDED = "offboarded"


class ConsentKind(StrEnum):
    CAMERA_COLLECTION = "camera_collection"
    ORGANIZATION_OPERATIONS = "organization_operations"
    CROP_ONLY_TRAINING = "crop_only_training"


class Provenance(StrEnum):
    FIXTURE = "fixture"
    LIVE = "live"


class TreeRegistrationStatus(StrEnum):
    AWAITING_CAMERA_VIEW_APPROVAL = "awaiting_camera_view_approval"
    COLLECTION_PERMITTED = "collection_permitted"
    COLLECTION_BLOCKED = "collection_blocked"


@dataclass(frozen=True)
class ConsentPolicy:
    kind: ConsentKind
    version: str
    required_for_collection: bool
    description: str


CONSENT_POLICIES: tuple[ConsentPolicy, ...] = (
    ConsentPolicy(
        kind=ConsentKind.CAMERA_COLLECTION,
        version="camera-collection-v1",
        required_for_collection=True,
        description="Owner-approved camera collection for tree observations.",
    ),
    ConsentPolicy(
        kind=ConsentKind.ORGANIZATION_OPERATIONS,
        version="organization-operations-v1",
        required_for_collection=True,
        description="Gleaning-organization operating consent, separately revocable.",
    ),
    ConsentPolicy(
        kind=ConsentKind.CROP_ONLY_TRAINING,
        version="crop-only-training-v1",
        required_for_collection=False,
        description="Optional crop-only training export consent; never implied by collection.",
    ),
)

_POLICIES_BY_KIND: Mapping[ConsentKind, ConsentPolicy] = {
    policy.kind: policy for policy in CONSENT_POLICIES
}


def policy_for(kind: ConsentKind) -> ConsentPolicy:
    return _POLICIES_BY_KIND[kind]


@dataclass(frozen=True)
class CoarseLocation:
    """Privacy-bounded location: a low-precision geohash, never exact coordinates."""

    geohash: str
    region_code: str | None = None


@dataclass(frozen=True)
class Cultivar:
    cultivar_id: str
    display_name: str
    species: str
    content_version: str
    review_status: str
    operational_prior_approved: bool


@dataclass(frozen=True)
class ConsentRecord:
    kind: ConsentKind
    policy_version: str
    accepted: bool
    accepted_at: datetime
    accepted_by: str


@dataclass(frozen=True)
class ConsentStatus:
    kind: ConsentKind
    granted: bool
    policy_current: bool
    policy_version: str | None
    accepted_at: datetime | None


@dataclass(frozen=True)
class CollectionGate:
    permitted: bool
    blocked_reasons: tuple[str, ...]


@dataclass(frozen=True)
class OnboardingAuditEvent:
    version: int
    actor_id: str
    action: str
    at: datetime


@dataclass(frozen=True)
class OrganizationRecord:
    organization_id: str
    name: str
    status: OrganizationStatus
    terms_version: str
    registration_fingerprint: str
    created_by: str
    created_at: datetime
    version: int


@dataclass(frozen=True)
class MemberRecord:
    member_id: str
    organization_id: str
    display_name: str
    roles: tuple[MembershipRole, ...]
    authenticated: bool
    terms_version: str
    registration_fingerprint: str
    registered_at: datetime
    version: int


@dataclass(frozen=True)
class TreeRecord:
    tree_id: str
    owner_id: str
    organization_id: str
    cultivar_id: str
    cultivar_display_name: str
    cultivar_content_version: str
    cultivar_operational_prior_approved: bool
    species: str
    coarse_location: CoarseLocation
    bloom_date: date | None
    provenance: Provenance
    consents: tuple[ConsentRecord, ...]
    registration_fingerprint: str
    camera_view_approved: bool
    registered_at: datetime
    version: int
    audit: tuple[OnboardingAuditEvent, ...]


@dataclass(frozen=True)
class OrganizationSnapshot:
    organization_id: str
    name: str
    status: OrganizationStatus
    terms_version: str
    created_by: str
    created_at: datetime
    cultivar_cohort: str | None
    version: int


@dataclass(frozen=True)
class MemberSnapshot:
    member_id: str
    organization_id: str
    display_name: str
    roles: tuple[MembershipRole, ...]
    authenticated: bool
    terms_version: str
    registered_at: datetime
    version: int


@dataclass(frozen=True)
class TreeSnapshot:
    tree_id: str
    owner_id: str
    organization_id: str
    cultivar_id: str
    cultivar_display_name: str
    cultivar_content_version: str
    cultivar_operational_prior_approved: bool
    species: str
    coarse_location: CoarseLocation
    bloom_date: date | None
    provenance: Provenance
    registered_at: datetime
    version: int
    camera_view_approved: bool
    registration_status: TreeRegistrationStatus
    consent_statuses: tuple[ConsentStatus, ...]
    collection: CollectionGate
    forecast_experiment_only: bool

    @property
    def training_export_allowed(self) -> bool:
        """Crop-only training requires its own current consent; collection never implies it."""

        return any(
            status.kind is ConsentKind.CROP_ONLY_TRAINING
            and status.granted
            and status.policy_current
            for status in self.consent_statuses
        )


@dataclass(frozen=True)
class OrganizationRegistration:
    organization_id: str
    name: str
    coordinator_id: str
    terms_version: str
    terms_accepted: bool
    at: datetime


@dataclass(frozen=True)
class OwnerRegistration:
    owner_id: str
    organization_id: str
    display_name: str
    authenticated: bool
    terms_version: str
    terms_accepted: bool
    at: datetime


@dataclass(frozen=True)
class TreeRegistration:
    tree_id: str
    owner_id: str
    organization_id: str
    cultivar_id: str
    species: str
    coarse_location: CoarseLocation
    consents: tuple[ConsentRecord, ...]
    provenance: Provenance
    at: datetime
    bloom_date: date | None = None


@dataclass(frozen=True)
class OrganizationRegistrationResult:
    organization: OrganizationSnapshot
    duplicate: bool


@dataclass(frozen=True)
class OwnerRegistrationResult:
    member: MemberSnapshot
    duplicate: bool


@dataclass(frozen=True)
class TreeRegistrationResult:
    tree: TreeSnapshot
    duplicate: bool


class OnboardingStore(Protocol):
    """Compare-and-set persistence port used by :class:`OnboardingService`."""

    def get_organization(self, organization_id: str) -> OrganizationRecord | None: ...

    def put_organization(self, record: OrganizationRecord) -> None: ...

    def get_member(self, member_id: str) -> MemberRecord | None: ...

    def put_member(self, record: MemberRecord) -> None: ...

    def get_tree(self, tree_id: str) -> TreeRecord | None: ...

    def put_tree(self, record: TreeRecord) -> None: ...

    def list_trees(self, organization_id: str) -> tuple[TreeRecord, ...]: ...


class CultivarCatalog(Protocol):
    """Versioned cultivar content port; unknown cultivars raise ``KeyError``."""

    def list_cultivars(self) -> tuple[Cultivar, ...]: ...

    def require_cultivar(self, cultivar_id: str) -> Cultivar: ...


def _required_text(value: str, field_name: str) -> str:
    text = value.strip()
    if not text:
        raise OnboardingError("required_field_missing", detail=field_name)
    if len(text) > MAX_IDENTIFIER_LENGTH:
        raise OnboardingError("invalid_field_value", detail=field_name)
    return text


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OnboardingError("timestamp_requires_timezone", detail=field_name)
    return value.astimezone(UTC)


def parse_coarse_location(geohash: str, region_code: str | None = None) -> CoarseLocation:
    """Normalize a coarse geohash cell and reject exact coordinates."""

    cell = geohash.strip().lower()
    if not cell:
        raise OnboardingError("required_field_missing", detail="coarse_location")
    if any(character not in GEOHASH_ALPHABET for character in cell):
        raise OnboardingError("coarse_location_invalid", detail="geohash")
    if len(cell) > COARSE_LOCATION_MAX_PRECISION:
        raise OnboardingError("coarse_location_too_precise", detail=f"precision={len(cell)}")
    if len(cell) < COARSE_LOCATION_MIN_PRECISION:
        raise OnboardingError("coarse_location_too_imprecise", detail=f"precision={len(cell)}")
    normalized_region: str | None = None
    if region_code is not None:
        region = region_code.strip().upper()
        if (
            not region
            or len(region) > 8
            or any(not (character.isalnum() or character == "-") for character in region)
            or not region[0].isalpha()
        ):
            raise OnboardingError("region_code_invalid", detail="region_code")
        normalized_region = region
    return CoarseLocation(geohash=cell, region_code=normalized_region)


def validate_bloom_date(value: date | None, *, reference: date) -> date | None:
    if value is None:
        return None
    if value > reference:
        raise OnboardingError("bloom_estimate_in_future", detail=value.isoformat())
    if reference - value > MAX_BLOOM_AGE:
        raise OnboardingError("bloom_estimate_too_old", detail=value.isoformat())
    return value


def _validate_consents(
    records: Iterable[ConsentRecord], *, owner_id: str, registered_at: datetime
) -> tuple[ConsentRecord, ...]:
    seen: dict[ConsentKind, ConsentRecord] = {}
    for record in records:
        if record.kind in seen:
            raise OnboardingError("duplicate_consent_decision", detail=record.kind.value)
        policy = policy_for(record.kind)
        if record.policy_version != policy.version:
            raise OnboardingError("consent_policy_outdated", detail=record.kind.value)
        if record.accepted_by.strip() != owner_id:
            raise OnboardingError("consent_accepted_by_wrong_actor", detail=record.kind.value)
        accepted_at = _aware(record.accepted_at, "consent.accepted_at")
        if (
            accepted_at > registered_at + MAX_CLOCK_SKEW
            or accepted_at < registered_at - MAX_CONSENT_AGE
        ):
            raise OnboardingError("consent_timestamp_invalid", detail=record.kind.value)
        if policy.required_for_collection and not record.accepted:
            raise OnboardingError("consent_required", detail=record.kind.value)
        seen[record.kind] = replace(record, accepted_at=accepted_at)
    missing = [
        policy.kind.value
        for policy in CONSENT_POLICIES
        if policy.required_for_collection and policy.kind not in seen
    ]
    if missing:
        raise OnboardingError("consent_required", detail=",".join(sorted(missing)))
    return tuple(seen[policy.kind] for policy in CONSENT_POLICIES if policy.kind in seen)


def evaluate_consents(records: Iterable[ConsentRecord]) -> tuple[ConsentStatus, ...]:
    by_kind = {record.kind: record for record in records}
    statuses: list[ConsentStatus] = []
    for policy in CONSENT_POLICIES:
        record = by_kind.get(policy.kind)
        statuses.append(
            ConsentStatus(
                kind=policy.kind,
                granted=record.accepted if record is not None else False,
                policy_current=record is not None and record.policy_version == policy.version,
                policy_version=record.policy_version if record is not None else None,
                accepted_at=record.accepted_at if record is not None else None,
            )
        )
    return tuple(statuses)


def evaluate_collection_gate(
    record: TreeRecord, organization_status: OrganizationStatus
) -> CollectionGate:
    reasons: list[str] = []
    if organization_status is not OrganizationStatus.ACTIVE:
        reasons.append("organization_not_active")
    if not record.camera_view_approved:
        reasons.append("camera_view_not_approved")
    for status in evaluate_consents(record.consents):
        if not policy_for(status.kind).required_for_collection:
            continue
        if not status.granted:
            reasons.append(f"consent_missing:{status.kind.value}")
        elif not status.policy_current:
            reasons.append(f"consent_policy_outdated:{status.kind.value}")
    return CollectionGate(permitted=not reasons, blocked_reasons=tuple(sorted(reasons)))


def _fingerprint(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def tree_registration_fingerprint(registration: TreeRegistration) -> str:
    """Stable request hash used for idempotent replay detection.

    Timestamps are excluded so an identical retry with a new clock value stays a duplicate.
    """

    return _fingerprint(
        {
            "tree_id": registration.tree_id.strip(),
            "owner_id": registration.owner_id.strip(),
            "organization_id": registration.organization_id.strip(),
            "cultivar_id": registration.cultivar_id.strip(),
            "species": registration.species.strip(),
            "geohash": registration.coarse_location.geohash.strip().lower(),
            "region_code": (
                registration.coarse_location.region_code.strip().upper()
                if registration.coarse_location.region_code is not None
                else None
            ),
            "bloom_date": (
                registration.bloom_date.isoformat() if registration.bloom_date is not None else None
            ),
            "provenance": registration.provenance.value,
            "consents": sorted(
                (
                    {
                        "kind": record.kind.value,
                        "policy_version": record.policy_version,
                        "accepted": record.accepted,
                        "accepted_by": record.accepted_by.strip(),
                    }
                    for record in registration.consents
                ),
                key=lambda item: str(item["kind"]),
            ),
        }
    )


def tree_snapshot_document(snapshot: TreeSnapshot) -> dict[str, Any]:
    """Serialize only coarse, non-media tree registration data."""

    return {
        "contract_version": "tree-registration-v1",
        "tree_id": snapshot.tree_id,
        "owner_id": snapshot.owner_id,
        "organization_id": snapshot.organization_id,
        "cultivar": {
            "cultivar_id": snapshot.cultivar_id,
            "display_name": snapshot.cultivar_display_name,
            "content_version": snapshot.cultivar_content_version,
            "species": snapshot.species,
            "operational_prior_approved": snapshot.cultivar_operational_prior_approved,
        },
        "coarse_location": {
            "geohash": snapshot.coarse_location.geohash,
            "region_code": snapshot.coarse_location.region_code,
        },
        "bloom_date": snapshot.bloom_date.isoformat() if snapshot.bloom_date else None,
        "provenance": snapshot.provenance.value,
        "registered_at": snapshot.registered_at.isoformat(),
        "version": snapshot.version,
        "camera_view_approved": snapshot.camera_view_approved,
        "registration_status": snapshot.registration_status.value,
        "consents": [
            {
                "kind": status.kind.value,
                "granted": status.granted,
                "policy_current": status.policy_current,
                "policy_version": status.policy_version,
            }
            for status in snapshot.consent_statuses
        ],
        "collection": {
            "permitted": snapshot.collection.permitted,
            "blocked_reasons": list(snapshot.collection.blocked_reasons),
        },
        "forecast_experiment_only": snapshot.forecast_experiment_only,
        "human_readiness_review_required": True,
        "internal_ripeness_inferred": False,
        "food_safety_inferred": False,
        "exact_address_included": False,
        "raw_media_included": False,
    }


class OnboardingService:
    """Registration service for owners, organizations, trees, cultivars, and consents."""

    def __init__(self, *, store: OnboardingStore, cultivars: CultivarCatalog) -> None:
        self._store = store
        self._cultivars = cultivars

    # -- organization -------------------------------------------------------------------
    def register_organization(
        self, registration: OrganizationRegistration
    ) -> OrganizationRegistrationResult:
        organization_id = _required_text(registration.organization_id, "organization_id")
        name = _required_text(registration.name, "name")
        coordinator_id = _required_text(registration.coordinator_id, "coordinator_id")
        terms_version = _required_text(registration.terms_version, "terms_version")
        created_at = _aware(registration.at, "organization.at")
        if not registration.terms_accepted:
            raise OnboardingError("terms_not_accepted", detail="organization_terms")
        if terms_version != ORGANIZATION_TERMS_VERSION:
            raise OnboardingError("terms_version_outdated", detail="organization_terms")

        fingerprint = _fingerprint(
            {
                "organization_id": organization_id,
                "name": name,
                "coordinator_id": coordinator_id,
                "terms_version": terms_version,
                "terms_accepted": registration.terms_accepted,
            }
        )
        existing = self._store.get_organization(organization_id)
        if existing is not None:
            if existing.registration_fingerprint != fingerprint:
                raise OnboardingError("organization_registration_conflict", detail=organization_id)
            return OrganizationRegistrationResult(
                organization=self._organization_snapshot(existing), duplicate=True
            )

        if self._store.get_member(coordinator_id) is not None:
            raise OnboardingError("member_registration_conflict", detail=coordinator_id)
        record = OrganizationRecord(
            organization_id=organization_id,
            name=name,
            status=OrganizationStatus.ACTIVE,
            terms_version=terms_version,
            registration_fingerprint=fingerprint,
            created_by=coordinator_id,
            created_at=created_at,
            version=0,
        )
        coordinator = MemberRecord(
            member_id=coordinator_id,
            organization_id=organization_id,
            display_name=coordinator_id,
            roles=(MembershipRole.COORDINATOR,),
            authenticated=True,
            terms_version=terms_version,
            registration_fingerprint=_fingerprint(
                {
                    "member_id": coordinator_id,
                    "organization_id": organization_id,
                    "roles": [MembershipRole.COORDINATOR.value],
                    "terms_version": terms_version,
                }
            ),
            registered_at=created_at,
            version=0,
        )
        self._store.put_organization(record)
        self._store.put_member(coordinator)
        return OrganizationRegistrationResult(
            organization=self._organization_snapshot(record), duplicate=False
        )

    def get_organization(self, *, organization_id: str, actor_id: str) -> OrganizationSnapshot:
        identifier = _required_text(organization_id, "organization_id")
        record = self._store.get_organization(identifier)
        if record is None:
            raise OnboardingError("organization_not_found", detail=identifier)
        self._require_member(actor_id, organization_id=identifier)
        return self._organization_snapshot(record)

    def _organization_snapshot(self, record: OrganizationRecord) -> OrganizationSnapshot:
        trees = self._store.list_trees(record.organization_id)
        return OrganizationSnapshot(
            organization_id=record.organization_id,
            name=record.name,
            status=record.status,
            terms_version=record.terms_version,
            created_by=record.created_by,
            created_at=record.created_at,
            cultivar_cohort=trees[0].cultivar_id if trees else None,
            version=record.version,
        )

    # -- members ------------------------------------------------------------------------
    def register_owner(self, registration: OwnerRegistration) -> OwnerRegistrationResult:
        owner_id = _required_text(registration.owner_id, "owner_id")
        organization_id = _required_text(registration.organization_id, "organization_id")
        display_name = _required_text(registration.display_name, "display_name")
        terms_version = _required_text(registration.terms_version, "terms_version")
        registered_at = _aware(registration.at, "owner.at")
        if not registration.terms_accepted:
            raise OnboardingError("terms_not_accepted", detail="owner_terms")
        if terms_version != OWNER_TERMS_VERSION:
            raise OnboardingError("terms_version_outdated", detail="owner_terms")

        fingerprint = _fingerprint(
            {
                "member_id": owner_id,
                "organization_id": organization_id,
                "display_name": display_name,
                "roles": [MembershipRole.OWNER.value],
                "authenticated": registration.authenticated,
                "terms_version": terms_version,
                "terms_accepted": registration.terms_accepted,
            }
        )
        existing = self._store.get_member(owner_id)
        if existing is not None:
            if existing.registration_fingerprint != fingerprint:
                raise OnboardingError("member_registration_conflict", detail=owner_id)
            return OwnerRegistrationResult(member=self._member_snapshot(existing), duplicate=True)

        if not registration.authenticated:
            raise OnboardingError("member_authentication_required", detail=owner_id)
        self._require_active_organization(organization_id)
        record = MemberRecord(
            member_id=owner_id,
            organization_id=organization_id,
            display_name=display_name,
            roles=(MembershipRole.OWNER,),
            authenticated=True,
            terms_version=terms_version,
            registration_fingerprint=fingerprint,
            registered_at=registered_at,
            version=0,
        )
        self._store.put_member(record)
        return OwnerRegistrationResult(member=self._member_snapshot(record), duplicate=False)

    def get_member(self, *, member_id: str, actor_id: str) -> MemberSnapshot:
        identifier = _required_text(member_id, "member_id")
        record = self._store.get_member(identifier)
        if record is None:
            raise OnboardingError("member_not_found", detail=identifier)
        actor = self._store.get_member(_required_text(actor_id, "actor_id"))
        if actor is None or actor.organization_id != record.organization_id:
            raise OnboardingAuthorizationError("actor_not_authorized", detail=identifier)
        return self._member_snapshot(record)

    def _member_snapshot(self, record: MemberRecord) -> MemberSnapshot:
        return MemberSnapshot(
            member_id=record.member_id,
            organization_id=record.organization_id,
            display_name=record.display_name,
            roles=record.roles,
            authenticated=record.authenticated,
            terms_version=record.terms_version,
            registered_at=record.registered_at,
            version=record.version,
        )

    # -- trees --------------------------------------------------------------------------
    def register_tree(self, registration: TreeRegistration) -> TreeRegistrationResult:
        tree_id = _required_text(registration.tree_id, "tree_id")
        owner_id = _required_text(registration.owner_id, "owner_id")
        organization_id = _required_text(registration.organization_id, "organization_id")
        cultivar_id = _required_text(registration.cultivar_id, "cultivar_id")
        species = _required_text(registration.species, "species")
        registered_at = _aware(registration.at, "tree.at")
        coarse_location = parse_coarse_location(
            registration.coarse_location.geohash, registration.coarse_location.region_code
        )
        bloom_date = validate_bloom_date(registration.bloom_date, reference=registered_at.date())
        consents = _validate_consents(
            registration.consents, owner_id=owner_id, registered_at=registered_at
        )
        try:
            cultivar = self._cultivars.require_cultivar(cultivar_id)
        except KeyError:
            raise OnboardingError("cultivar_not_supported", detail=cultivar_id) from None
        if cultivar.species != species:
            raise OnboardingError("cultivar_species_mismatch", detail=cultivar_id)

        fingerprint = tree_registration_fingerprint(registration)
        existing = self._store.get_tree(tree_id)
        if existing is not None:
            if existing.registration_fingerprint != fingerprint:
                raise OnboardingError("tree_registration_conflict", detail=tree_id)
            return TreeRegistrationResult(tree=self._tree_snapshot(existing), duplicate=True)

        organization = self._require_active_organization(organization_id)
        owner = self._store.get_member(owner_id)
        if owner is None or owner.organization_id != organization_id:
            raise OnboardingAuthorizationError("actor_not_authorized", detail=owner_id)
        if MembershipRole.OWNER not in owner.roles:
            raise OnboardingAuthorizationError("actor_not_authorized", detail=owner_id)
        if not owner.authenticated:
            raise OnboardingError("member_authentication_required", detail=owner_id)
        cohort = {record.cultivar_id for record in self._store.list_trees(organization_id)}
        if cohort and cultivar_id not in cohort:
            raise OnboardingError("cultivar_cohort_mismatch", detail=cultivar_id)
        if len(cohort) > 1:
            raise OnboardingError("cultivar_cohort_mismatch", detail=organization_id)

        record = TreeRecord(
            tree_id=tree_id,
            owner_id=owner_id,
            organization_id=organization_id,
            cultivar_id=cultivar.cultivar_id,
            cultivar_display_name=cultivar.display_name,
            cultivar_content_version=cultivar.content_version,
            cultivar_operational_prior_approved=cultivar.operational_prior_approved,
            species=cultivar.species,
            coarse_location=coarse_location,
            bloom_date=bloom_date,
            provenance=registration.provenance,
            consents=consents,
            registration_fingerprint=fingerprint,
            camera_view_approved=False,
            registered_at=registered_at,
            version=0,
            audit=(
                OnboardingAuditEvent(
                    version=0,
                    actor_id=owner_id,
                    action="tree_registered",
                    at=registered_at,
                ),
            ),
        )
        self._store.put_tree(record)
        return TreeRegistrationResult(
            tree=self._tree_snapshot(record, organization=organization), duplicate=False
        )

    def get_tree(self, *, tree_id: str, actor_id: str) -> TreeSnapshot:
        identifier = _required_text(tree_id, "tree_id")
        record = self._store.get_tree(identifier)
        if record is None:
            raise OnboardingError("tree_not_found", detail=identifier)
        self._require_member(actor_id, organization_id=record.organization_id)
        return self._tree_snapshot(record)

    def _tree_snapshot(
        self, record: TreeRecord, *, organization: OrganizationRecord | None = None
    ) -> TreeSnapshot:
        org = organization or self._store.get_organization(record.organization_id)
        if org is None:
            raise OnboardingError("organization_not_found", detail=record.organization_id)
        gate = evaluate_collection_gate(record, org.status)
        if gate.permitted:
            status = TreeRegistrationStatus.COLLECTION_PERMITTED
        elif not record.camera_view_approved:
            status = TreeRegistrationStatus.AWAITING_CAMERA_VIEW_APPROVAL
        else:
            status = TreeRegistrationStatus.COLLECTION_BLOCKED
        return TreeSnapshot(
            tree_id=record.tree_id,
            owner_id=record.owner_id,
            organization_id=record.organization_id,
            cultivar_id=record.cultivar_id,
            cultivar_display_name=record.cultivar_display_name,
            cultivar_content_version=record.cultivar_content_version,
            cultivar_operational_prior_approved=(record.cultivar_operational_prior_approved),
            species=record.species,
            coarse_location=record.coarse_location,
            bloom_date=record.bloom_date,
            provenance=record.provenance,
            registered_at=record.registered_at,
            version=record.version,
            camera_view_approved=record.camera_view_approved,
            registration_status=status,
            consent_statuses=evaluate_consents(record.consents),
            collection=gate,
            forecast_experiment_only=not record.cultivar_operational_prior_approved,
        )

    # -- shared checks -------------------------------------------------------------------
    def _require_member(self, actor_id: str, *, organization_id: str) -> MemberRecord:
        actor = self._store.get_member(_required_text(actor_id, "actor_id"))
        if actor is None or actor.organization_id != organization_id:
            raise OnboardingAuthorizationError("actor_not_authorized", detail=actor_id)
        return actor

    def _require_active_organization(self, organization_id: str) -> OrganizationRecord:
        organization = self._store.get_organization(organization_id)
        if organization is None:
            raise OnboardingError("organization_not_found", detail=organization_id)
        if organization.status is not OrganizationStatus.ACTIVE:
            raise OnboardingAuthorizationError("organization_not_active", detail=organization_id)
        return organization
