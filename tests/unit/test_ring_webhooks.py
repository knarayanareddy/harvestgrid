import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from harvestgrid_ring_adapter import (
    ACK_BUDGET,
    WEBHOOK_ERROR_CODES,
    InMemoryMediaJobQueue,
    InMemoryWebhookStore,
    MediaJob,
    RingWebhookService,
    SignatureError,
    TokenBucketRateLimiter,
    WebhookRejection,
    WebhookRequest,
    compute_signature,
)

SECRET = b"fixture-webhook-secret-0123456789abcdef"
T0 = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)
PATH = "/v1/ring/webhooks"


class Clock:
    """Deterministic clock; ``advance`` lets tests measure ACK latency."""

    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "event_id": "evt-0001",
        "event_type": "device.snapshot",
        "device_id": "device-fixture",
        "connection_id": "connection-fixture",
        "occurred_at": "2026-10-24T09:59:30Z",
        "media_ref": "ring://fixture/snapshot/0001",
        "sequence": 17,
    }
    base.update(overrides)
    return base


def body(**overrides: Any) -> bytes:
    return json.dumps(payload(**overrides), separators=(",", ":")).encode("utf-8")


def service(
    *,
    clock: Clock | None = None,
    capacity: int = 30,
    max_body_bytes: int = 64 * 1024,
    queue_depth: int = 1_000,
) -> RingWebhookService:
    return RingWebhookService(
        secret=SECRET,
        store=InMemoryWebhookStore(),
        queue=InMemoryMediaJobQueue(max_depth=queue_depth),
        clock=clock or Clock(),
        rate_limiter=TokenBucketRateLimiter(capacity=capacity, refill_per_second=0.5),
        max_body_bytes=max_body_bytes,
    )


def signed_request(
    *,
    raw: bytes | None = None,
    timestamp: str | None = None,
    signature: str | None = None,
    secret: bytes = SECRET,
    headers: dict[str, str] | None = None,
    method: str = "POST",
    path: str = PATH,
    received_at: datetime = T0,
) -> WebhookRequest:
    raw_body = body() if raw is None else raw
    stamp = timestamp if timestamp is not None else str(int(T0.timestamp()))
    computed = (
        signature
        if signature is not None
        else compute_signature(secret=secret, timestamp=stamp, body=raw_body)
    )
    base_headers = {
        "Content-Type": "application/json",
        "X-Ring-Signature": computed,
        "X-Ring-Timestamp": stamp,
        "User-Agent": "Ring/1.0",
    }
    base_headers.update(headers or {})
    return WebhookRequest(
        method=method,
        path=path,
        headers=base_headers,
        body=raw_body,
        received_at=received_at,
    )


def test_valid_signed_event_is_accepted_enqueued_and_acknowledged_fast() -> None:
    clock = Clock()
    target = service(clock=clock)
    ack = target.handle(signed_request())

    assert ack.accepted is True
    assert ack.status == 200
    assert ack.duplicate is False
    assert ack.body["event_id"] == "evt-0001"
    assert ack.body["status"] == "accepted"
    assert ack.job_id is not None
    assert ack.acknowledgement_ms < ACK_BUDGET.total_seconds() * 1000

    jobs = target.pending_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert isinstance(job, MediaJob)
    assert job.event_id == "evt-0001"
    assert job.event_type == "device.snapshot"
    assert job.connection_id == "connection-fixture"
    assert job.device_id == "device-fixture"
    assert job.media_ref == "ring://fixture/snapshot/0001"
    assert job.payload_sha256 == __import__("hashlib").sha256(body()).hexdigest()
    assert job.provenance == "live"
    assert job.processed is False

    # Metadata-only retention: nothing from the payload is stored except the envelope.
    stored = target.store.get_by_event_id("evt-0001")
    assert stored is not None
    assert set(stored.response) == {"status", "event_id", "job_id", "event_type"}
    assert "ring://fixture" not in json.dumps(stored.response)


def test_duplicate_delivery_is_idempotent_and_creates_one_job() -> None:
    target = service()
    first = target.handle(signed_request())
    second = target.handle(signed_request())

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.deduplicated_by == "event_id"
    assert second.job_id == first.job_id
    assert second.status == 200
    assert len(target.pending_jobs()) == 1
    assert target.queue.all_jobs() == (target.pending_jobs()[0],)

    # A byte-identical payload delivered again is the same observation.
    assert target.handle(signed_request(raw=body())).duplicate is True
    changed = body(**{"event_id": "evt-0002", "occurred_at": "2026-10-24T09:59:31Z"})
    ack = target.handle(signed_request(raw=changed))
    assert ack.duplicate is False
    assert len(target.pending_jobs()) == 2


