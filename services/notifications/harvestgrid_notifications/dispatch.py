"""Coordinator-approved volunteer dispatch over SMS.

Rules encoded here (``SEC-HG-006``, ``INT-HG-007``, ``NFR-HG-004``):

* Nothing is dispatched without a current coordinator approval and current owner approval;
  there is no code path that sends on a schedule or after a ranking result alone.
* A volunteer is messaged only after explicit SMS opt-in, and never during local quiet
  hours.
* Repeat dispatch for the same ``glean/state/recipient/template/version`` is suppressed, and
  each recipient has a hard daily cap.
* Messages carry a coarse area, the time window, and short access constraints. They never
  carry the address, access code, or owner identity; the renderer refuses to send if it
  detects a restricted string it was given for comparison.
* LEDGER entries keep a body digest and provider receipt, never the message text or phone
  number.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .templates import (
    TEMPLATE_VERSION,
    AlertKind,
    PickupAlert,
    TemplateError,
    message_fingerprint,
    message_segments,
    redact_phone,
    render_alert,
)
from .transports import NotificationError, SmsMessage, SmsTransport

DEFAULT_TIMEZONE = "Europe/Amsterdam"
QUIET_HOURS_START = 21
QUIET_HOURS_END = 7
MAX_PER_RECIPIENT_PER_DAY = 3
MAX_SENDS_PER_TICK = 50
TEMPLATE_NAME = "pickup"
DAY = timedelta(days=1)

DispatchStatus = Literal["sent", "skipped", "deferred", "failed"]


class Channel(StrEnum):
    SMS = "sms"
    EMAIL = "email"


@dataclass(frozen=True)
class VolunteerContact:
    """Messaging preferences. No address, access note, or recipient data belongs here."""

    volunteer_id: str
    channel: Channel = Channel.SMS
    phone_e164: str | None = None
    sms_opt_in: bool = False
    sms_opt_in_version: str | None = None
    locale: str = "en"
    timezone: str = DEFAULT_TIMEZONE
    suppressed: bool = False

    def __post_init__(self) -> None:
        if not self.volunteer_id.strip():
            raise NotificationError("contact_volunteer_id_required")
        if self.phone_e164 is not None and (
            not self.phone_e164.startswith("+") or not self.phone_e164[1:].isdigit()
        ):
            raise NotificationError("contact_phone_invalid", "e164_required")
        if self.sms_opt_in and not (self.sms_opt_in_version or "").strip():
            raise NotificationError("contact_opt_in_version_required")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise NotificationError("contact_timezone_unknown", self.timezone) from None

    @property
    def log_reference(self) -> str:
        """Phone redaction for logs; the raw number is never written anywhere."""

        return redact_phone(self.phone_e164 or "")


@dataclass(frozen=True)
class QuietHours:
    start_hour: int = QUIET_HOURS_START
    end_hour: int = QUIET_HOURS_END

    def __post_init__(self) -> None:
        for hour in (self.start_hour, self.end_hour):
            if not 0 <= hour <= 23:
                raise NotificationError("quiet_hours_invalid", str(hour))

    def is_quiet(self, when: datetime, timezone: str) -> bool:
        if when.tzinfo is None or when.utcoffset() is None:
            raise NotificationError("quiet_hours_requires_aware_timestamp")
        local_hour = when.astimezone(ZoneInfo(timezone)).hour
        if self.start_hour == self.end_hour:
            return False
        if self.start_hour < self.end_hour:
            return self.start_hour <= local_hour < self.end_hour
        return local_hour >= self.start_hour or local_hour < self.end_hour


@dataclass(frozen=True)
class DispatchAuthorization:
    """Coordinator approval evidence required before any message leaves the system."""

    glean_id: str
    coordinator_id: str
    approved_by: str
    approved_at: datetime
    owner_approval_current: bool
    glean_state: str

    def __post_init__(self) -> None:
        if not self.glean_id.strip() or not self.coordinator_id.strip():
            raise NotificationError("authorization_incomplete", "identifiers")
        if self.approved_at.tzinfo is None or self.approved_at.utcoffset() is None:
            raise NotificationError("authorization_incomplete", "approved_at")

    def problem(self, alert: PickupAlert) -> str | None:
        if self.approved_by != self.coordinator_id:
            return "coordinator_approval_required"
        if not self.owner_approval_current:
            return "owner_approval_missing"
        if self.glean_id != alert.glean_id:
            return "glean_mismatch"
        if alert.kind is AlertKind.CANCELLATION:
            if self.glean_state != "cancelled":
                return "cancellation_requires_cancelled_state"
            return None
        if self.glean_state not in {"invited", "confirmed"}:
            return "glean_state_not_dispatchable"
        return None


@dataclass(frozen=True)
class NotificationAttempt:
    idempotency_key: str
    volunteer_id: str
    provider: str
    status: str
    at: datetime
    message_id: str | None = None
    body_sha256: str | None = None
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "idempotency_key": self.idempotency_key,
            "volunteer_id": self.volunteer_id,
            "provider": self.provider,
            "status": self.status,
            "at": self.at.isoformat(),
            "message_id": self.message_id,
            "body_sha256": self.body_sha256,
            "error_code": self.error_code,
        }


class NotificationLedger(Protocol):
    def has_sent(self, idempotency_key: str) -> bool: ...

    def record(self, attempt: NotificationAttempt) -> None: ...

    def sent_since(self, volunteer_id: str, since: datetime) -> int: ...

    def attempts(self) -> tuple[NotificationAttempt, ...]: ...


@dataclass
class InMemoryNotificationLedger:
    """Fixture ledger: process-local, stores digests and IDs only."""

    _entries: list[NotificationAttempt] = field(default_factory=list)
    _sent_keys: set[str] = field(default_factory=set)

    def has_sent(self, idempotency_key: str) -> bool:
        return idempotency_key in self._sent_keys

    def record(self, attempt: NotificationAttempt) -> None:
        self._entries.append(attempt)
        if attempt.status == "sent":
            self._sent_keys.add(attempt.idempotency_key)

    def sent_since(self, volunteer_id: str, since: datetime) -> int:
        return sum(
            1
            for entry in self._entries
            if entry.volunteer_id == volunteer_id and entry.status == "sent" and entry.at >= since
        )

    def attempts(self) -> tuple[NotificationAttempt, ...]:
        return tuple(self._entries)


@dataclass(frozen=True)
class RecipientOutcome:
    volunteer_id: str
    status: DispatchStatus
    reason: str
    message_id: str | None = None
    segments: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "volunteer_id": self.volunteer_id,
            "status": self.status,
            "reason": self.reason,
            "message_id": self.message_id,
            "segments": self.segments,
        }


@dataclass(frozen=True)
class DispatchReport:
    glean_id: str
    template_version: str
    kind: str
    at: datetime
    outcomes: tuple[RecipientOutcome, ...]

    def _count(self, status: DispatchStatus) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == status)

    @property
    def sent(self) -> int:
        return self._count("sent")

    @property
    def skipped(self) -> int:
        return self._count("skipped")

    @property
    def deferred(self) -> int:
        return self._count("deferred")

    @property
    def failed(self) -> int:
        return self._count("failed")

    def as_dict(self) -> dict[str, Any]:
        return {
            "glean_id": self.glean_id,
            "template_version": self.template_version,
            "kind": self.kind,
            "at": self.at.isoformat(),
            "sent": self.sent,
            "skipped": self.skipped,
            "deferred": self.deferred,
            "failed": self.failed,
            "outcomes": [outcome.as_dict() for outcome in self.outcomes],
        }


def idempotency_key(
    *, glean_id: str, glean_state: str, volunteer_id: str, template_version: str
) -> str:
    """``glean/state/recipient/template/version`` from the engineering standard."""

    parts = (glean_id, glean_state, volunteer_id, TEMPLATE_NAME, template_version)
    if any(not part.strip() for part in parts):
        raise NotificationError("idempotency_key_incomplete")
    return "/".join(parts)


@dataclass
class NotificationDispatchService:
    transport: SmsTransport
    ledger: NotificationLedger
    clock: Callable[[], datetime]
    quiet_hours: QuietHours = field(default_factory=QuietHours)
    max_per_recipient_per_day: int = MAX_PER_RECIPIENT_PER_DAY
    max_sends_per_tick: int = MAX_SENDS_PER_TICK

    def __post_init__(self) -> None:
        if self.max_per_recipient_per_day < 1 or self.max_sends_per_tick < 1:
            raise NotificationError("dispatch_limits_invalid")

    def _now(self, now: datetime | None = None) -> datetime:
        value = now if now is not None else self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise NotificationError("dispatch_clock_requires_timezone")
        return value.astimezone(UTC)

    def dispatch(
        self,
        *,
        alert: PickupAlert,
        recipients: tuple[VolunteerContact, ...],
        authorization: DispatchAuthorization,
        restricted_markers: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> DispatchReport:
        at = self._now(now)
        problem = authorization.problem(alert)
        if problem is not None:
            raise NotificationError("dispatch_not_authorized", problem)

        outcomes: list[RecipientOutcome] = []
        sends_this_tick = 0
        for contact in sorted(recipients, key=lambda item: item.volunteer_id):
            key = idempotency_key(
                glean_id=alert.glean_id,
                glean_state=authorization.glean_state,
                volunteer_id=contact.volunteer_id,
                template_version=alert.template_version,
            )
            decision = self._precheck(
                contact=contact,
                alert=alert,
                key=key,
                at=at,
                sends_this_tick=sends_this_tick,
                restricted_markers=restricted_markers,
            )
            if isinstance(decision, RecipientOutcome):
                outcomes.append(decision)
                self._record_blocked(decision, contact, key, at)
                continue

            body, segments = decision
            try:
                receipt = self.transport.send(
                    SmsMessage(
                        to=contact.phone_e164 or "",
                        body=body,
                        idempotency_key=key,
                        locale=contact.locale,
                    )
                )
            except NotificationError as error:
                status: DispatchStatus = (
                    "deferred" if error.code == "provider_rate_limited" else "failed"
                )
                outcomes.append(
                    RecipientOutcome(
                        volunteer_id=contact.volunteer_id,
                        status=status,
                        reason=error.code,
                    )
                )
                self.ledger.record(
                    NotificationAttempt(
                        idempotency_key=key,
                        volunteer_id=contact.volunteer_id,
                        provider=self.transport.provider,
                        status=status,
                        at=at,
                        body_sha256=message_fingerprint(body),
                        error_code=error.code,
                    )
                )
                continue

            sends_this_tick += 1
            outcomes.append(
                RecipientOutcome(
                    volunteer_id=contact.volunteer_id,
                    status="sent",
                    reason="delivered_to_provider",
                    message_id=receipt.message_id,
                    segments=segments,
                )
            )
            self.ledger.record(
                NotificationAttempt(
                    idempotency_key=key,
                    volunteer_id=contact.volunteer_id,
                    provider=receipt.provider,
                    status="sent",
                    at=at,
                    message_id=receipt.message_id,
                    body_sha256=message_fingerprint(body),
                )
            )

        return DispatchReport(
            glean_id=alert.glean_id,
            template_version=alert.template_version,
            kind=alert.kind.value,
            at=at,
            outcomes=tuple(outcomes),
        )

    def _precheck(
        self,
        *,
        contact: VolunteerContact,
        alert: PickupAlert,
        key: str,
        at: datetime,
        sends_this_tick: int,
        restricted_markers: tuple[str, ...],
    ) -> RecipientOutcome | tuple[str, int]:
        if contact.suppressed:
            return RecipientOutcome(contact.volunteer_id, "skipped", "recipient_suppressed")
        if contact.channel is not Channel.SMS:
            return RecipientOutcome(contact.volunteer_id, "skipped", "channel_unsupported")
        if not contact.sms_opt_in:
            return RecipientOutcome(contact.volunteer_id, "skipped", "sms_opt_in_required")
        if not contact.phone_e164:
            return RecipientOutcome(contact.volunteer_id, "skipped", "recipient_phone_missing")
        if self.ledger.has_sent(key):
            return RecipientOutcome(contact.volunteer_id, "skipped", "duplicate_suppressed")
        if self.quiet_hours.is_quiet(at, contact.timezone):
            return RecipientOutcome(contact.volunteer_id, "deferred", "quiet_hours")
        if sends_this_tick >= self.max_sends_per_tick:
            return RecipientOutcome(contact.volunteer_id, "deferred", "tick_cap_reached")
        if self.ledger.sent_since(contact.volunteer_id, at - DAY) >= (
            self.max_per_recipient_per_day
        ):
            return RecipientOutcome(contact.volunteer_id, "skipped", "daily_cap_reached")
        try:
            body = render_alert(alert, locale=contact.locale, restricted_markers=restricted_markers)
        except TemplateError as error:
            return RecipientOutcome(contact.volunteer_id, "skipped", error.code)
        return body, message_segments(body)

    def _record_blocked(
        self,
        outcome: RecipientOutcome,
        contact: VolunteerContact,
        key: str,
        at: datetime,
    ) -> None:
        """Record non-sent decisions so an operator can audit suppression reasons."""

        self.ledger.record(
            NotificationAttempt(
                idempotency_key=key,
                volunteer_id=contact.volunteer_id,
                provider=self.transport.provider,
                status=outcome.status,
                at=at,
                error_code=outcome.reason,
            )
        )


def default_alert_template_version() -> str:
    return TEMPLATE_VERSION
