from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from harvestgrid_domain import (
    CONSENT_POLICIES,
    ORGANIZATION_TERMS_VERSION,
    OWNER_TERMS_VERSION,
    CoarseLocation,
    ConsentKind,
    ConsentRecord,
    Cultivar,
    InMemoryOnboardingStore,
    JsonCultivarCatalog,
    MemberRecord,
    MembershipRole,
    OnboardingAuthorizationError,
    OnboardingError,
    OnboardingService,
    OrganizationRegistration,
    OrganizationStatus,
    OwnerRegistration,
    Provenance,
    TreeRecord,
    TreeRegistration,
    TreeRegistrationStatus,
    evaluate_collection_gate,
    evaluate_consents,
    parse_coarse_location,
    policy_for,
    tree_registration_fingerprint,
    tree_snapshot_document,
)

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
CULTIVAR_ID = "european_plum_v1"
SPECIES = "prunus_domestica"


def canonical_catalog() -> JsonCultivarCatalog:
    return JsonCultivarCatalog.from_directory(ROOT / "content" / "cultivars")


def service(catalog: JsonCultivarCatalog | None = None) -> OnboardingService:
    return OnboardingService(
        store=InMemoryOnboardingStore(), cultivars=catalog or canonical_catalog()
    )


def consents(
    *,
    owner_id: str = "owner-fixture",
    at: datetime = T0,
    accepted: bool = True,
    policy_version: str | None = None,
    kinds: tuple[ConsentKind, ...] = (
        ConsentKind.CAMERA_COLLECTION,
        ConsentKind.ORGANIZATION_OPERATIONS,
    ),
) -> tuple[ConsentRecord, ...]:
    records = []
    for kind in kinds:
        policy = policy_for(kind)
        records.append(
            ConsentRecord(
                kind=kind,
                policy_version=policy_version or policy.version,
                accepted=accepted,
                accepted_at=at,
                accepted_by=owner_id,
            )
        )
    return tuple(records)


def register_organization(
    target: OnboardingService,
    *,
    organization_id: str = "org-fixture",
    coordinator_id: str = "coordinator-fixture",
    name: str = "Fixture gleaning organization",
) -> None:
    target.register_organization(
        OrganizationRegistration(
            organization_id=organization_id,
            name=name,
            coordinator_id=coordinator_id,
            terms_version=ORGANIZATION_TERMS_VERSION,
            terms_accepted=True,
            at=T0,
        )
    )


def register_owner(
    target: OnboardingService,
    *,
    owner_id: str = "owner-fixture",
    organization_id: str = "org-fixture",
    authenticated: bool = True,
    display_name: str = "Fixture owner",
    terms_version: str = OWNER_TERMS_VERSION,
    terms_accepted: bool = True,
    at: datetime = T0,
) -> None:
    target.register_owner(
        OwnerRegistration(
            owner_id=owner_id,
            organization_id=organization_id,
            display_name=display_name,
            authenticated=authenticated,
            terms_version=terms_version,
            terms_accepted=terms_accepted,
            at=at,
        )
    )


def tree_registration(
    *,
    tree_id: str = "tree-fixture",
    owner_id: str = "owner-fixture",
    organization_id: str = "org-fixture",
    cultivar_id: str = CULTIVAR_ID,
    species: str = SPECIES,
    geohash: str = "u173z",
    region_code: str | None = "NL-ZH",
    consent_records: tuple[ConsentRecord, ...] | None = None,
    provenance: Provenance = Provenance.FIXTURE,
    at: datetime = T0,
    bloom_date: date | None = None,
) -> TreeRegistration:
    return TreeRegistration(
        tree_id=tree_id,
        owner_id=owner_id,
        organization_id=organization_id,
        cultivar_id=cultivar_id,
        species=species,
        coarse_location=CoarseLocation(geohash=geohash, region_code=region_code),
        consents=consent_records if consent_records is not None else consents(),
        provenance=provenance,
        at=at,
        bloom_date=bloom_date,
    )


def onboarded_service(
    catalog: JsonCultivarCatalog | None = None,
) -> OnboardingService:
    target = service(catalog)
    register_organization(target)
    register_owner(target)
    return target


def tree_record(target: OnboardingService, tree_id: str = "tree-fixture") -> TreeRecord:
    record = target._store.get_tree(tree_id)  # noqa: SLF001 - fixture store inspection
    assert record is not None
    return record


