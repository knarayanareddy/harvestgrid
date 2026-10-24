"""Pickup-alert message templates with hard no-address guarantees.

An SMS leaves the trust boundary of the application: it is stored by a carrier, may appear
on a lock screen, and cannot be revoked. HarvestGrid therefore never puts an exact address,
access code, or owner identity into a message. The template receives a coarse area label and
a short constraint list, and the renderer refuses to emit text that contains any of the
restricted strings it was given for comparison (``SEC-HG-007``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

TEMPLATE_VERSION = "pickup-v2"
MAX_SMS_CHARS = 480
SMS_CHARS_PER_SEGMENT = 153
MIN_REVEAL_LEAD_HOURS = 2

FICTIONAL_ADDRESS_MARKER = "FIXTURE"


class TemplateError(ValueError):
    """Template rendering refused; ``code`` is stable."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class AlertKind(StrEnum):
    INVITATION = "invitation"
    REMINDER = "reminder"
    CANCELLATION = "cancellation"


@dataclass(frozen=True)
class PickupAlert:
    """Everything a volunteer may be told outside the app.

    There is deliberately no address, access-code, coordinate, owner-name, or recipient
    field on this type.
    """

    glean_id: str
    crop_label: str
    coarse_area_label: str
    window_start: datetime
    window_end: datetime
    access_constraints: tuple[str, ...] = ()
    safety_notes: tuple[str, ...] = ()
    coordinator_channel: str = "in-app coordinator chat"
    kind: AlertKind = AlertKind.INVITATION
    template_version: str = TEMPLATE_VERSION

    def __post_init__(self) -> None:
        if not self.glean_id.strip():
            raise TemplateError("alert_glean_id_required")
        if not self.crop_label.strip() or not self.coarse_area_label.strip():
            raise TemplateError("alert_description_required")
        for name, value in (
            ("window_start", self.window_start),
            ("window_end", self.window_end),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise TemplateError("alert_window_requires_timezone", name)
        if self.window_end <= self.window_start:
            raise TemplateError("alert_window_unordered")
        for constraint in self.access_constraints:
            if not constraint.strip():
                raise TemplateError("empty_access_constraint")
            if len(constraint) > 80:
                raise TemplateError("access_constraint_too_long")
        if not self.coordinator_channel.strip():
            raise TemplateError("coordinator_channel_required")

    @property
    def reveal_not_before(self) -> datetime:
        """Exact details unlock two hours before the window, never earlier."""

        from datetime import timedelta

        return self.window_start - timedelta(hours=MIN_REVEAL_LEAD_HOURS)

    def window_label(self, *, locale: str = "en") -> str:
        start = self.window_start.astimezone(UTC)
        end = self.window_end.astimezone(UTC)
        if start.date() == end.date():
            return f"{start:%a %d %b %H:%M}-{end:%H:%M} UTC"
        return f"{start:%a %d %b %H:%M} to {end:%a %d %b %H:%M} UTC"


def _ascii_phone_guard(text: str) -> None:
    """Reject obvious phone-like or house-number-plus-street sequences."""

    if re.search(r"\+\d{8,15}", text):
        raise TemplateError("message_contains_phone_number")
    address_pattern = (
        r"\b\d{1,4}\s+[A-Z][a-z]{2,}\s+"
        r"(Street|Strasse|Straat|Straße|Road|Lane|Avenue)\b"
    )
    if re.search(address_pattern, text):
        raise TemplateError("message_contains_address_pattern")


def assert_no_restricted_content(text: str, restricted_markers: tuple[str, ...]) -> None:
    """Refuse to send if any known restricted string (address, access note) appears."""

    lowered = text.casefold()
    for marker in restricted_markers:
        probe = marker.strip().casefold()
        if len(probe) < 4:
            continue
        if probe in lowered:
            raise TemplateError("message_contains_restricted_content", "marker")


def render_alert(
    alert: PickupAlert,
    *,
    locale: str = "en",
    restricted_markers: tuple[str, ...] = (),
) -> str:
    """Render one short SMS body; raises rather than leaking restricted detail."""

    if locale.split("-")[0] not in {"en", "nl"}:
        raise TemplateError("locale_unsupported", locale)
    constraints = "; ".join(alert.access_constraints) or "none beyond general safety"
    safety = "; ".join(alert.safety_notes)
    if alert.kind is AlertKind.CANCELLATION:
        body = (
            f"HarvestGrid: glean {alert.glean_id} is cancelled. "
            f"No action needed. Questions: {alert.coordinator_channel}. "
            f"Ref {alert.template_version}"
        )
    else:
        lead = "Reminder" if alert.kind is AlertKind.REMINDER else "New glean"
        body = (
            f"HarvestGrid {lead}: {alert.crop_label} in {alert.coarse_area_label}, "
            f"{alert.window_label(locale=locale)}. "
            f"Access: {constraints}. "
        )
        if safety:
            body += f"Safety: {safety}. "
        body += (
            "Exact address unlocks in the app 2h before. "
            f"Questions: {alert.coordinator_channel}. Ref {alert.template_version}"
        )
    if len(body) > MAX_SMS_CHARS:
        raise TemplateError("message_too_long", str(len(body)))
    assert_no_restricted_content(body, restricted_markers)
    _ascii_phone_guard(body)
    return body


def message_segments(body: str) -> int:
    """GSM-style segment estimate; we refuse anything above three segments."""

    if not body:
        return 0
    return max(1, -(-len(body) // SMS_CHARS_PER_SEGMENT))


def redact_phone(phone: str) -> str:
    """Log-safe reference: keep the country prefix and last two digits only."""

    digits = "".join(character for character in phone if character.isdigit())
    if len(digits) < 5:
        return "redacted"
    return f"+{digits[:1]}***{digits[-2:]}"


def message_fingerprint(body: str) -> str:
    """Body digest recorded instead of the body itself."""

    import hashlib

    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def alert_marker(alert: PickupAlert) -> str:
    """Fictional-fixture marker guard: fixture alerts stay labelled in transit."""

    return FICTIONAL_ADDRESS_MARKER if "fixture" in alert.coarse_area_label.casefold() else ""
