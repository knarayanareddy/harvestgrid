from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from harvestgrid_domain import (
    CoarseLocation,
    InMemoryOnboardingStore,
    JsonCultivarCatalog,
    MemberRecord,
    MembershipRole,
    OnboardingError,
    OrganizationRecord,
    OrganizationStatus,
    Provenance,
    TreeRecord,
)

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def organization_record(*, organization_id: str, version: int = 0) -> OrganizationRecord:
    return OrganizationRecord(
        organization_id=organization_id,
        name=f"Fixture {organization_id}",
        status=OrganizationStatus.ACTIVE,
        terms_version="harvestgrid-organization-terms-v1",
        registration_fingerprint="fixture",
        created_by="coordinator-fixture",
        created_at=T0,
        version=version,
    )


def test_store_enforces_compare_and_set_versions() -> None:
    store = InMemoryOnboardingStore()
    assert store.get_organization("org-fixture") is None
    store.put_organization(organization_record(organization_id="org-fixture"))
    assert store.get_organization("org-fixture") == organization_record(
        organization_id="org-fixture"
    )

    with pytest.raises(OnboardingError, match="stale_record_version"):
        store.put_organization(organization_record(organization_id="org-fixture"))
    with pytest.raises(OnboardingError, match="stale_record_version"):
        store.put_organization(organization_record(organization_id="org-fixture", version=2))

    store.put_organization(organization_record(organization_id="org-fixture", version=1))
    updated = store.get_organization("org-fixture")
    assert updated is not None
    assert updated.version == 1


def test_store_lists_only_matching_trees_in_stable_order() -> None:
    store = InMemoryOnboardingStore()
    tree = TreeRecord(
        tree_id="tree-b",
        owner_id="owner-fixture",
        organization_id="org-fixture",
        cultivar_id="european_plum_v1",
        cultivar_display_name="european_plum_v1",
        cultivar_content_version="european_plum_v1",
        cultivar_operational_prior_approved=False,
        species="prunus_domestica",
        coarse_location=CoarseLocation(geohash="u173z"),
        bloom_date=None,
        provenance=Provenance.FIXTURE,
        consents=(),
        registration_fingerprint="fixture",
        camera_view_approved=False,
        registered_at=T0,
        version=0,
        audit=(),
    )
    other = replace(tree, tree_id="tree-a", version=0)
    elsewhere = replace(tree, tree_id="tree-c", organization_id="org-other", version=0)
    for record in (tree, other, elsewhere):
        store.put_tree(record)

    assert [record.tree_id for record in store.list_trees("org-fixture")] == [
        "tree-a",
        "tree-b",
    ]
    assert [record.tree_id for record in store.list_trees("org-missing")] == []
    assert store.get_tree("tree-c") is not None


def test_catalog_reads_approved_and_draft_content() -> None:
    draft = JsonCultivarCatalog.from_text(
        '{"content_version": "draft_plum_v1", "species": "prunus_domestica",'
        ' "review_status": "draft_no_operational_prior_approved"}'
    )
    cultivar = draft.require_cultivar("draft_plum_v1")
    assert cultivar.display_name == "draft_plum_v1"
    assert cultivar.operational_prior_approved is False
    assert draft.list_cultivars() == (cultivar,)

    approved = JsonCultivarCatalog.from_text(
        '{"content_version": "plum_v2", "display_name": "European plum",'
        ' "species": "prunus_domestica", "review_status": "approved_operational_prior"}'
    )
    assert approved.require_cultivar("plum_v2").operational_prior_approved is True
    assert approved.require_cultivar("plum_v2").display_name == "European plum"
    with pytest.raises(KeyError):
        approved.require_cultivar("unknown")


def test_catalog_rejects_malformed_content() -> None:
    with pytest.raises(OnboardingError, match="cultivar_catalog_invalid"):
        JsonCultivarCatalog.from_text("not json")
    with pytest.raises(OnboardingError, match="cultivar_catalog_invalid"):
        JsonCultivarCatalog.from_text("[]")
    with pytest.raises(OnboardingError, match="cultivar_catalog_invalid"):
        JsonCultivarCatalog.from_text('{"species": "prunus_domestica", "review_status": "draft"}')
    with pytest.raises(OnboardingError, match="cultivar_catalog_invalid"):
        JsonCultivarCatalog.from_text(
            '{"content_version": "plum_v1", "species": " ", "review_status": "draft"}'
        )
    with pytest.raises(OnboardingError, match="cultivar_catalog_invalid"):
        JsonCultivarCatalog.from_text(
            '{"content_version": "plum_v1", "species": "prunus_domestica",'
            ' "review_status": "draft", "display_name": ""}'
        )
    with pytest.raises(OnboardingError, match="cultivar_catalog_invalid"):
        JsonCultivarCatalog([])

    duplicate = {
        "content_version": "plum_v1",
        "species": "prunus_domestica",
        "review_status": "draft",
    }
    with pytest.raises(OnboardingError, match="cultivar_catalog_invalid"):
        JsonCultivarCatalog([duplicate, duplicate])
    with pytest.raises(OnboardingError, match="cultivar_catalog_invalid") as missing:
        JsonCultivarCatalog.from_directory(ROOT / "models")
    assert missing.value.detail == "no_cultivar_files"


def test_catalog_from_directory_keeps_raw_documents_isolated() -> None:
    catalog = JsonCultivarCatalog.from_directory(ROOT / "content" / "cultivars")
    cultivar = catalog.require_cultivar("european_plum_v1")
    assert cultivar.species == "prunus_domestica"
    document = catalog.require_document("european_plum_v1")
    assert document["content_version"] == "european_plum_v1"
    document["content_version"] = "mutated"
    assert catalog.require_cultivar("european_plum_v1").content_version == "european_plum_v1"


def test_store_keeps_member_records_isolated_per_identifier() -> None:
    store = InMemoryOnboardingStore()
    member = MemberRecord(
        member_id="owner-fixture",
        organization_id="org-fixture",
        display_name="Fixture owner",
        roles=(MembershipRole.OWNER,),
        authenticated=True,
        terms_version="harvestgrid-owner-terms-v1",
        registration_fingerprint="fixture",
        registered_at=T0,
        version=0,
    )
    store.put_member(member)
    assert store.get_member("owner-fixture") == member
    assert store.get_member("member-missing") is None
    with pytest.raises(OnboardingError, match="stale_record_version"):
        store.put_member(member)


def test_catalog_rejects_unparseable_content_files(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not json")
    with pytest.raises(OnboardingError, match="cultivar_catalog_invalid") as invalid:
        JsonCultivarCatalog.from_directory(tmp_path)
    assert invalid.value.detail == "invalid_json:broken.json"