def test_full_registration_records_owner_organization_tree_and_consents() -> None:
    target = onboarded_service()
    result = target.register_tree(tree_registration())
    snapshot = result.tree

    assert result.duplicate is False
    assert snapshot.tree_id == "tree-fixture"
    assert snapshot.owner_id == "owner-fixture"
    assert snapshot.organization_id == "org-fixture"
    assert snapshot.cultivar_id == CULTIVAR_ID
    assert snapshot.cultivar_content_version == CULTIVAR_ID
    assert snapshot.species == SPECIES
    assert snapshot.coarse_location == CoarseLocation(geohash="u173z", region_code="NL-ZH")
    assert snapshot.provenance is Provenance.FIXTURE
    assert snapshot.version == 0
    assert snapshot.registered_at == T0
    assert [event.action for event in tree_record(target).audit] == ["tree_registered"]

    statuses = {status.kind: status for status in snapshot.consent_statuses}
    assert statuses[ConsentKind.CAMERA_COLLECTION].granted is True
    assert statuses[ConsentKind.ORGANIZATION_OPERATIONS].granted is True
    assert statuses[ConsentKind.ORGANIZATION_OPERATIONS].policy_current is True
    assert statuses[ConsentKind.CROP_ONLY_TRAINING].granted is False
    assert statuses[ConsentKind.CROP_ONLY_TRAINING].policy_version is None
    assert snapshot.training_export_allowed is False

    assert snapshot.camera_view_approved is False
    assert snapshot.registration_status is TreeRegistrationStatus.AWAITING_CAMERA_VIEW_APPROVAL
    assert snapshot.collection.permitted is False
    assert snapshot.collection.blocked_reasons == ("camera_view_not_approved",)
    assert snapshot.cultivar_operational_prior_approved is False
    assert snapshot.forecast_experiment_only is True

    organization = target.get_organization(
        organization_id="org-fixture", actor_id="coordinator-fixture"
    )
    assert organization.status is OrganizationStatus.ACTIVE
    assert organization.terms_version == ORGANIZATION_TERMS_VERSION
    assert organization.cultivar_cohort == CULTIVAR_ID
    assert organization.version == 0

    member = target.get_member(member_id="owner-fixture", actor_id="owner-fixture")
    assert member.roles == (MembershipRole.OWNER,)
    assert member.authenticated is True
    assert target.get_tree(tree_id="tree-fixture", actor_id="coordinator-fixture").tree_id == (
        "tree-fixture"
    )


def test_registration_is_idempotent_for_identical_retries() -> None:
    target = onboarded_service()
    first = target.register_tree(tree_registration(bloom_date=date(2026, 4, 10)))
    retry_at = T0 + timedelta(hours=3)
    replay = target.register_tree(tree_registration(at=retry_at, bloom_date=date(2026, 4, 10)))

    assert first.duplicate is False
    assert replay.duplicate is True
    assert replay.tree.registered_at == T0
    assert replay.tree.version == 0
    assert len(tree_record(target).audit) == 1

    organization_replay = target.register_organization(
        OrganizationRegistration(
            organization_id="org-fixture",
            name="Fixture gleaning organization",
            coordinator_id="coordinator-fixture",
            terms_version=ORGANIZATION_TERMS_VERSION,
            terms_accepted=True,
            at=retry_at,
        )
    )
    assert organization_replay.duplicate is True
    assert organization_replay.organization.version == 0

    owner_replay = target.register_owner(
        OwnerRegistration(
            owner_id="owner-fixture",
            organization_id="org-fixture",
            display_name="Fixture owner",
            authenticated=True,
            terms_version=OWNER_TERMS_VERSION,
            terms_accepted=True,
            at=retry_at,
        )
    )
    assert owner_replay.duplicate is True
    assert owner_replay.member.version == 0

    # Bloom date is part of the request hash: a materially different request conflicts.
    with pytest.raises(OnboardingError, match="tree_registration_conflict"):
        target.register_tree(tree_registration(geohash="u1740"))


