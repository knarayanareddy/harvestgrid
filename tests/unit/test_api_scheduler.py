"""Unit tests for the forecasting API: scheduler trigger, projections, auth, and idempotency.

The routes are exercised through a real ASGI round-trip (routing, dependencies, middleware, and
exception handlers), while the container is deterministic: a fixed clock, a seeded fixture
store, and the synthetic weather source.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from harvestgrid_api import (
    BrokerJobRunner,
    InlineJobRunner,
    InMemoryJobRegistry,
    build_fixture_container,
    create_app,
)
from harvestgrid_api.container import OrganizationScopedStore
from harvestgrid_testkit import AsgiClient

FIXED_NOW = datetime(2026, 7, 2, 8, 0, tzinfo=UTC)
TRIGGER = "/v1/forecasting/trigger"
PROJECTION = "/v1/forecasting/{tree_id}/projection"

COORDINATOR_A = {"authorization": "Bearer fixture-coordinator-a"}
COORDINATOR_B = {"authorization": "Bearer fixture-coordinator-b"}
OWNER_A = {"authorization": "Bearer fixture-owner-a"}
EVALUATOR_A = {"authorization": "Bearer fixture-evaluator-a"}
VOLUNTEER_A = {"authorization": "Bearer fixture-volunteer-a"}


def _client(container: Any | None = None) -> tuple[AsgiClient, Any]:
    resolved = container or build_fixture_container(clock=lambda: FIXED_NOW)
    return AsgiClient(create_app(resolved)), resolved


@pytest.fixture
def client_and_container() -> tuple[AsgiClient, Any]:
    return _client()


def _trigger(client: AsgiClient, key: str = "trigger-key-0001", **headers: str) -> Any:
    return client.post(
        TRIGGER, headers={**COORDINATOR_A, **headers, "idempotency-key": key}, json_body={}
    )


# --------------------------------------------------------------------------- trigger


def test_trigger_runs_one_tick_and_returns_a_completed_job(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    response = _trigger(client)

    assert response.status_code == 202
    document = response.json()
    assert document["kind"] == "forecast_ripening_tick"
    assert document["status"] == "completed"
    assert document["provenance"] == "fixture"
    assert document["completed_at"] == document["submitted_at"] == "2026-07-02T08:00:00Z"
    assert len(document["job_id"]) == 26
    assert len(document["result_sha256"] or "") == 64

    result = document["result"]
    assert result["trees_considered"] == 2  # both fixture trees belong to org-fixture-a
    assert result["trees_due"] == 2
    assert result["projections_updated"] == 2
    assert result["failed"] == []
    assert result["budget_breaches"] == []
    # Only counters and tree ids leave the worker: no coordinates, addresses, or weather rows.
    assert set(result) == {
        "trees_considered",
        "trees_due",
        "observations_new",
        "observations_duplicate",
        "projections_updated",
        "projections_unchanged",
        "adjustments",
        "failed",
        "skipped_gated",
        "skipped_backoff",
        "skipped_cap",
        "budget_breaches",
        "review_recommended",
    }
    assert container.store.observation_count() == result["observations_new"]


def test_trigger_records_the_job_in_the_registry(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    job_id = _trigger(client).json()["job_id"]

    assert container.jobs.get(job_id) is not None


def test_replaying_a_trigger_key_reuses_the_job_and_does_not_poll_again(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    first = _trigger(client)
    observations_after_first = container.store.observation_count()

    second = _trigger(client)

    assert second.status_code == 202
    assert second.header("idempotent-replay") == "true"
    assert second.json()["job_id"] == first.json()["job_id"]
    assert container.store.observation_count() == observations_after_first
    assert len(container.jobs.all()) == 1


def test_trigger_requires_an_idempotency_key(client_and_container: tuple[AsgiClient, Any]) -> None:
    client, _ = client_and_container
    response = client.post(TRIGGER, headers=COORDINATOR_A, json_body={})

    assert response.status_code == 400
    assert response.header("content-type") == "application/problem+json"
    problem = response.json()
    assert problem["code"] == "idempotency_key_required"
    assert problem["instance"] == TRIGGER
    assert problem["request_id"]


@pytest.mark.parametrize("key", ["short", "has spaces in it", "x" * 200, "bad/key"])
def test_trigger_rejects_malformed_idempotency_keys(
    client_and_container: tuple[AsgiClient, Any], key: str
) -> None:
    client, _ = client_and_container
    response = client.post(TRIGGER, headers={**COORDINATOR_A, "idempotency-key": key}, json_body={})

    assert response.status_code == 400
    assert response.json()["code"] == "idempotency_key_invalid"


def test_blank_idempotency_key_is_treated_as_missing(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, _ = client_and_container
    response = client.post(TRIGGER, headers={**COORDINATOR_A, "idempotency-key": ""}, json_body={})

    assert response.status_code == 400
    assert response.json()["code"] == "idempotency_key_required"


def test_trigger_rejects_unknown_body_fields(client_and_container: tuple[AsgiClient, Any]) -> None:
    client, _ = client_and_container
    response = client.post(
        TRIGGER,
        headers={**COORDINATOR_A, "idempotency-key": "trigger-key-0002"},
        json_body={"force": True},
    )

    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "validation_failed"
    assert {"path": "/force", "code": "extra_forbidden"} in problem["fields"]


def test_trigger_requires_a_bearer_token(client_and_container: tuple[AsgiClient, Any]) -> None:
    client, _ = client_and_container

    missing = client.post(TRIGGER, headers={"idempotency-key": "trigger-key-0001"}, json_body={})
    unknown = client.post(
        TRIGGER,
        headers={"authorization": "Bearer nope", "idempotency-key": "trigger-key-0001"},
        json_body={},
    )
    wrong_scheme = client.post(
        TRIGGER,
        headers={"authorization": "Basic abc", "idempotency-key": "trigger-key-0001"},
        json_body={},
    )

    for response in (missing, unknown, wrong_scheme):
        assert response.status_code == 401
        assert response.json()["code"] == "unauthenticated"
        assert response.header("www-authenticate") == "Bearer"


@pytest.mark.parametrize("headers", [OWNER_A, EVALUATOR_A, VOLUNTEER_A])
def test_trigger_is_coordinator_only(
    client_and_container: tuple[AsgiClient, Any], headers: dict[str, str]
) -> None:
    client, _ = client_and_container
    response = client.post(
        TRIGGER, headers={**headers, "idempotency-key": "trigger-key-0001"}, json_body={}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "role_forbidden"


def test_idempotency_keys_are_scoped_per_organization(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    first = _trigger(client)
    other = client.post(
        TRIGGER, headers={**COORDINATOR_B, "idempotency-key": "trigger-key-0001"}, json_body={}
    )

    assert other.status_code == 202
    assert other.header("idempotent-replay") is None
    assert other.json()["job_id"] != first.json()["job_id"]
    # Organization B owns no fixture trees, so its tick is a no-op rather than a cross-org read.
    assert other.json()["result"]["trees_considered"] == 0
    assert len(container.jobs.all()) == 2


def test_trigger_reports_503_when_no_worker_backend_exists() -> None:
    container = build_fixture_container(clock=lambda: FIXED_NOW)
    registry = InMemoryJobRegistry()
    container.runner_factory = lambda scoped: BrokerJobRunner(
        broker_url=None, clock=container.clock, registry=registry
    )
    client = AsgiClient(create_app(container))

    response = _trigger(client)

    assert response.status_code == 503
    problem = response.json()
    assert problem["code"] == "worker_backend_unavailable"
    assert "FORECASTING_BROKER_URL" in problem["detail"]
    assert registry.all() == ()


def test_worker_runner_receives_an_organization_scoped_store() -> None:
    container = build_fixture_container(clock=lambda: FIXED_NOW)
    seen: list[OrganizationScopedStore] = []

    def factory(scoped: OrganizationScopedStore) -> InlineJobRunner:
        seen.append(scoped)
        return InlineJobRunner(clock=container.clock, registry=container.jobs)

    container.runner_factory = factory
    client = AsgiClient(create_app(container))
    _trigger(client)

    assert len(seen) == 1
    assert [item.tree_id for item in seen[0].subscriptions()] == ["tree-plum-01", "tree-plum-02"]


# --------------------------------------------------------------------------- projections


def test_projection_is_404_until_the_first_tick(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, _ = client_and_container
    before = client.get(PROJECTION.format(tree_id="tree-plum-01"), headers=OWNER_A)

    assert before.status_code == 404
    assert before.json()["code"] == "projection_not_available"

    _trigger(client)
    after = client.get(PROJECTION.format(tree_id="tree-plum-01"), headers=OWNER_A)

    assert after.status_code == 200
    assert after.json()["provenance"] == "fixture"


def test_projection_reports_a_review_gated_harvest_window(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, _ = client_and_container
    _trigger(client)
    response = client.get(PROJECTION.format(tree_id="tree-plum-01"), headers=OWNER_A)

    assert response.status_code == 200
    document = response.json()
    assert document["tree_id"] == "tree-plum-01"
    assert document["cultivar_id"] == "european_plum"
    assert document["coarse_geohash"] == "u173z"  # coarse-only, never a precise location
    assert document["version"] == 1
    assert len(document["fingerprint"]) == 64

    review = document["review"]
    assert review["required"] is True
    assert review["experimental"] is True
    assert review["prior_review_status"] == "fixture_unreviewed"
    assert "experimental_projection" in review["reason_codes"]

    projection = document["projection"]
    assert projection["internal_ripeness_inferred"] is False
    assert projection["food_safety_inferred"] is False
    assert projection["human_readiness_review_required"] is True


def test_projection_harvest_window_is_ordered_and_dated(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, _ = client_and_container
    _trigger(client)
    window = client.get(PROJECTION.format(tree_id="tree-plum-01"), headers=OWNER_A).json()[
        "harvest_window"
    ]

    assert window["available"] is True
    assert window["start"] <= window["end"]
    # FIXED_NOW is early July; a European plum is not ripe yet, so the window is closed.
    assert window["open_now"] is False


def test_projection_window_opens_inside_the_projected_range() -> None:
    container = build_fixture_container(clock=lambda: FIXED_NOW)
    client = AsgiClient(create_app(container))
    _trigger(client)
    window = client.get(PROJECTION.format(tree_id="tree-plum-01"), headers=OWNER_A).json()[
        "harvest_window"
    ]
    start = datetime.fromisoformat(window["start"]).replace(tzinfo=UTC)

    # Evaluate the same stored projection at a moment inside the window.
    inside = start + timedelta(days=1)
    container.clock = lambda: inside
    reopened = client.get(PROJECTION.format(tree_id="tree-plum-01"), headers=OWNER_A).json()

    assert reopened["harvest_window"] == {**window, "open_now": True}


@pytest.mark.parametrize("headers", [OWNER_A, COORDINATOR_A, EVALUATOR_A])
def test_projection_is_readable_by_owner_coordinator_and_evaluator(
    client_and_container: tuple[AsgiClient, Any], headers: dict[str, str]
) -> None:
    client, _ = client_and_container
    _trigger(client)

    assert client.get(PROJECTION.format(tree_id="tree-plum-02"), headers=headers).status_code == 200


def test_projection_rejects_the_volunteer_role(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, _ = client_and_container
    _trigger(client)
    response = client.get(PROJECTION.format(tree_id="tree-plum-01"), headers=VOLUNTEER_A)

    assert response.status_code == 403
    assert response.json()["code"] == "role_forbidden"


def test_foreign_tree_is_indistinguishable_from_a_missing_tree(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, _ = client_and_container
    _trigger(client)

    foreign = client.get(PROJECTION.format(tree_id="tree-plum-01"), headers=COORDINATOR_B)
    missing = client.get(PROJECTION.format(tree_id="tree-never-existed"), headers=COORDINATOR_A)

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["code"] == missing.json()["code"] == "tree_not_found"
    assert foreign.json()["detail"] == missing.json()["detail"]
    # The only difference is the echoed path and the correlation id: no hint of the real tree.
    assert {key: value for key, value in foreign.json().items() if key != "request_id"} == {
        key: value for key, value in missing.json().items() if key != "request_id"
    } | {"instance": PROJECTION.format(tree_id="tree-plum-01")}
    assert "european_plum" not in foreign.text


def test_projection_validates_the_tree_id(client_and_container: tuple[AsgiClient, Any]) -> None:
    client, _ = client_and_container
    response = client.get(PROJECTION.format(tree_id="-forbidden"), headers=OWNER_A)

    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "validation_failed"
    assert problem["fields"] == [{"path": "/tree_id", "code": "string_pattern_mismatch"}]


def test_projection_requires_authentication(client_and_container: tuple[AsgiClient, Any]) -> None:
    client, _ = client_and_container
    response = client.get(PROJECTION.format(tree_id="tree-plum-01"))

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


# --------------------------------------------------------------------------- transport


def test_every_response_carries_an_correlation_id(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, _ = client_and_container

    responses = [
        client.get("/healthz"),
        client.get(PROJECTION.format(tree_id="tree-plum-01")),
        _trigger(client),
    ]

    request_ids = [response.header("x-request-id") or "" for response in responses]
    assert all(len(request_id) == 26 for request_id in request_ids)
    assert len(set(request_ids)) == len(request_ids)
    assert responses[1].json()["request_id"] == request_ids[1]


def test_health_reports_fixture_provenance(client_and_container: tuple[AsgiClient, Any]) -> None:
    client, _ = client_and_container
    document = client.get("/healthz").json()

    assert document == {
        "status": "ok",
        "provenance": "fixture",
        "demo_mode": True,
        "worker_backend": "InlineJobRunner",
    }


def test_wrong_method_is_rejected_with_a_problem_document(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, _ = client_and_container
    response = client.post(PROJECTION.format(tree_id="tree-plum-01"), headers=OWNER_A, json_body={})

    assert response.status_code == 405


def test_openapi_document_describes_the_three_routes(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    document = create_app(container).openapi()

    assert set(document["paths"]) == {
        "/healthz",
        "/v1/forecasting/trigger",
        "/v1/forecasting/{tree_id}/projection",
        "/v1/notifications/dispatch",
    }
    trigger_parameters = {
        parameter["name"] for parameter in document["paths"][TRIGGER]["post"]["parameters"]
    }
    assert "Idempotency-Key" in trigger_parameters
    assert document["paths"][TRIGGER]["post"]["responses"]["202"]["description"]
