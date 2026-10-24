"""Idempotency-Key handling for retryable mutations.

The shared standard requires storing the key, a request hash, the status, and the response for
at least 24 hours. A replay with the same key and the same request returns the original
result; a different request under the same key returns ``409 idempotency_key_reused``.

The store is deliberately small and in-memory for this slice. It is a fixture, not a database,
and it is keyed by ``(actor, route)`` so two actors cannot collide.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .problems import bad_request, conflict

IDEMPOTENCY_HEADER = "Idempotency-Key"
MIN_KEY_LENGTH = 8
MAX_KEY_LENGTH = 128
RETENTION = timedelta(hours=24)
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:/+-]+$")


def canonical_request_hash(payload: Any, *, route: str) -> str:
    """Stable SHA-256 over the route plus a canonical JSON encoding of the payload."""

    encoded = json.dumps(
        {"route": route, "body": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require_idempotency_key(value: str | None) -> str:
    if value is None or not value.strip():
        raise bad_request(
            "idempotency_key_required",
            f"This mutation requires an {IDEMPOTENCY_HEADER} header so retries stay safe.",
        )
    key = value.strip()
    if not MIN_KEY_LENGTH <= len(key) <= MAX_KEY_LENGTH:
        raise bad_request(
            "idempotency_key_invalid",
            f"{IDEMPOTENCY_HEADER} must be {MIN_KEY_LENGTH}-{MAX_KEY_LENGTH} characters.",
        )
    if not _KEY_PATTERN.match(key):
        raise bad_request(
            "idempotency_key_invalid",
            f"{IDEMPOTENCY_HEADER} may contain only letters, digits, and ._:/+-",
        )
    return key


@dataclass(frozen=True)
class StoredResponse:
    request_sha256: str
    status: int
    body: dict[str, Any]
    stored_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return now - self.stored_at > RETENTION


class IdempotencyStore:
    """Protocol-shaped base class; the in-memory variant is a fixture."""

    def get(self, scope: str, key: str, now: datetime) -> StoredResponse | None:
        raise NotImplementedError

    def put(self, scope: str, key: str, response: StoredResponse) -> None:
        raise NotImplementedError


class InMemoryIdempotencyStore(IdempotencyStore):
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], StoredResponse] = {}

    def get(self, scope: str, key: str, now: datetime) -> StoredResponse | None:
        stored = self._entries.get((scope, key))
        if stored is None:
            return None
        if stored.is_expired(now):
            self._entries.pop((scope, key), None)
            return None
        return stored

    def put(self, scope: str, key: str, response: StoredResponse) -> None:
        self._entries[(scope, key)] = response

    def __len__(self) -> int:
        return len(self._entries)


def resolve_replay(
    store: IdempotencyStore,
    *,
    scope: str,
    key: str,
    request_sha256: str,
    now: datetime,
) -> StoredResponse | None:
    """Return the stored response for an identical replay, or raise on key reuse."""

    stored = store.get(scope, key, now)
    if stored is None:
        return None
    if stored.request_sha256 != request_sha256:
        raise conflict(
            "idempotency_key_reused",
            "This Idempotency-Key was already used with a different request payload.",
        )
    return stored


def store_response(
    store: IdempotencyStore,
    *,
    scope: str,
    key: str,
    request_sha256: str,
    status: int,
    body: dict[str, Any],
    now: datetime,
) -> None:
    store.put(
        scope,
        key,
        StoredResponse(
            request_sha256=request_sha256,
            status=status,
            body=body,
            stored_at=now.astimezone(UTC),
        ),
    )
