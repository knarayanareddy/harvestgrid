"""Ring webhook ingestion: HMAC verification, raw bodies, fast ACK, idempotency.

Implements ``INT-HG-003`` for ``services/ring-adapter``. The service is framework
agnostic; :mod:`harvestgrid_ring_adapter.server` wraps it in a small stdlib HTTP adapter
and tests drive :meth:`RingWebhookService.handle` directly with raw request bytes.

Ordering is deliberate and tested:

1. bounded raw body (no JSON re-serialization anywhere);
2. documented HMAC-SHA256 signature over ``timestamp + "." + raw_body`` with a bounded
   timestamp window, so forged or replayed deliveries are rejected before parsing;
3. single JSON parse with a strict, minimal event schema;
4. per-connection token-bucket rate limiting;
5. idempotent de-duplication by event ID and by payload digest;
6. queue enqueue and a bounded acknowledgement (five seconds end to end).

Rejected requests never reach the media queue, so no media is processed for an invalid
signature. The canonical header names and event mapping are an adapter contract that must
be re-verified against Ring's documented webhook reference during preflight; the fixture
tests here prove our behaviour, not Ring's.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

ACK_BUDGET = timedelta(seconds=5)
TIMESTAMP_TOLERANCE = timedelta(minutes=5)
MAX_BODY_BYTES = 64 * 1024
SIGNATURE_HEADER = "x-ring-signature"
TIMESTAMP_HEADER = "x-ring-timestamp"
SIGNATURE_SCHEME = "sha256"
ALLOWED_EVENT_TYPES = frozenset(
    {
        "device.motion",
        "device.snapshot",
        "device.live_view_unavailable",
        "device.offline",
        "device.online",
    }
)
EVENT_FIELDS = frozenset(
    {"event_id", "event_type", "device_id", "connection_id", "occurred_at", "media_ref", "sequence"}
)
FORBIDDEN_FIELDS = frozenset(
    {
        "person",
        "people",
        "face",
        "faces",
        "plate",
        "license_plate",
        "face_embedding",
        "person_track",
        "raw_media",
        "media_bytes",
        "address",
        "access_notes",
        "coordinates",
    }
)

WEBHOOK_ERROR_CODES = frozenset(
    {
        "ack_budget_exceeded",
        "body_too_large",
        "content_type_unsupported",
        "event_id_reuse",
        "method_not_allowed",
        "not_found",
        "payload_forbidden_field",
        "payload_invalid_json",
        "payload_unexpected_field",
        "payload_validation_failed",
        "queue_full",
        "rate_limited",
        "signature_malformed",
        "signature_missing",
        "signature_mismatch",
        "timestamp_in_future",
        "timestamp_malformed",
        "timestamp_missing",
        "timestamp_stale",
    }
)


class WebhookRejection(Exception):
    """Deterministic rejection with an HTTP status; never carries payload content."""

    def __init__(self, code: str, *, status: int, detail: str = "") -> None:
        if code not in WEBHOOK_ERROR_CODES:
            raise ValueError(f"Unknown webhook error code: {code}")
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


class SignatureError(WebhookRejection):
    """A delivery whose HMAC, scheme, or timestamp does not verify."""


@dataclass(frozen=True)
class WebhookRequest:
    method: str
    path: str
    headers: Mapping[str, str]
    body: bytes
    received_at: datetime

    def header(self, name: str) -> str | None:
        lowered = {key.lower(): value for key, value in self.headers.items()}
        return lowered.get(name.lower())


@dataclass(frozen=True)
class MediaJob:
    """Minimal queued work item; it never contains media bytes or person metadata."""

    job_id: str
    connection_id: str
    device_id: str
    event_id: str
    event_type: str
    occurred_at: datetime
    payload_sha256: str
    media_ref: str | None
    enqueued_at: datetime
    provenance: str = "live"
    processed: bool = False


@dataclass(frozen=True)
class WebhookAck:
    status: int
    body: Mapping[str, Any]
    acknowledgement_ms: float
    accepted: bool
    duplicate: bool
    job_id: str | None = None
    deduplicated_by: str | None = None


@dataclass(frozen=True)
class StoredAck:
    """Persisted idempotency record: request digest and the original response body."""

    event_id: str
    payload_sha256: str
    job_id: str | None
    stored_at: datetime
    response: Mapping[str, Any]


class WebhookStore(Protocol):
    """Durable idempotency port (production: transactional store, not a fixture)."""

    def get_by_event_id(self, event_id: str) -> StoredAck | None: ...

    def get_by_payload(self, payload_sha256: str) -> StoredAck | None: ...

    def put(self, record: StoredAck) -> None: ...


class InMemoryWebhookStore:
    """Local fixture store; single-process, non-durable, and explicitly labelable."""

    def __init__(self) -> None:
        self._by_event: dict[str, StoredAck] = {}
        self._by_payload: dict[str, StoredAck] = {}

    def get_by_event_id(self, event_id: str) -> StoredAck | None:
        return self._by_event.get(event_id)

    def get_by_payload(self, payload_sha256: str) -> StoredAck | None:
        return self._by_payload.get(payload_sha256)

    def put(self, record: StoredAck) -> None:
        self._by_event[record.event_id] = record
        self._by_payload.setdefault(record.payload_sha256, record)


class MediaJobQueue(Protocol):
    def enqueue(self, job: MediaJob) -> None: ...

    def pending(self) -> tuple[MediaJob, ...]: ...


@dataclass
class InMemoryMediaJobQueue:
    """Fixture queue standing in for SQS; no media is read or copied here."""

    max_depth: int = 1_000
    _jobs: list[MediaJob] = field(default_factory=list)

    def enqueue(self, job: MediaJob) -> None:
        if len(self._jobs) >= self.max_depth:
            raise WebhookRejection("queue_full", status=503, detail="queue_depth")
        self._jobs.append(job)

    def pending(self) -> tuple[MediaJob, ...]:
        return tuple(job for job in self._jobs if not job.processed)

    def all_jobs(self) -> tuple[MediaJob, ...]:
        return tuple(self._jobs)

    def mark_processed(self, job_id: str, at: datetime) -> MediaJob:
        for index, job in enumerate(self._jobs):
            if job.job_id == job_id and not job.processed:
                updated = MediaJob(
                    job_id=job.job_id,
                    connection_id=job.connection_id,
                    device_id=job.device_id,
                    event_id=job.event_id,
                    event_type=job.event_type,
                    occurred_at=job.occurred_at,
                    payload_sha256=job.payload_sha256,
                    media_ref=job.media_ref,
                    enqueued_at=job.enqueued_at,
                    provenance=job.provenance,
                    processed=True,
                )
                self._jobs[index] = updated
                return updated
        raise WebhookRejection("not_found", status=404, detail="job_not_pending")


@dataclass
class TokenBucketRateLimiter:
    """Deterministic per-connection token bucket with ``Retry-After`` math."""

    capacity: int = 30
    refill_per_second: float = 0.5
    _tokens: dict[str, float] = field(default_factory=dict)
    _updated_at: dict[str, datetime] = field(default_factory=dict)

    def check(self, key: str, now: datetime) -> float | None:
        """Return ``None`` when allowed, otherwise the number of seconds to wait."""

        self._tokens.setdefault(key, float(self.capacity))
        self._updated_at.setdefault(key, now)
        last = self._updated_at[key]
        elapsed = max(0.0, (now - last).total_seconds())
        tokens = min(float(self.capacity), self._tokens[key] + elapsed * self.refill_per_second)
        self._tokens[key] = tokens
        self._updated_at[key] = now
        if tokens < 1.0:
            missing = 1.0 - tokens
            wait = missing / self.refill_per_second if self.refill_per_second else 60.0
            return max(1.0, float(int(wait) + 1))
        self._tokens[key] = tokens - 1.0
        return None


def compute_signature(*, secret: bytes, timestamp: str, body: bytes) -> str:
    """Documented signing input: ``sha256(timestamp + "." + raw_body)``."""

    if not secret:
        raise SignatureError("signature_missing", status=401, detail="secret_unconfigured")
    message = timestamp.encode("utf-8") + b"." + body
    digest = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_SCHEME}={digest}"


def _canonical_timestamp(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise SignatureError("timestamp_missing", status=401)
    try:
        parsed = datetime.fromtimestamp(float(text), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        raise SignatureError("timestamp_malformed", status=401) from None
    return parsed


def _verify_signature(
    *,
    request: WebhookRequest,
    secret: bytes,
    now: datetime,
    tolerance: timedelta,
) -> None:
    signature = request.header(SIGNATURE_HEADER)
    if signature is None or not signature.strip():
        raise SignatureError("signature_missing", status=401)
    timestamp_header = request.header(TIMESTAMP_HEADER)
    if timestamp_header is None or not timestamp_header.strip():
        raise SignatureError("timestamp_missing", status=401)
    timestamp = _canonical_timestamp(timestamp_header)
    if timestamp > now + tolerance:
        raise SignatureError("timestamp_in_future", status=401, detail="clock_skew")
    if now - timestamp > tolerance:
        raise SignatureError("timestamp_stale", status=401, detail="replay_window")

    scheme, _, digest = signature.strip().partition("=")
    if scheme.lower() != SIGNATURE_SCHEME or len(digest) != 64:
        raise SignatureError("signature_malformed", status=401)
    if any(character not in "0123456789abcdefABCDEF" for character in digest):
        raise SignatureError("signature_malformed", status=401)
    expected = compute_signature(
        secret=secret, timestamp=timestamp_header.strip(), body=request.body
    )
    if not hmac.compare_digest(expected, f"{SIGNATURE_SCHEME}={digest.lower()}"):
        raise SignatureError("signature_mismatch", status=401)


def _parse_event(body: bytes) -> dict[str, Any]:
    if not body:
        raise WebhookRejection("payload_invalid_json", status=400, detail="empty")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise WebhookRejection("payload_invalid_json", status=400) from None
    if not isinstance(parsed, dict):
        raise WebhookRejection("payload_validation_failed", status=400, detail="object_required")
    keys = {str(key) for key in parsed}
    forbidden = sorted(keys & FORBIDDEN_FIELDS)
    if forbidden:
        raise WebhookRejection("payload_forbidden_field", status=400, detail=",".join(forbidden))
    unexpected = sorted(keys - EVENT_FIELDS)
    if unexpected:
        raise WebhookRejection("payload_unexpected_field", status=400, detail=",".join(unexpected))
    for name in ("event_id", "event_type", "device_id", "connection_id", "occurred_at"):
        value = parsed.get(name)
        if not isinstance(value, str) or not value.strip():
            raise WebhookRejection("payload_validation_failed", status=400, detail=f"field:{name}")
    if parsed["event_type"] not in ALLOWED_EVENT_TYPES:
        raise WebhookRejection(
            "payload_validation_failed", status=400, detail="event_type_unsupported"
        )
    try:
        occurred_at = datetime.fromisoformat(str(parsed["occurred_at"]).replace("Z", "+00:00"))
    except ValueError:
        raise WebhookRejection(
            "payload_validation_failed", status=400, detail="occurred_at_invalid"
        ) from None
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise WebhookRejection(
            "payload_validation_failed", status=400, detail="occurred_at_timezone"
        )
    parsed["occurred_at"] = occurred_at.astimezone(UTC)
    sequence = parsed.get("sequence")
    if sequence is not None and (
        isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0
    ):
        raise WebhookRejection("payload_validation_failed", status=400, detail="sequence")
    media_ref = parsed.get("media_ref")
    if media_ref is not None and (not isinstance(media_ref, str) or not media_ref.strip()):
        raise WebhookRejection("payload_validation_failed", status=400, detail="media_ref")
    if len(str(media_ref or "")) > 200:
        raise WebhookRejection("payload_validation_failed", status=400, detail="media_ref")
    return parsed


def _payload_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _job_id(event_id: str, payload_sha256: str) -> str:
    digest = hashlib.sha256(f"{event_id}:{payload_sha256}".encode()).hexdigest()
    return f"job-{digest[:24]}"


@dataclass
class RingWebhookService:
    """Verifies and accepts Ring webhook deliveries; media work happens asynchronously."""

    secret: bytes
    store: WebhookStore
    queue: InMemoryMediaJobQueue
    clock: Callable[[], datetime]
    rate_limiter: TokenBucketRateLimiter = field(default_factory=TokenBucketRateLimiter)
    ack_budget: timedelta = ACK_BUDGET
    timestamp_tolerance: timedelta = TIMESTAMP_TOLERANCE
    max_body_bytes: int = MAX_BODY_BYTES
    rate_limited_total: int = 0

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("webhook clock must return timezone-aware timestamps")
        return now.astimezone(UTC)

    def handle(self, request: WebhookRequest) -> WebhookAck:
        received = request.received_at
        if received.tzinfo is None or received.utcoffset() is None:
            raise ValueError("webhook received_at must be timezone-aware")
        try:
            return self._handle(request, received.astimezone(UTC))
        except WebhookRejection as rejection:
            elapsed_ms = max(0.0, (self._now() - received.astimezone(UTC)).total_seconds() * 1000.0)
            return WebhookAck(
                status=rejection.status,
                body={
                    "status": "rejected",
                    "code": rejection.code,
                    "detail": rejection.detail,
                },
                acknowledgement_ms=round(elapsed_ms, 3),
                accepted=False,
                duplicate=False,
            )

    def _handle(self, request: WebhookRequest, received: datetime) -> WebhookAck:
        if request.method.upper() != "POST":
            raise WebhookRejection("method_not_allowed", status=405)
        if request.path.rstrip("/") not in {"/v1/ring/webhooks", "/webhooks/ring"}:
            raise WebhookRejection("not_found", status=404)
        content_type = (request.header("content-type") or "application/json").split(";")[0]
        if content_type.strip().lower() != "application/json":
            raise WebhookRejection(
                "content_type_unsupported", status=415, detail=content_type.strip().lower()
            )
        if not isinstance(request.body, bytes):
            raise WebhookRejection("payload_invalid_json", status=400, detail="bytes_required")
        if len(request.body) > self.max_body_bytes:
            raise WebhookRejection("body_too_large", status=413, detail=str(self.max_body_bytes))

        # Signature verification precedes any parsing of the body.
        _verify_signature(
            request=request,
            secret=self.secret,
            now=self._now(),
            tolerance=self.timestamp_tolerance,
        )
        event = _parse_event(request.body)
        payload_sha256 = _payload_sha256(request.body)

        wait = self.rate_limiter.check(str(event["connection_id"]), self._now())
        if wait is not None:
            self.rate_limited_total += 1
            raise WebhookRejection("rate_limited", status=429, detail=f"retry_after={wait:.0f}")

        event_id = str(event["event_id"])
        existing = self.store.get_by_event_id(event_id)
        if existing is not None:
            if existing.payload_sha256 != payload_sha256:
                raise WebhookRejection("event_id_reuse", status=409, detail="payload_mismatch")
            return self._ack(
                received,
                event_id=event_id,
                job_id=existing.job_id,
                body=existing.response,
                duplicate=True,
                deduplicated_by="event_id",
            )
        payload_replay = self.store.get_by_payload(payload_sha256)
        if payload_replay is not None:
            return self._ack(
                received,
                event_id=event_id,
                job_id=payload_replay.job_id,
                body=payload_replay.response,
                duplicate=True,
                deduplicated_by="payload_sha256",
            )

        job = MediaJob(
            job_id=_job_id(event_id, payload_sha256),
            connection_id=str(event["connection_id"]),
            device_id=str(event["device_id"]),
            event_id=event_id,
            event_type=str(event["event_type"]),
            occurred_at=event["occurred_at"],
            payload_sha256=payload_sha256,
            media_ref=str(event["media_ref"]) if event.get("media_ref") else None,
            enqueued_at=self._now(),
        )
        self.queue.enqueue(job)
        response = {
            "status": "accepted",
            "event_id": event_id,
            "job_id": job.job_id,
            "event_type": job.event_type,
        }
        self.store.put(
            StoredAck(
                event_id=event_id,
                payload_sha256=payload_sha256,
                job_id=job.job_id,
                stored_at=self._now(),
                response=response,
            )
        )
        ack = self._ack(
            received,
            event_id=event_id,
            job_id=job.job_id,
            body=response,
            duplicate=False,
            deduplicated_by=None,
        )
        if ack.acknowledgement_ms > self.ack_budget.total_seconds() * 1000.0:
            # The event is durable, but we must not pretend a slow ACK met the budget.
            raise WebhookRejection(
                "ack_budget_exceeded", status=503, detail=f"{ack.acknowledgement_ms:.0f}ms"
            )
        return ack

    def _ack(
        self,
        received: datetime,
        *,
        event_id: str,
        job_id: str | None,
        body: Mapping[str, Any],
        duplicate: bool,
        deduplicated_by: str | None,
    ) -> WebhookAck:
        elapsed_ms = max(0.0, (self._now() - received).total_seconds() * 1000.0)
        payload = {
            "status": body.get("status", "accepted"),
            "event_id": event_id,
            "job_id": job_id,
            "duplicate": duplicate,
        }
        return WebhookAck(
            status=200,
            body=payload,
            acknowledgement_ms=round(elapsed_ms, 3),
            accepted=True,
            duplicate=duplicate,
            job_id=job_id,
            deduplicated_by=deduplicated_by,
        )

    def pending_jobs(self) -> tuple[MediaJob, ...]:
        return self.queue.pending()

    def process_pending(self, handler: Callable[[MediaJob], None]) -> tuple[str, ...]:
        """Drain the queue once; a processed job is never delivered twice."""

        processed: list[str] = []
        for job in self.queue.pending():
            handler(job)
            self.queue.mark_processed(job.job_id, self._now())
            processed.append(job.job_id)
        return tuple(processed)
