import json
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from harvestgrid_domain import (
    CONSENT_POLICIES,
    ONBOARDING_ERROR_CODES,
    ORGANIZATION_TERMS_VERSION,
    OWNER_TERMS_VERSION,
    CoarseLocation,
    ConsentKind,
    ConsentRecord,
    InMemoryOnboardingStore,
    JsonCultivarCatalog,
    OnboardingService,
    OrganizationRegistration,
    OwnerRegistration,
    Provenance,
    TreeRegistration,
    policy_for,
    tree_snapshot_document,
)
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
CULTIVAR_ID = "european_plum_v1"


def load_json(relative: str) -> dict[str, Any]:
    return json.loads((ROOT / relative).read_text())


def registration_schema() -> dict[str, Any]:
    schema = load_json("packages/contracts/tree-registration.schema.json")
    Draft202012Validator.check_schema(schema)
    return schema


def validator() -> Draft202012Validator:
    return Draft202012Validator(registration_schema(), format_checker=FormatChecker())


def registration_document() -> dict[str, Any]:
    catalog = JsonCultivarCatalog.from_directory(ROOT / "content" / "cultivars")
    service = OnboardingService(
        store=InMemoryOnboardingStore(),
        cultivars=catalog,
    )
    service.register_organization(
        OrganizationRegistration(
            organization_id="org-fixture",
            name="Fixture gleaning organization",
            coordinator_id="coordinator-fixture",
            terms_version=ORGANIZATION_TERMS_VERSION,
            terms_accepted=True,
            at=T0,
        )
    )
    service.register_owner(
        OwnerRegistration(
            owner_id="owner-fixture",
            organization_id="org-fixture",
            display_name="Fixture owner",
            authenticated=True,
            terms_version=OWNER_TERMS_VERSION,
            terms_accepted=True,
            at=T0,
        )
    )
    consents = tuple(
        ConsentRecord(
            kind=policy.kind,
            policy_version=policy.version,
            accepted=True,
            accepted_at=T0,
            accepted_by="owner-fixture",
        )
        for policy in CONSENT_POLICIES
        if policy.required_for_collection
    )
    result = service.register_tree(
        TreeRegistration(
            tree_id="tree-fixture",
            owner_id="owner-fixture",
            organization_id="org-fixture",
            cultivar_id=CULTIVAR_ID,
            species="prunus_domestica",
            coarse_location=CoarseLocation(geohash="u173z", region_code="NL-ZH"),
            consents=consents,
            provenance=Provenance.FIXTURE,
            at=T0,
            bloom_date=date(2026, 4, 10),
        )
    )
    return tree_snapshot_document(result.tree)


def test_registration_schema_is_valid_and_document_conforms() -> None:
    registration_schema()
    validator().validate(registration_document())


def test_registration_document_is_stable_across_replays() -> None:
    first = registration_document()
    assert registration_document() == first
    assert json.loads(json.dumps(first)) == first


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document.update({"exact_address": "FIXTURE 1 Example Street"}),
        lambda document: document.update({"raw_media_uri": "s3://fixture/raw"}),
        lambda document: document.update({"latitude": 52.0067}),
        lambda document: document["coarse_location"].update({"latitude": 52.0067}),
        lambda document: document["coarse_location"].update({"geohash": "u173zp"}),
        lambda document: document["coarse_location"].update({"geohash": "52.0067,4.3556"}),
        lambda document: document.update({"registration_status": "dispatched"}),
        lambda document: document.update({"internal_ripeness_inferred": True}),
        lambda document: document.update({"food_safety_inferred": True}),
        lambda document: document.update({"human_readiness_review_required": False}),
        lambda document: document.update({"raw_media_included": True}),
        lambda document: document.update({"exact_address_included": True}),
        lambda document: document["consents"][0].update({"policy_version": 1}),
        lambda document: document["consents"][0].update({"kind": "person_tracking"}),
        lambda document: document["cultivar"].update({"operational_prior_approved": "yes"}),
        lambda document: document.update({"contract_version": "tree-registration-v0"}),
    ],
)
def test_schema_rejects_non_coarse_media_and_unsupported_claims(mutation: Any) -> None:
    document = deepcopy(registration_document())
    mutation(document)
    with pytest.raises(ValidationError):
        validator().validate(document)


def test_schema_requires_every_documented_field() -> None:
    document = registration_document()
    for field in registration_schema()["required"]:
        incomplete = deepcopy(document)
        del incomplete[field]
        assert not validator().is_valid(incomplete), field


def test_error_code_catalog_is_a_stable_contract() -> None:
    expected = {
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
    assert ONBOARDING_ERROR_CODES == frozenset(expected)
    assert all(code == code.lower() and " " not in code for code in ONBOARDING_ERROR_CODES)


def test_consent_policy_contract_keeps_collection_and_training_separate() -> None:
    policies = {policy.kind: policy for policy in CONSENT_POLICIES}
    required = {kind for kind, policy in policies.items() if policy.required_for_collection}
    assert required == {ConsentKind.CAMERA_COLLECTION, ConsentKind.ORGANIZATION_OPERATIONS}
    assert policies[ConsentKind.CROP_ONLY_TRAINING].required_for_collection is False
    assert policies[ConsentKind.CAMERA_COLLECTION].version == "camera-collection-v1"
    assert policies[ConsentKind.ORGANIZATION_OPERATIONS].version == ("organization-operations-v1")
    assert policies[ConsentKind.CROP_ONLY_TRAINING].version == "crop-only-training-v1"
    assert policy_for(ConsentKind.CAMERA_COLLECTION) is policies[ConsentKind.CAMERA_COLLECTION]


def test_canonical_cultivar_content_stays_a_draft_until_reviewed() -> None:
    catalog = JsonCultivarCatalog.from_directory(ROOT / "content" / "cultivars")
    cultivars = catalog.list_cultivars()
    assert [cultivar.cultivar_id for cultivar in cultivars] == [CULTIVAR_ID]
    cultivar = cultivars[0]
    assert cultivar.species == "prunus_domestica"
    assert cultivar.review_status == "draft_no_operational_prior_approved"
    assert cultivar.operational_prior_approved is False

    document = catalog.require_document(CULTIVAR_ID)
    gate = document["production_ml_gate"]
    assert isinstance(gate, dict)
    assert gate["minimum_tree_season_sequences"] == 100
    assert gate["minimum_sites"] == 20
    assert gate["held_out_site_and_season_required"] is True

    registered = registration_document()
    assert registered["forecast_experiment_only"] is True
    assert registered["cultivar"]["operational_prior_approved"] is False
    assert registered["provenance"] == "fixture"