def test_conflicting_registration_retries_are_rejected() -> None:
    target = onboarded_service()
    target.register_tree(tree_registration())

    with pytest.raises(OnboardingError, match="organization_registration_conflict"):
        target.register_organization(
            OrganizationRegistration(
                organization_id="org-fixture",
                name="Different organization",
                coordinator_id="coordinator-fixture",
                terms_version=ORGANIZATION_TERMS_VERSION,
                terms_accepted=True,
                at=T0,
            )
        )
    with pytest.raises(OnboardingError, match="member_registration_conflict"):
        register_owner(target, display_name="Different owner")
    with pytest.raises(OnboardingError, match="member_registration_conflict"):
        target.register_organization(
            OrganizationRegistration(
                organization_id="org-second",
                name="Second organization",
                coordinator_id="owner-fixture",
                terms_version=ORGANIZATION_TERMS_VERSION,
                terms_accepted=True,
                at=T0,
            )
        )


def test_owner_registration_requires_authentication_and_current_terms() -> None:
    target = service()
    register_organization(target)

    with pytest.raises(OnboardingError, match="member_authentication_required"):
        register_owner(target, authenticated=False)
    with pytest.raises(OnboardingError, match="terms_not_accepted"):
        register_owner(target, terms_accepted=False)
    with pytest.raises(OnboardingError, match="terms_version_outdated"):
        register_owner(target, terms_version="owner-terms-v0")
    with pytest.raises(OnboardingError, match="organization_not_found"):
        register_owner(target, organization_id="org-unknown")
    with pytest.raises(OnboardingError, match="required_field_missing"):
        register_owner(target, display_name="   ")
    with pytest.raises(OnboardingError, match="invalid_field_value"):
        register_owner(target, display_name="x" * 201)
    with pytest.raises(OnboardingError, match="timestamp_requires_timezone"):
        target.register_owner(
            OwnerRegistration(
                owner_id="owner-fixture",
                organization_id="org-fixture",
                display_name="Fixture owner",
                authenticated=True,
                terms_version=OWNER_TERMS_VERSION,
                terms_accepted=True,
                at=datetime(2026, 10, 23, 12, 0),
            )
        )
    with pytest.raises(OnboardingError, match="terms_not_accepted"):
        target.register_organization(
            OrganizationRegistration(
                organization_id="org-terms",
                name="Fixture organization",
                coordinator_id="coordinator-terms",
                terms_version=ORGANIZATION_TERMS_VERSION,
                terms_accepted=False,
                at=T0,
            )
        )
    with pytest.raises(OnboardingError, match="terms_version_outdated"):
        target.register_organization(
            OrganizationRegistration(
                organization_id="org-terms",
                name="Fixture organization",
                coordinator_id="coordinator-terms",
                terms_version="org-terms-v0",
                terms_accepted=True,
                at=T0,
            )
        )


def test_registration_rejects_unknown_suspended_or_offboarded_organizations() -> None:
    target = onboarded_service()
    with pytest.raises(OnboardingError, match="organization_not_found"):
        target.register_tree(tree_registration(organization_id="org-unknown"))

    store = target._store  # noqa: SLF001 - fixture store inspection
    organization = store.get_organization("org-fixture")
    assert organization is not None
    store.put_organization(replace(organization, status=OrganizationStatus.SUSPENDED, version=1))
    with pytest.raises(PermissionError, match="organization_not_active"):
        target.register_tree(tree_registration())
    with pytest.raises(PermissionError, match="organization_not_active"):
        register_owner(target, owner_id="owner-second")


def test_tree_registration_requires_an_owner_member_of_the_organization() -> None:
    target = onboarded_service()
    target.register_tree(tree_registration())
    with pytest.raises(PermissionError, match="actor_not_authorized"):
        target.register_tree(
            tree_registration(
                tree_id="tree-unknown-owner",
                owner_id="owner-unknown",
                consent_records=consents(owner_id="owner-unknown"),
            )
        )

    store = target._store  # noqa: SLF001 - fixture store inspection
    store.put_member(
        MemberRecord(
            member_id="volunteer-fixture",
            organization_id="org-fixture",
            display_name="Fixture volunteer",
            roles=(MembershipRole.VOLUNTEER,),
            authenticated=True,
            terms_version=OWNER_TERMS_VERSION,
            registration_fingerprint="fixture",
            registered_at=T0,
            version=0,
        )
    )
    with pytest.raises(PermissionError, match="actor_not_authorized"):
        target.register_tree(
            tree_registration(
                tree_id="tree-volunteer",
                owner_id="volunteer-fixture",
                consent_records=consents(owner_id="volunteer-fixture"),
            )
        )

    with pytest.raises(OnboardingError, match="tree_not_found"):
        target.get_tree(tree_id="tree-unknown", actor_id="owner-fixture")
    with pytest.raises(PermissionError, match="actor_not_authorized"):
        target.get_tree(tree_id="tree-fixture", actor_id="stranger-fixture")
    with pytest.raises(OnboardingError, match="member_not_found"):
        target.get_member(member_id="member-unknown", actor_id="owner-fixture")
    with pytest.raises(PermissionError, match="actor_not_authorized"):
        target.get_member(member_id="owner-fixture", actor_id="stranger-fixture")

    register_organization(target, organization_id="org-other", coordinator_id="coordinator-other")
    register_owner(target, owner_id="owner-other", organization_id="org-other")
    with pytest.raises(PermissionError, match="actor_not_authorized"):
        target.get_tree(tree_id="tree-fixture", actor_id="owner-other")