def test_event_id_reuse_with_different_payload_is_rejected() -> None:
    target = service()
    target.handle(signed_request())
    conflicting = body(sequence=18)
    ack = target.handle(signed_request(raw=conflicting))
    assert ack.accepted is False
    assert ack.status == 409
    assert ack.body["code"] == "event_id_reuse"
    assert len(target.pending_jobs()) == 1


@pytest.mark.parametrize(
    ("overrides", "code", "status"),
    [
        ({"signature": ""}, "signature_missing", 401),
        ({"signature": "   "}, "signature_missing", 401),
        ({"headers": {"X-Ring-Timestamp": ""}}, "timestamp_missing", 401),
        ({"signature": "md5=abc"}, "signature_malformed", 401),
        ({"signature": "sha256=not-hex"}, "signature_malformed", 401),
        ({"headers": {"X-Ring-Timestamp": "not-a-clock"}}, "timestamp_malformed", 401),
        (
            {"timestamp": str(int((T0 + timedelta(minutes=10)).timestamp()))},
            "timestamp_in_future",
            401,
        ),
        (
            {"timestamp": str(int((T0 - timedelta(minutes=10)).timestamp()))},
            "timestamp_stale",
            401,
        ),
        ({"secret": b"some-other-secret-value"}, "signature_mismatch", 401),
        ({"headers": {"X-Ring-Timestamp": "  "}}, "timestamp_missing", 401),
    ],
)
def test_signature_failures_reject_before_any_media_work(
    overrides: dict[str, Any], code: str, status: int
) -> None:
    target = service()
    request = signed_request(**overrides)
    ack = target.handle(request)
    assert ack.accepted is False
    assert ack.status == status
    assert ack.body["code"] == code
    assert ack.job_id is None
    assert target.pending_jobs() == ()
    assert target.store.get_by_event_id("evt-0001") is None


def test_tampered_body_is_rejected_even_with_a_valid_looking_signature() -> None:
    timestamp = str(int(T0.timestamp()))
    original = body()
    signature = compute_signature(secret=SECRET, timestamp=timestamp, body=original)
    tampered = body(device_id="device-attacker")
    ack = service().handle(signed_request(raw=tampered, timestamp=timestamp, signature=signature))
    assert ack.accepted is False
    assert ack.body["code"] == "signature_mismatch"
    assert (
        service()
        .handle(signed_request(raw=tampered, timestamp=timestamp, signature=signature))
        .body["code"]
        == "signature_mismatch"
    )


@pytest.mark.parametrize(
    ("raw", "headers", "code", "status"),
    [
        (b"", {}, "payload_invalid_json", 400),
        (b"{not json", {}, "payload_invalid_json", 400),
        (b"\xff\xfe", {}, "payload_invalid_json", 400),
        (b"[]", {}, "payload_validation_failed", 400),
        (body(event_type="device.unknown"), {}, "payload_validation_failed", 400),
        (body(occurred_at="2026-10-24 09:59:30"), {}, "payload_validation_failed", 400),
        (body(event_id="  "), {}, "payload_validation_failed", 400),
        (body(sequence=[1]), {}, "payload_validation_failed", 400),
        (body(sequence=-1), {}, "payload_validation_failed", 400),
        (body(extra_field=True), {}, "payload_unexpected_field", 400),
        (body(), {"Content-Type": "text/plain"}, "content_type_unsupported", 415),
    ],
)
def test_payload_failures_after_verification(
    raw: bytes, headers: dict[str, str], code: str, status: int
) -> None:
    target = service()
    ack = target.handle(signed_request(raw=raw, headers=headers))
    assert ack.accepted is False
    assert ack.status == status
    assert ack.body["code"] == code
    assert target.pending_jobs() == ()


def test_person_and_location_fields_are_forbidden_in_webhook_payloads() -> None:
    target = service()
    for forbidden in ({"person": {"track": 1}}, {"license_plate": "XX-99-ZZ"}, {"address": "x"}):
        raw = json.dumps({**payload(), **forbidden}, separators=(",", ":")).encode()
        ack = target.handle(signed_request(raw=raw))
        assert ack.accepted is False
        assert ack.body["code"] == "payload_forbidden_field"
    assert target.pending_jobs() == ()


def test_body_size_limits_and_content_type_are_enforced() -> None:
    target = service(max_body_bytes=64)
    oversized = signed_request(raw=body(media_ref="x" * 200))
    ack = target.handle(oversized)
    assert ack.status == 413
    assert ack.body["code"] == "body_too_large"
    assert target.pending_jobs() == ()


def test_method_and_path_routing_reject_unexpected_requests() -> None:
    target = service()
    assert target.handle(signed_request(method="GET")).body["code"] == "method_not_allowed"
    assert target.handle(signed_request(method="GET")).status == 405
    not_found = target.handle(signed_request(path="/v1/other"))
    assert not_found.status == 404
    assert not_found.body["code"] == "not_found"
    assert target.handle(signed_request(path="/webhooks/ring/")).accepted is True


