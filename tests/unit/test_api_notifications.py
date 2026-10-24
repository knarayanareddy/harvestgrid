"""Unit tests for the notification dispatch route.

The route is the only place where a coordinator's approval becomes an outbound message, so these
tests cover the guarantees rather than the plumbing: no phone number or restricted address ever
crosses the API boundary in either direction, opt-out and caps are honoured, approval evidence is
required, and a replayed ``Idempotency-Key`` cannot send twice.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from harvestgrid_api import build_fixture_container, create_app
from harvestgrid_testkit import AsgiClient

FIXED_NOW = datetime(2026, 7, 2, 8, 0, tzinfo=UTC)
DISPATCH = "/v1/notifications/dispatch"

COORDINATOR_A = {"authorization": "Bearer fixture-coordinator-a"}
COORDINATOR_B = {"authorization": "Bearer fixture-coordinator-b"}
OWNER_A = {"authorization": "Bearer fixture-owner-a"}
VOLUNTEER_A = {"authorization": "Bearer fixture-volunteer-a"}

RESTRICTED_ADDRESS = "FIXTURE 1 Example Street"
RESTRICTED_ACCESS_NOTE = "gate code 0000"
COORDINATOR_NOTE = "FIXTURE access note"


def dispatch_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "glean_id": "glean-fixture-01",
        "glean_state": "confirmed",
        "owner_approval_current": True,
        "approved_at": (FIXED_NOW - timedelta(hours=1)).isoformat(),
        "kind": "invitation",
        "crop_label": "plum",
        "coarse_area_label": "Delft south",
        "window_start": (FIXED_NOW + timedelta(hours=5)).isoformat(),
        "window_end": (FIXED_NOW + timedelta(hours=8)).isoformat(),
        "access_constraints": ["street parking only"],
        "safety_notes": ["ladder required"],
        "volunteer_ids": ["volunteer-a", "volunteer-b", "volunteer-c"],
    }
    body.update(overrides)
    return body


def _client(now: datetime = FIXED_NOW) -> tuple[AsgiClient, Any]:
    container = build_fixture_container(clock=lambda: now)
    return AsgiClient(create_app(container)), container


@pytest.fixture
def client_and_container() -> tuple[AsgiClient, Any]:
    return _client()


def _dispatch(
    client: AsgiClient,
    *,
    key: str = "dispatch-key-0001",
    headers: dict[str, str] | None = None,
    **overrides: Any,
) -> Any:
    return client.post(
        DISPATCH,
        headers={**(headers if headers is not None else COORDINATOR_A), "idempotency-key": key},
        json_body=dispatch_body(**overrides),
    )


# --------------------------------------------------------------------------- happy path


def test_dispatch_sends_to_opted_in_volunteers_only(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    response = _dispatch(client)

    assert response.status_code == 200
    document = response.json()
    assert document["glean_id"] == "glean-fixture-01"
    assert document["kind"] == "invitation"
    assert document["template_version"] == "pickup-v2"
    assert document["provenance"] == "fixture"
    assert (document["sent"], document["skipped"], document["deferred"], document["failed"]) == (
        2,
        1,
        0,
        0,
    )

    outcomes = {outcome["volunteer_id"]: outcome for outcome in document["outcomes"]}
    assert outcomes["volunteer-a"]["status"] == "sent"
    assert outcomes["volunteer-a"]["reason"] == "delivered_to_provider"
    assert outcomes["volunteer-a"]["message_id"]
    assert 1 <= outcomes["volunteer-a"]["segments"] <= 3
    # volunteer-c never opted in, so nothing may be rendered or queued for them.
    assert outcomes["volunteer-c"]["status"] == "skipped"
    assert outcomes["volunteer-c"]["reason"] == "sms_opt_in_required"
    assert len(container.transport.sent) == 2


def test_dispatch_response_never_contains_phones_or_restricted_details(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    response = _dispatch(client)

    forbidden = (
        RESTRICTED_ADDRESS,
        RESTRICTED_ACCESS_NOTE,
        "+31600000001",
        "+31600000002",
        "phone_e164",
    )
    for fragment in forbidden:
        assert fragment not in response.text
    for message in container.transport.sent:
        assert RESTRICTED_ADDRESS not in message.body
        assert RESTRICTED_ACCESS_NOTE not in message.body


def test_dispatch_returns_digests_instead_of_message_bodies(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    document = _dispatch(client).json()

    notices = {notice["volunteer_id"]: notice for notice in document["notices"]}
    sent_bodies = {
        message.to: hashlib.sha256(message.body.encode("utf-8")).hexdigest()
        for message in container.transport.sent
    }
    assert notices["volunteer-a"]["status"] == "sent"
    assert notices["volunteer-a"]["body_sha256"] in set(sent_bodies.values())
    assert len(notices["volunteer-a"]["body_sha256"]) == 64
    # A suppressed recipient has nothing rendered, so there is nothing to digest.
    assert notices["volunteer-c"] == {
        "volunteer_id": "volunteer-c",
        "status": "skipped",
        "body_sha256": None,
    }
    assert len(document["result_sha256"]) == 64
    assert len(document["request_sha256"]) == 64
    assert document["job_id"]


def test_dispatch_records_the_completed_job(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    job_id = _dispatch(client).json()["job_id"]

    record = container.jobs.get(job_id)
    assert record is not None
    assert record.kind == "notification_dispatch"
    assert record.status == "completed"
    assert record.provenance == "fixture"
    assert record.result_sha256 is not None


def test_ledger_stores_digests_and_keys_only(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    _dispatch(client)

    attempts = container.ledger.attempts()
    assert [attempt.volunteer_id for attempt in attempts[:2]] == ["volunteer-a", "volunteer-b"]
    assert attempts[0].idempotency_key == "glean-fixture-01/confirmed/volunteer-a/pickup/pickup-v2"
    assert attempts[0].provider == "fixture"
    assert len(attempts[0].body_sha256 or "") == 64
    assert all("+316" not in str(attempt.as_dict()) for attempt in attempts)


# --------------------------------------------------------------------------- suppression


def test_quiet_hours_defer_without_sending() -> None:
    # 22:30 in Delft is inside the 21:00-07:00 quiet window.
    client, container = _client(datetime(2026, 7, 2, 20, 30, tzinfo=UTC))
    response = _dispatch(client)

    assert response.status_code == 200
    document = response.json()
    assert (document["sent"], document["deferred"]) == (0, 2)
    assert {outcome["reason"] for outcome in document["outcomes"]} == {
        "quiet_hours",
        "sms_opt_in_required",
    }
    assert container.transport.sent == []


def test_daily_cap_limits_repeat_notices_to_one_volunteer(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    for index in range(4):
        # A distinct glean per attempt: reusing one glean would hit duplicate suppression first,
        # which is a different guard from the per-recipient daily cap.
        response = _dispatch(
            client,
            key=f"dispatch-key-{index:04d}",
            glean_id=f"glean-fixture-{index:02d}",
            volunteer_ids=["volunteer-a"],
        )
        assert response.status_code == 200

    blocked = [attempt for attempt in container.ledger.attempts() if attempt.status != "sent"]
    assert len(container.transport.sent) == 3  # MAX_PER_RECIPIENT_PER_DAY
    assert [attempt.error_code for attempt in blocked] == ["daily_cap_reached"]


def test_cancellation_requires_a_cancelled_glean(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, _ = client_and_container
    response = _dispatch(client, kind="cancellation", glean_state="confirmed")

    assert response.status_code == 403
    assert response.json()["code"] == "dispatch_not_authorized"


def test_cancellation_is_dispatched_when_the_glean_is_cancelled(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    response = _dispatch(client, kind="cancellation", glean_state="cancelled")

    assert response.status_code == 200
    assert response.json()["kind"] == "cancellation"
    assert len(container.transport.sent) == 2


# --------------------------------------------------------------------------- safety gates


def test_stale_owner_approval_is_refused(client_and_container: tuple[AsgiClient, Any]) -> None:
    client, container = client_and_container
    response = _dispatch(client, owner_approval_current=False)

    assert response.status_code == 403
    problem = response.json()
    assert problem["code"] == "dispatch_not_authorized"
    assert problem["detail"]
    assert container.transport.sent == []
    assert container.ledger.attempts() == ()


def test_reminder_for_a_cancelled_glean_is_refused(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    response = _dispatch(client, kind="reminder", glean_state="cancelled", key="dispatch-key-0002")

    # A cancelled glean may only produce a cancellation notice, never an invitation or reminder.
    assert response.status_code == 403
    assert response.json()["code"] == "dispatch_not_authorized"
    assert container.transport.sent == []


def test_restricted_address_in_a_coordinator_note_blocks_the_message(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    response = _dispatch(client, access_constraints=[RESTRICTED_ADDRESS])

    assert response.status_code == 200
    document = response.json()
    assert document["sent"] == 0
    assert {outcome["status"] for outcome in document["outcomes"]} == {"skipped"}
    assert "message_contains_restricted_content" in {
        outcome["reason"] for outcome in document["outcomes"]
    }
    assert container.transport.sent == []
    assert RESTRICTED_ADDRESS not in response.text


def test_phone_number_in_a_coordinator_note_blocks_the_message(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    response = _dispatch(client, safety_notes=["call +31600000001"])

    assert response.status_code == 200
    document = response.json()
    assert document["sent"] == 0
    assert "message_contains_phone_number" in {
        outcome["reason"] for outcome in document["outcomes"]
    }
    assert container.transport.sent == []


def test_provider_failure_is_reported_per_recipient(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    from harvestgrid_notifications import NotificationError

    client, container = client_and_container
    container.transport.failure = NotificationError("provider_rejected", "quota")
    response = _dispatch(client)

    assert response.status_code == 200
    document = response.json()
    assert document["sent"] == 0
    assert document["failed"] == 2
    assert {outcome["reason"] for outcome in document["outcomes"]} == {
        "provider_rejected",
        "sms_opt_in_required",
    }


# --------------------------------------------------------------------------- authorization


def test_dispatch_requires_an_idempotency_key(client_and_container: tuple[AsgiClient, Any]) -> None:
    client, container = client_and_container
    response = client.post(DISPATCH, headers=COORDINATOR_A, json_body=dispatch_body())

    assert response.status_code == 400
    assert response.json()["code"] == "idempotency_key_required"
    assert container.transport.sent == []


def test_dispatch_is_coordinator_only(client_and_container: tuple[AsgiClient, Any]) -> None:
    client, container = client_and_container

    for headers in (OWNER_A, VOLUNTEER_A):
        response = _dispatch(client, headers=headers)
        assert response.status_code == 403
        assert response.json()["code"] == "role_forbidden"
    anonymous = client.post(
        DISPATCH, headers={"idempotency-key": "k" * 10}, json_body=dispatch_body()
    )
    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "unauthenticated"
    assert container.transport.sent == []


def test_dispatch_rejects_volunteers_from_another_organization(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    response = _dispatch(client, headers=COORDINATOR_B, volunteer_ids=["volunteer-a"])

    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "volunteer_not_found"
    assert problem["fields"] == [{"path": "/volunteer_ids/0", "code": "not_found"}]
    assert container.transport.sent == []


def test_dispatch_rejects_unknown_volunteer_ids_with_indexes(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, _ = client_and_container
    response = _dispatch(client, volunteer_ids=["volunteer-a", "volunteer-zzz"])

    assert response.status_code == 422
    problem = response.json()
    assert problem["fields"] == [{"path": "/volunteer_ids/1", "code": "not_found"}]


# --------------------------------------------------------------------------- idempotency


def test_replaying_a_dispatch_key_sends_nothing_again(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    first = _dispatch(client)
    attempts_after_first = len(container.ledger.attempts())

    second = _dispatch(client)

    assert second.status_code == 200
    assert second.header("idempotent-replay") == "true"
    assert second.json() == first.json()
    assert len(container.transport.sent) == 2
    assert len(container.ledger.attempts()) == attempts_after_first


def test_reusing_a_key_for_a_different_payload_is_a_conflict(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    _dispatch(client)
    response = _dispatch(client, crop_label="apple")

    assert response.status_code == 409
    problem = response.json()
    assert problem["code"] == "idempotency_key_reused"
    assert len(container.transport.sent) == 2


def test_a_second_dispatch_for_the_same_glean_is_suppressed(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    _dispatch(client, key="dispatch-key-0001")
    second = _dispatch(client, key="dispatch-key-0002")

    # A new Idempotency-Key is not consent to re-notify: the glean/recipient/template key in the
    # ledger still suppresses the duplicate, so no volunteer is texted twice about one glean.
    assert second.status_code == 200
    assert second.header("idempotent-replay") is None
    assert second.json()["sent"] == 0
    assert "duplicate_suppressed" in {outcome["reason"] for outcome in second.json()["outcomes"]}
    assert len(container.transport.sent) == 2


def test_a_new_glean_notifies_the_same_volunteer_again(
    client_and_container: tuple[AsgiClient, Any],
) -> None:
    client, container = client_and_container
    _dispatch(client, key="dispatch-key-0001")
    _dispatch(client, key="dispatch-key-0002", glean_id="glean-fixture-02")

    assert len(container.transport.sent) == 4
    assert {message.idempotency_key.split("/")[0] for message in container.transport.sent} == {
        "glean-fixture-01",
        "glean-fixture-02",
    }
    assert len({message.idempotency_key for message in container.transport.sent}) == 4


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("overrides", "path", "code"),
    [
        ({"glean_state": "archived"}, "/glean_state", "literal_error"),
        ({"window_end": "2026-07-02T09:00:00"}, "/window_end", "value_error"),
        ({"volunteer_ids": []}, "/volunteer_ids", "too_short"),
        ({"volunteer_ids": ["volunteer-a", "volunteer-a"]}, "/volunteer_ids", "value_error"),
        ({"safety_notes": [""]}, "/safety_notes", "value_error"),
        ({"phone_e164": "+31600000001"}, "/phone_e164", "extra_forbidden"),
        ({"access_note": "gate code"}, "/access_note", "extra_forbidden"),
    ],
)
def test_dispatch_validates_requests(
    client_and_container: tuple[AsgiClient, Any],
    overrides: dict[str, Any],
    path: str,
    code: str,
) -> None:
    client, container = client_and_container
    response = _dispatch(client, **overrides)

    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "validation_failed"
    assert {"path": path, "code": code} in problem["fields"]
    assert container.transport.sent == []


def test_window_must_end_after_it_starts(client_and_container: tuple[AsgiClient, Any]) -> None:
    client, _ = client_and_container
    response = _dispatch(
        client,
        window_start=(FIXED_NOW + timedelta(hours=8)).isoformat(),
        window_end=(FIXED_NOW + timedelta(hours=5)).isoformat(),
    )

    assert response.status_code == 422
    assert response.json()["fields"] == [{"path": "/", "code": "value_error"}]