def test_coarse_location_privileges_and_normalization() -> None:
    assert parse_coarse_location("u173z").region_code is None
    location = parse_coarse_location("  U173Z ", " nl-zh ")
    assert location.geohash == "u173z"
    assert location.region_code == "NL-ZH"

    with pytest.raises(OnboardingError, match="required_field_missing"):
        parse_coarse_location("   ")
    with pytest.raises(OnboardingError, match="coarse_location_invalid"):
        parse_coarse_location("52.0067")
    with pytest.raises(OnboardingError, match="coarse_location_invalid"):
        parse_coarse_location("u173z,4.3556")
    with pytest.raises(OnboardingError, match="coarse_location_too_precise"):
        parse_coarse_location("u173zp")
    with pytest.raises(OnboardingError, match="coarse_location_too_imprecise"):
        parse_coarse_location("u17")
    with pytest.raises(OnboardingError, match="region_code_invalid"):
        parse_coarse_location("u173z", "12")
    with pytest.raises(OnboardingError, match="region_code_invalid"):
        parse_coarse_location("u173z", "NL ZH")
    with pytest.raises(OnboardingError, match="region_code_invalid"):
        parse_coarse_location("u173z", "")
    with pytest.raises(OnboardingError, match="region_code_invalid"):
        parse_coarse_location("u173z", "NL-ZUID-HOLLAND")

    target = onboarded_service()
    snapshot = target.register_tree(tree_registration(geohash="U173Z", region_code="nl-zh")).tree
    assert snapshot.coarse_location == CoarseLocation(geohash="u173z", region_code="NL-ZH")
    exact_coordinates = tree_snapshot_document(snapshot)
    assert "latitude" not in exact_coordinates
    assert "longitude" not in exact_coordinates


def test_tree_registration_validates_bloom_estimate() -> None:
    target = onboarded_service()
    snapshot = target.register_tree(tree_registration(bloom_date=date(2026, 4, 10))).tree
    assert snapshot.bloom_date == date(2026, 4, 10)

    with pytest.raises(OnboardingError, match="bloom_estimate_in_future"):
        target.register_tree(tree_registration(tree_id="tree-future", bloom_date=date(2026, 11, 1)))
    with pytest.raises(OnboardingError, match="bloom_estimate_too_old"):
        target.register_tree(tree_registration(tree_id="tree-old", bloom_date=date(2025, 1, 1)))


def test_tree_registration_requires_explicit_current_consents() -> None:
    target = onboarded_service()
    camera_only = consents(kinds=(ConsentKind.CAMERA_COLLECTION,))

    with pytest.raises(OnboardingError, match="consent_required"):
        target.register_tree(tree_registration(consent_records=camera_only))
    with pytest.raises(OnboardingError, match="consent_required"):
        target.register_tree(tree_registration(consent_records=()))
    with pytest.raises(OnboardingError, match="consent_policy_outdated"):
        target.register_tree(
            tree_registration(consent_records=consents(policy_version="camera-collection-v0"))
        )
    with pytest.raises(OnboardingError, match="consent_accepted_by_wrong_actor"):
        target.register_tree(
            tree_registration(
                consent_records=tuple(
                    replace(record, accepted_by="coordinator-fixture") for record in consents()
                )
            )
        )
    with pytest.raises(OnboardingError, match="consent_timestamp_invalid"):
        target.register_tree(
            tree_registration(
                consent_records=tuple(
                    replace(record, accepted_at=T0 + timedelta(hours=1)) for record in consents()
                )
            )
        )
    with pytest.raises(OnboardingError, match="consent_timestamp_invalid"):
        target.register_tree(
            tree_registration(
                consent_records=tuple(
                    replace(record, accepted_at=T0 - timedelta(days=401)) for record in consents()
                )
            )
        )
    with pytest.raises(OnboardingError, match="timestamp_requires_timezone"):
        target.register_tree(
            tree_registration(
                consent_records=tuple(
                    replace(record, accepted_at=datetime(2026, 10, 23, 12, 0))
                    for record in consents()
                )
            )
        )
    with pytest.raises(OnboardingError, match="duplicate_consent_decision"):
        target.register_tree(
            tree_registration(
                consent_records=(*consents(), consents()[0]),
            )
        )
    with pytest.raises(OnboardingError, match="consent_required"):
        target.register_tree(
            tree_registration(
                consent_records=tuple(replace(record, accepted=False) for record in consents())
            )
        )
    with pytest.raises(OnboardingError, match="timestamp_requires_timezone"):
        target.register_tree(tree_registration(at=datetime(2026, 10, 23, 12, 0)))
    assert target._store.get_tree("tree-fixture") is None  # noqa: SLF001


