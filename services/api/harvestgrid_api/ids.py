"""Canonical ULID helpers.

The shared engineering standard requires 26-character canonical ULIDs for identifiers and
correlation IDs. Entropy is injectable so tests can produce deterministic values.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ULID_LENGTH = 26
MAX_TIMESTAMP_MS = (1 << 48) - 1


def encode_ulid(timestamp_ms: int, randomness: bytes) -> str:
    """Encode a 48-bit millisecond timestamp plus 80 bits of entropy as Crockford base32."""

    if not 0 <= timestamp_ms <= MAX_TIMESTAMP_MS:
        raise ValueError("ulid timestamp out of range")
    if len(randomness) != 10:
        raise ValueError("ulid randomness must be exactly 10 bytes")
    value = (timestamp_ms << 80) | int.from_bytes(randomness, "big")
    characters = []
    for shift in range(130 - 5, -1, -5):
        characters.append(CROCKFORD[(value >> shift) & 0x1F])
    encoded = "".join(characters)
    return encoded[-ULID_LENGTH:]


def new_ulid(
    clock: Callable[[], datetime] | None = None,
    entropy: Callable[[int], bytes] | None = None,
) -> str:
    """Generate a ULID from the supplied clock and entropy source."""

    now = (clock or (lambda: datetime.now(UTC)))()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("ulid clock must be timezone-aware")
    timestamp_ms = int(now.timestamp() * 1000)
    source = entropy or secrets.token_bytes
    return encode_ulid(timestamp_ms, source(10))


def is_ulid(value: str) -> bool:
    if len(value) != ULID_LENGTH:
        return False
    if any(character not in CROCKFORD for character in value):
        return False
    # Crockford encoding of a 48-bit timestamp keeps the first character within 0-7.
    return value[0] in CROCKFORD[:8]