def test_rate_limit_returns_retry_after_and_never_starves_valid_events() -> None:
    clock = Clock()
    target = service(clock=clock, capacity=3)
    for index in range(3):
        ack = target.handle(signed_request(raw=body(event_id=f"evt-{index}"), received_at=T0))
        assert ack.accepted is True
    blocked = target.handle(signed_request(raw=body(event_id="evt-blocked")))
    assert blocked.status == 429
    assert blocked.body["code"] == "rate_limited"
    assert "retry_after=" in str(blocked.body["detail"])
    assert target.rate_limited_total == 1

    # Tokens refill deterministically: four seconds buys two more events.
    clock.advance(4.0)
    assert target.handle(signed_request(raw=body(event_id="evt-after"))).accepted is True


def test_queue_overflow_fails_visibly_without_dropping_data() -> None:
    target = service(queue_depth=1)
    assert target.handle(signed_request()).accepted is True
    second = target.handle(
        signed_request(raw=body(event_id="evt-2", occurred_at="2026-10-24T09:59:40Z"))
    )
    assert second.status == 503
    assert second.body["code"] == "queue_full"
    assert len(target.queue.all_jobs()) == 1


def test_slow_acknowledgement_reports_budget_violation_but_keeps_the_event() -> None:
    clock = Clock()
    target = service(clock=clock)
    request = signed_request()
    clock.advance(6.0)  # handler reaches the tail of the work 6 s after receipt
    ack = target.handle(request)
    assert ack.status == 503
    assert ack.body["code"] == "ack_budget_exceeded"
    # The event is durable, so the provider retry succeeds and dedupes.
    retry = target.handle(signed_request())
    assert retry.accepted is True
    assert retry.duplicate is True
    assert len(target.pending_jobs()) == 1


def test_processing_is_asynchronous_and_never_repeats_a_job() -> None:
    target = service()
    target.handle(signed_request())
    handled: list[str] = []
    (pending,) = target.pending_jobs()

    def handler(job: MediaJob) -> None:
        handled.append(job.event_id)

    assert target.process_pending(handler) == (pending.job_id,)
    assert handled == ["evt-0001"]
    assert target.pending_jobs() == ()
    assert target.process_pending(handler) == ()
    assert handled == ["evt-0001"]
    with pytest.raises(WebhookRejection, match="not_found"):
        target.queue.mark_processed("job-missing", T0)


def test_ack_budget_and_error_code_catalogue_are_documented_constants() -> None:
    assert ACK_BUDGET == timedelta(seconds=5)
    assert {
        "signature_missing",
        "signature_malformed",
        "signature_mismatch",
        "timestamp_stale",
        "timestamp_in_future",
        "event_id_reuse",
        "payload_forbidden_field",
        "rate_limited",
        "body_too_large",
        "ack_budget_exceeded",
    } <= WEBHOOK_ERROR_CODES
    assert all(code == code.lower() for code in WEBHOOK_ERROR_CODES)
    with pytest.raises(ValueError, match="Unknown webhook error code"):
        WebhookRejection("invented_code", status=400)
    with pytest.raises(ValueError, match="Unknown webhook error code"):
        SignatureError("invented_signature_error", status=401)


def test_header_lookup_is_case_insensitive_and_naive_clocks_are_rejected() -> None:
    request = signed_request(headers={"x-ring-signature": "sha256=" + "a" * 64})
    assert request.header("X-RING-SIGNATURE").startswith("sha256=")
    assert request.header("missing-header") is None
    naive = replace(request, received_at=datetime(2026, 10, 24, 10, 0))
    with pytest.raises(ValueError, match="timezone-aware"):
        service().handle(naive)
    bad_clock = RingWebhookService(
        secret=SECRET,
        store=InMemoryWebhookStore(),
        queue=InMemoryMediaJobQueue(),
        clock=lambda: datetime(2026, 10, 24, 10, 0),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        bad_clock.handle(request)


def test_compute_signature_is_stable_and_scheme_prefix_is_required() -> None:
    timestamp = str(int(T0.timestamp()))
    first = compute_signature(secret=SECRET, timestamp=timestamp, body=body())
    assert first == compute_signature(secret=SECRET, timestamp=timestamp, body=body())
    assert first.startswith("sha256=")
    assert len(first.split("=", 1)[1]) == 64
    assert first != compute_signature(secret=SECRET, timestamp=timestamp, body=b"{}")
    with pytest.raises(SignatureError, match="signature_missing"):
        compute_signature(secret=b"", timestamp=timestamp, body=body())