def test_organization_reads_require_membership_and_existence() -> None:
    target = onboarded_service()
    with pytest.raises(OnboardingError, match="organization_not_found"):
        target.get_organization(organization_id="org-unknown", actor_id="owner-fixture")
    with pytest.raises(PermissionError, match="actor_not_authorized"):
        target.get_organization(organization_id="org-fixture", actor_id="stranger-fixture")

    store = target._store  # noqa: SLF001 - fixture store inspection
    owner = store.get_member("owner-fixture")
    assert owner is not None
    store.put_member(replace(owner, authenticated=False, version=1))
    with pytest.raises(OnboardingError, match="member_authentication_required"):
        target.register_tree(tree_registration(tree_id="tree-unauthenticated"))


def test_optional_training_consent_is_separately_recorded_and_refusable() -> None:
    target = onboarded_service()
    granted = target.register_tree(
        tree_registration(
            tree_id="tree-training",
            consent_records=consents(
                kinds=(
                    ConsentKind.CAMERA_COLLECTION,
                    ConsentKind.ORGANIZATION_OPERATIONS,
                    ConsentKind.CROP_ONLY_TRAINING,
                )
            ),
        )
    ).tree
    assert granted.training_export_allowed is True
    assert granted.collection.blocked_reasons == ("camera_view_not_approved",)

    declined = target.register_tree(
        tree_registration(
            tree_id="tree-no-training",
            consent_records=(
                *consents(),
                ConsentRecord(
                    kind=ConsentKind.CROP_ONLY_TRAINING,
                    policy_version=policy_for(ConsentKind.CROP_ONLY_TRAINING).version,
                    accepted=False,
                    accepted_at=T0,
                    accepted_by="owner-fixture",
                ),
            ),
        )
    ).tree
    assert declined.training_export_allowed is False
    training = next(
        status
        for status in declined.consent_statuses
        if status.kind is ConsentKind.CROP_ONLY_TRAINING
    )
    assert training.policy_current is True
    assert training.granted is False


def test_cultivar_support_cohort_and_species_are_enforced() -> None:
    second_cultivar = Cultivar(
        cultivar_id="second_plum_v1",
        display_name="Second plum",
        species="prunus_domestica",
        content_version="second_plum_v1",
        review_status="approved_operational_prior",
        operational_prior_approved=True,
    )
    base = canonical_catalog().require_cultivar(CULTIVAR_ID)
    catalog = JsonCultivarCatalog(
        [
            _document(base),
            _document(second_cultivar),
        ]
    )
    target = onboarded_service(catalog)
    first = target.register_tree(tree_registration())
    assert first.tree.forecast_experiment_only is True

    # A second cultivar in one deployment cohort is rejected before the write.
    with pytest.raises(OnboardingError, match="cultivar_cohort_mismatch"):
        target.register_tree(
            tree_registration(
                tree_id="tree-approved",
                cultivar_id="second_plum_v1",
                species="prunus_domestica",
            )
        )
    assert target._store.get_tree("tree-approved") is None  # noqa: SLF001

    with pytest.raises(OnboardingError, match="cultivar_not_supported"):
        target.register_tree(tree_registration(tree_id="tree-unsupported", cultivar_id="plum-x"))
    with pytest.raises(OnboardingError, match="cultivar_species_mismatch"):
        target.register_tree(tree_registration(tree_id="tree-species", species="malus_domestica"))

    single = onboarded_service()
    first_tree = single.register_tree(tree_registration())
    second_tree = single.register_tree(tree_registration(tree_id="tree-fixture-2"))
    assert second_tree.tree.cultivar_id == first_tree.tree.cultivar_id


def _document(cultivar: Cultivar) -> dict[str, object]:
    return {
        "content_version": cultivar.content_version,
        "display_name": cultivar.display_name,
        "species": cultivar.species,
        "review_status": cultivar.review_status,
    }


def test_collection_gate_tracks_view_approval_consent_and_organization_status() -> None:
    target = onboarded_service()
    target.register_tree(tree_registration())
    store = target._store  # noqa: SLF001 - fixture store inspection
    record = tree_record(target)

    approved = replace(record, camera_view_approved=True, version=1)
    store.put_tree(approved)
    snapshot = target.get_tree(tree_id="tree-fixture", actor_id="owner-fixture")
    assert snapshot.registration_status is TreeRegistrationStatus.COLLECTION_PERMITTED
    assert snapshot.collection.permitted is True
    assert snapshot.collection.blocked_reasons == ()

    stale_policy = replace(
        record,
        camera_view_approved=True,
        consents=tuple(
            replace(consent, policy_version="camera-collection-v0")
            if consent.kind is ConsentKind.CAMERA_COLLECTION
            else consent
            for consent in record.consents
        ),
        version=2,
    )
    store.put_tree(stale_policy)
    blocked = target.get_tree(tree_id="tree-fixture", actor_id="owner-fixture")
    assert blocked.registration_status is TreeRegistrationStatus.COLLECTION_BLOCKED
    assert blocked.collection.blocked_reasons == ("consent_policy_outdated:camera_collection",)

    missing = replace(record, camera_view_approved=True, consents=(), version=3)
    store.put_tree(missing)
    gate = target.get_tree(tree_id="tree-fixture", actor_id="owner-fixture").collection
    assert gate.permitted is False
    assert gate.blocked_reasons == (
        "consent_missing:camera_collection",
        "consent_missing:organization_operations",
    )

    organization = store.get_organization("org-fixture")
    assert organization is not None
    store.put_organization(replace(organization, status=OrganizationStatus.OFFBOARDED, version=1))
    suspended = target.get_tree(tree_id="tree-fixture", actor_id="owner-fixture").collection
    assert "organization_not_active" in suspended.blocked_reasons

    assert evaluate_collection_gate(record, OrganizationStatus.ACTIVE).blocked_reasons == (
        "camera_view_not_approved",
    )


def test_consent_evaluation_is_deterministic_and_absent_consents_are_unknown() -> None:
    statuses = evaluate_consents(consents())
    assert [status.kind for status in statuses] == [policy.kind for policy in CONSENT_POLICIES]
    assert [status.policy_current for status in statuses] == [True, True, False]
    assert all(status.accepted_at == T0 for status in statuses if status.granted)
    assert evaluate_consents(consents()) == statuses


def test_tree_fingerprint_ignores_clocks_but_not_material_fields() -> None:
    base = tree_registration()
    retry = tree_registration(at=T0 + timedelta(days=1))
    assert tree_registration_fingerprint(base) == tree_registration_fingerprint(retry)
    assert tree_registration_fingerprint(base) != tree_registration_fingerprint(
        tree_registration(tree_id="tree-other")
    )
    assert tree_registration_fingerprint(base) != tree_registration_fingerprint(
        tree_registration(provenance=Provenance.LIVE)
    )


def test_unknown_error_code_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown onboarding error code"):
        OnboardingError("invented_failure")
    error = OnboardingError("tree_not_found", detail="tree-fixture")
    assert isinstance(error, ValueError)
    assert error.code == "tree_not_found"
    assert error.detail == "tree-fixture"
    assert isinstance(OnboardingAuthorizationError("actor_not_authorized"), PermissionError)


def test_snapshot_projection_contains_only_coarse_non_media_fields() -> None:
    target = onboarded_service()
    snapshot = target.register_tree(tree_registration()).tree
    document = tree_snapshot_document(snapshot)
    serialized = repr(document)
    assert document["coarse_location"] == {"geohash": "u173z", "region_code": "NL-ZH"}
    assert document["exact_address_included"] is False
    assert document["raw_media_included"] is False
    assert document["internal_ripeness_inferred"] is False
    assert document["food_safety_inferred"] is False
    assert document["human_readiness_review_required"] is True
    assert "access_notes" not in serialized
    assert "raw_media_uri" not in serialized
