import base64
import importlib.util
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest
from harvestgrid_notifications import (
    AlertKind,
    BackendUnavailable,
    Channel,
    DispatchAuthorization,
    InMemoryNotificationLedger,
    NotificationAttempt,
    NotificationDispatchService,
    NotificationError,
    PickupAlert,
    QuietHours,
    RecordingSmsTransport,
    SmsMessage,
    SnsSmsTransport,
    TwilioSmsTransport,
    UrllibHttpTransport,
    VolunteerContact,
    idempotency_key,
    message_segments,
    redact_phone,
    render_alert,
)

MORNING = datetime(2026, 7, 2, 8, 0, tzinfo=UTC)  # 10:00 in Europe/Amsterdam
PHONE_A = "+31612345678"
PHONE_B = "+31698765432"
# Fixture-only provider credentials; no real Twilio account is referenced anywhere.
TWILIO_TOKEN = "fixture-token-0123456789"  # noqa: S105
ADDRESS = "FIXTURE 1 Example Street"
ACCESS_NOTE = "gate code 0000"
WINDOW_START = datetime(2026, 7, 3, 9, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


def alert(**overrides: object) -> PickupAlert:
    values: dict[str, object] = {
        "glean_id": "glean-fixture-1",
        "crop_label": "european plum",
        "coarse_area_label": "Delft, coarse cell u173z",
        "window_start": WINDOW_START,
        "window_end": WINDOW_END,
        "access_constraints": ("street parking only", "bring a 3m ladder"),
        "safety_notes": ("painted kerb, no ladder on the sidewalk",),
    }
    values.update(overrides)
    return PickupAlert(**values)  # type: ignore[arg-type]


def contact(volunteer_id: str = "volunteer-a", **overrides: object) -> VolunteerContact:
    values: dict[str, object] = {
        "volunteer_id": volunteer_id,
        "phone_e164": PHONE_A if volunteer_id == "volunteer-a" else PHONE_B,
        "sms_opt_in": True,
        "sms_opt_in_version": "sms-opt-in-v1",
    }
    values.update(overrides)
    return VolunteerContact(**values)  # type: ignore[arg-type]


def authorization(**overrides: object) -> DispatchAuthorization:
    values: dict[str, object] = {
        "glean_id": "glean-fixture-1",
        "coordinator_id": "coordinator-fixture",
        "approved_by": "coordinator-fixture",
        "approved_at": MORNING,
        "owner_approval_current": True,
        "glean_state": "invited",
    }
    values.update(overrides)
    return DispatchAuthorization(**values)  # type: ignore[arg-type]


def service(
    *,
    transport: RecordingSmsTransport | None = None,
    ledger: InMemoryNotificationLedger | None = None,
    now: datetime = MORNING,
    **kwargs: object,
) -> NotificationDispatchService:
    return NotificationDispatchService(
        transport=transport or RecordingSmsTransport(),
        ledger=ledger or InMemoryNotificationLedger(),
        clock=lambda: now,
        **kwargs,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- templates


def test_rendered_alert_carries_time_area_and_constraints_only() -> None:
    body = render_alert(alert(), restricted_markers=(ADDRESS, ACCESS_NOTE))
    assert "european plum" in body
    assert "Delft, coarse cell u173z" in body
    assert "03 Jul 09:00-12:00 UTC" in body
    assert "street parking only" in body
    assert "Exact address unlocks in the app 2h before" in body
    assert "pickup-v2" in body
    assert ADDRESS not in body
    assert ACCESS_NOTE not in body
    assert len(body) <= 480
    assert message_segments(body) <= 3


def test_rendering_refuses_known_restricted_content() -> None:
    leaked = alert(coarse_area_label=f"Delft near {ADDRESS}")
    with pytest.raises(Exception, match="message_contains_restricted_content"):
        render_alert(leaked, restricted_markers=(ADDRESS,))


def test_rendering_refuses_address_and_phone_patterns() -> None:
    with pytest.raises(Exception, match="message_contains_address_pattern"):
        render_alert(alert(crop_label="plum at 12 Elm Street"))
    with pytest.raises(Exception, match="message_contains_phone_number"):
        render_alert(alert(access_constraints=("call +31612345678",)))


def test_rendering_cancellation_and_reminder_variants() -> None:
    cancelled = render_alert(alert(kind=AlertKind.CANCELLATION))
    assert "is cancelled" in cancelled
    assert "No action needed" in cancelled
    reminder = render_alert(alert(kind=AlertKind.REMINDER))
    assert reminder.startswith("HarvestGrid Reminder:")


def test_rendering_rejects_unsupported_locale_and_long_messages() -> None:
    with pytest.raises(Exception, match="locale_unsupported"):
        render_alert(alert(), locale="fr")
    with pytest.raises(Exception, match="message_too_long"):
        render_alert(alert(access_constraints=tuple("access note " * 4 for _ in range(12))))


def test_alert_validation() -> None:
    with pytest.raises(Exception, match="alert_glean_id_required"):
        alert(glean_id=" ")
    with pytest.raises(Exception, match="alert_description_required"):
        alert(crop_label="")
    with pytest.raises(Exception, match="alert_window_unordered"):
        alert(window_end=WINDOW_START)
    with pytest.raises(Exception, match="alert_window_requires_timezone"):
        alert(window_start=datetime(2026, 7, 3, 9, 0))
    with pytest.raises(Exception, match="empty_access_constraint"):
        alert(access_constraints=("  ",))
    with pytest.raises(Exception, match="access_constraint_too_long"):
        alert(access_constraints=("x" * 81,))
    with pytest.raises(Exception, match="coordinator_channel_required"):
        alert(coordinator_channel=" ")
    assert alert().reveal_not_before == WINDOW_START - timedelta(hours=2)


def test_contact_validation_and_redaction() -> None:
    with pytest.raises(NotificationError, match="contact_volunteer_id_required"):
        VolunteerContact(volunteer_id=" ")
    with pytest.raises(NotificationError, match="contact_phone_invalid"):
        VolunteerContact(volunteer_id="v", phone_e164="0612345678")
    with pytest.raises(NotificationError, match="contact_opt_in_version_required"):
        VolunteerContact(volunteer_id="v", phone_e164=PHONE_A, sms_opt_in=True)
    with pytest.raises(NotificationError, match="contact_timezone_unknown"):
        VolunteerContact(volunteer_id="v", timezone="Mars/Olympus")
    assert redact_phone(PHONE_A) == "+3***78"
    assert redact_phone("12") == "redacted"
    assert contact().log_reference == redact_phone(PHONE_A)


# --------------------------------------------------------------------------- dispatch


def test_dispatch_sends_to_each_opted_in_volunteer() -> None:
    transport = RecordingSmsTransport()
    dispatch = service(transport=transport)
    report = dispatch.dispatch(
        alert=alert(),
        recipients=(contact(), contact("volunteer-b")),
        authorization=authorization(),
        restricted_markers=(ADDRESS, ACCESS_NOTE),
        now=MORNING,
    )
    assert report.sent == 2
    assert report.skipped == 0
    assert [message.to for message in transport.sent] == [PHONE_A, PHONE_B]
    assert all(ADDRESS not in message.body for message in transport.sent)
    assert all(
        message.idempotency_key.startswith("glean-fixture-1/invited/") for message in transport.sent
    )
    assert report.as_dict()["template_version"] == "pickup-v2"


def test_dispatch_requires_current_coordinator_and_owner_approval() -> None:
    dispatch = service()
    for overrides, reason in (
        ({"approved_by": "someone-else"}, "coordinator_approval_required"),
        ({"owner_approval_current": False}, "owner_approval_missing"),
        ({"glean_id": "other-glean"}, "glean_mismatch"),
        ({"glean_state": "matching"}, "glean_state_not_dispatchable"),
        ({"glean_state": "confirmed"}, None),
    ):
        if reason is None:
            dispatch.dispatch(
                alert=alert(), recipients=(contact(),), authorization=authorization(**overrides)
            )
            continue
        with pytest.raises(NotificationError) as error:
            dispatch.dispatch(
                alert=alert(), recipients=(contact(),), authorization=authorization(**overrides)
            )
        assert error.value.code == "dispatch_not_authorized"
        assert error.value.detail == reason


def test_cancellation_alert_requires_cancelled_state() -> None:
    dispatch = service()
    with pytest.raises(NotificationError, match="cancellation_requires_cancelled_state"):
        dispatch.dispatch(
            alert=alert(kind=AlertKind.CANCELLATION),
            recipients=(contact(),),
            authorization=authorization(),
        )
    report = dispatch.dispatch(
        alert=alert(kind=AlertKind.CANCELLATION),
        recipients=(contact(),),
        authorization=authorization(glean_state="cancelled"),
    )
    assert report.sent == 1


def test_dispatch_skips_without_opt_in_phone_or_channel() -> None:
    transport = RecordingSmsTransport()
    dispatch = service(transport=transport)
    report = dispatch.dispatch(
        alert=alert(),
        recipients=(
            contact("volunteer-a", sms_opt_in=False),
            contact("volunteer-b", phone_e164=None),
            contact("volunteer-c", channel=Channel.EMAIL),
            contact("volunteer-d", suppressed=True),
        ),
        authorization=authorization(),
    )
    assert report.sent == 0
    assert {outcome.reason for outcome in report.outcomes} == {
        "sms_opt_in_required",
        "recipient_phone_missing",
        "channel_unsupported",
        "recipient_suppressed",
    }
    assert transport.sent == []


def test_repeated_dispatch_is_suppressed_by_idempotency_key() -> None:
    transport = RecordingSmsTransport()
    dispatch = service(transport=transport)
    first = dispatch.dispatch(alert=alert(), recipients=(contact(),), authorization=authorization())
    second = dispatch.dispatch(
        alert=alert(), recipients=(contact(),), authorization=authorization()
    )
    assert first.sent == 1
    assert second.sent == 0
    assert second.outcomes[0].reason == "duplicate_suppressed"
    assert len(transport.sent) == 1
    assert (
        idempotency_key(
            glean_id="glean-fixture-1",
            glean_state="invited",
            volunteer_id="volunteer-a",
            template_version="pickup-v2",
        )
        == "glean-fixture-1/invited/volunteer-a/pickup/pickup-v2"
    )


def test_quiet_hours_defer_instead_of_sending() -> None:
    transport = RecordingSmsTransport()
    dispatch = service(transport=transport)
    late = datetime(2026, 7, 2, 20, 30, tzinfo=UTC)  # 22:30 Amsterdam
    report = dispatch.dispatch(
        alert=alert(), recipients=(contact(),), authorization=authorization(), now=late
    )
    assert report.deferred == 1
    assert report.outcomes[0].reason == "quiet_hours"
    assert transport.sent == []


def test_quiet_hours_window_handles_wrap_around() -> None:
    quiet = QuietHours(start_hour=21, end_hour=7)
    assert quiet.is_quiet(datetime(2026, 7, 2, 20, 0, tzinfo=UTC), "Europe/Amsterdam") is True
    # 04:00 UTC is 06:00 in Amsterdam (CEST) and still inside the quiet window.
    assert quiet.is_quiet(datetime(2026, 7, 2, 4, 0, tzinfo=UTC), "Europe/Amsterdam") is True
    assert quiet.is_quiet(datetime(2026, 7, 2, 10, 0, tzinfo=UTC), "Europe/Amsterdam") is False
    daytime = QuietHours(start_hour=9, end_hour=17)
    assert daytime.is_quiet(datetime(2026, 7, 2, 10, 0, tzinfo=UTC), "Europe/Amsterdam") is True
    assert daytime.is_quiet(datetime(2026, 7, 2, 18, 0, tzinfo=UTC), "Europe/Amsterdam") is False
    assert QuietHours(start_hour=0, end_hour=0).is_quiet(MORNING, "Europe/Amsterdam") is False
    with pytest.raises(NotificationError, match="quiet_hours_invalid"):
        QuietHours(start_hour=24)
    with pytest.raises(NotificationError, match="quiet_hours_requires_aware_timestamp"):
        quiet.is_quiet(datetime(2026, 7, 2, 20, 0), "Europe/Amsterdam")


def test_tick_cap_limits_sends_per_run() -> None:
    dispatch = service(max_sends_per_tick=1)
    report = dispatch.dispatch(
        alert=alert(),
        recipients=(contact(), contact("volunteer-b")),
        authorization=authorization(),
    )
    assert report.sent == 1
    assert report.deferred == 1
    assert report.outcomes[-1].reason == "tick_cap_reached"


def test_daily_cap_stops_repeat_messaging_of_one_volunteer() -> None:
    ledger = InMemoryNotificationLedger()
    for index in range(3):
        ledger.record(
            NotificationAttempt(
                idempotency_key=f"glean-fixture-1/invited/volunteer-a/pickup/v{index}",
                volunteer_id="volunteer-a",
                provider="fixture",
                status="sent",
                at=MORNING - timedelta(minutes=index + 1),
            )
        )
    dispatch = service(ledger=ledger)
    report = dispatch.dispatch(
        alert=alert(template_version="pickup-v9"),
        recipients=(contact(),),
        authorization=authorization(),
    )
    assert report.skipped == 1
    assert report.outcomes[0].reason == "daily_cap_reached"


def test_provider_rate_limit_defers_and_outage_fails() -> None:
    rate_limited = RecordingSmsTransport(
        failure=NotificationError("provider_rate_limited", "retry_after=5")
    )
    report = service(transport=rate_limited).dispatch(
        alert=alert(), recipients=(contact(),), authorization=authorization()
    )
    assert report.deferred == 1
    assert report.outcomes[0].reason == "provider_rate_limited"

    unavailable = RecordingSmsTransport(
        failure=NotificationError("provider_unavailable", "status=503")
    )
    report = service(transport=unavailable).dispatch(
        alert=alert(), recipients=(contact(),), authorization=authorization()
    )
    assert report.failed == 1
    assert report.outcomes[0].reason == "provider_unavailable"


def test_ledger_records_digests_not_message_text_or_phone_numbers() -> None:
    ledger = InMemoryNotificationLedger()
    dispatch = service(ledger=ledger)
    dispatch.dispatch(alert=alert(), recipients=(contact(),), authorization=authorization())
    attempt = ledger.attempts()[0]
    serialized = json.dumps(attempt.as_dict())
    assert PHONE_A not in serialized
    assert ADDRESS not in serialized
    assert "european plum" not in serialized
    assert attempt.body_sha256 is not None
    assert len(attempt.body_sha256) == 64


def test_report_exposes_no_phone_numbers() -> None:
    dispatch = service()
    report = dispatch.dispatch(
        alert=alert(), recipients=(contact(), contact("volunteer-b")), authorization=authorization()
    )
    serialized = json.dumps(report.as_dict())
    assert PHONE_A not in serialized
    assert PHONE_B not in serialized
    assert "volunteer-a" in serialized


def test_blocked_rendering_is_reported_as_skipped() -> None:
    dispatch = service()
    report = dispatch.dispatch(
        alert=alert(coarse_area_label=f"Delft near {ADDRESS}"),
        recipients=(contact(),),
        authorization=authorization(),
        restricted_markers=(ADDRESS,),
    )
    assert report.skipped == 1
    assert report.outcomes[0].reason == "message_contains_restricted_content"


def test_authorization_validation() -> None:
    with pytest.raises(NotificationError, match="authorization_incomplete"):
        DispatchAuthorization(
            glean_id=" ",
            coordinator_id="coordinator-fixture",
            approved_by="coordinator-fixture",
            approved_at=MORNING,
            owner_approval_current=True,
            glean_state="invited",
        )
    with pytest.raises(NotificationError, match="authorization_incomplete"):
        DispatchAuthorization(
            glean_id="glean-fixture-1",
            coordinator_id="coordinator-fixture",
            approved_by="coordinator-fixture",
            approved_at=datetime(2026, 7, 2, 8, 0),
            owner_approval_current=True,
            glean_state="invited",
        )


def test_dispatch_rejects_invalid_limits_and_clock() -> None:
    with pytest.raises(NotificationError, match="dispatch_limits_invalid"):
        service(max_sends_per_tick=0)
    naive = NotificationDispatchService(
        transport=RecordingSmsTransport(),
        ledger=InMemoryNotificationLedger(),
        clock=lambda: datetime(2026, 7, 2, 8, 0),
    )
    with pytest.raises(NotificationError, match="dispatch_clock_requires_timezone"):
        naive.dispatch(alert=alert(), recipients=(contact(),), authorization=authorization())


# --------------------------------------------------------------------------- transports


class FakeHttp:
    def __init__(
        self,
        status: int = 201,
        body: bytes = b'{"sid": "SM0123456789abcdef", "status": "queued", "num_segments": "2"}',
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.requests: list[tuple[str, dict[str, str], bytes, float]] = []

    def post(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout: float
    ) -> tuple[int, Mapping[str, str], bytes]:
        self.requests.append((url, dict(headers), body, timeout))
        return self.status, self.headers, self.body


def twilio(http: FakeHttp) -> TwilioSmsTransport:
    return TwilioSmsTransport(
        account_sid="AC" + "0" * 32,
        auth_token=TWILIO_TOKEN,
        from_number="+31555000123",
        http=http,
    )


def message() -> SmsMessage:
    return SmsMessage(to=PHONE_A, body="HarvestGrid test body", idempotency_key="k/1")


def test_twilio_request_shape_and_receipt() -> None:
    http = FakeHttp()
    receipt = twilio(http).send(message())
    url, headers, body, timeout = http.requests[0]
    assert url.endswith("/Accounts/AC" + "0" * 32 + "/Messages.json")
    decoded = base64.b64decode(headers["Authorization"].split(" ", 1)[1]).decode()
    assert decoded.startswith("AC")
    assert decoded.endswith(f":{TWILIO_TOKEN}")
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert "To=%2B31612345678" in body.decode()
    assert "From=%2B31555000123" in body.decode()
    assert timeout == 10.0
    assert receipt.message_id == "SM0123456789abcdef"
    assert receipt.status == "queued"
    assert receipt.segments == 2
    assert receipt.accepted is True
    assert receipt.as_dict()["provider"] == "twilio"


@pytest.mark.parametrize(
    "status, headers, code",
    [
        (401, {}, "provider_unauthorized"),
        (403, {}, "provider_unauthorized"),
        (429, {"Retry-After": "7"}, "provider_rate_limited"),
        (500, {}, "provider_unavailable"),
        (400, {}, "provider_rejected"),
    ],
)
def test_twilio_error_mapping(status: int, headers: dict[str, str], code: str) -> None:
    with pytest.raises(NotificationError) as error:
        twilio(FakeHttp(status=status, headers=headers)).send(message())
    assert error.value.code == code
    assert TWILIO_TOKEN not in str(error.value)
    assert PHONE_A not in str(error.value)


def test_twilio_rejects_malformed_responses() -> None:
    with pytest.raises(NotificationError, match="provider_response_invalid"):
        twilio(FakeHttp(body=b"not json")).send(message())
    with pytest.raises(NotificationError, match="provider_response_invalid"):
        twilio(FakeHttp(body=b"[]")).send(message())
    with pytest.raises(NotificationError, match="provider_response_invalid"):
        twilio(FakeHttp(body=b'{"status": "queued"}')).send(message())


def test_twilio_configuration_and_message_validation() -> None:
    with pytest.raises(NotificationError, match="twilio_configuration_invalid"):
        TwilioSmsTransport(
            account_sid="bad", auth_token="x" * 20, from_number=PHONE_A, http=FakeHttp()
        )
    with pytest.raises(NotificationError, match="twilio_configuration_invalid"):
        TwilioSmsTransport(
            account_sid="AC" + "0" * 32,
            auth_token="short",  # noqa: S106 - deliberately invalid fixture value
            from_number=PHONE_A,
            http=FakeHttp(),
        )
    with pytest.raises(NotificationError, match="twilio_configuration_invalid"):
        TwilioSmsTransport(
            account_sid="AC" + "0" * 32, auth_token="x" * 20, from_number="1234", http=FakeHttp()
        )
    with pytest.raises(NotificationError, match="recipient_phone_invalid"):
        SmsMessage(to="0612345678", body="x", idempotency_key="k")
    with pytest.raises(NotificationError, match="message_body_required"):
        SmsMessage(to=PHONE_A, body="  ", idempotency_key="k")
    with pytest.raises(NotificationError, match="message_body_too_long"):
        SmsMessage(to=PHONE_A, body="x" * 481, idempotency_key="k")
    with pytest.raises(NotificationError, match="idempotency_key_required"):
        SmsMessage(to=PHONE_A, body="x", idempotency_key=" ")


class FakeSnsClient:
    def __init__(self, *, error: Exception | None = None, response: object = None) -> None:
        self.error = error
        self.response = response if response is not None else {"MessageId": "sns-msg-1"}
        self.calls: list[dict[str, object]] = []

    def publish(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def test_sns_publish_shape() -> None:
    client = FakeSnsClient()
    transport = SnsSmsTransport(client=client, sender_id="HarvestGrid")
    receipt = transport.send(message())
    call = client.calls[0]
    assert call["PhoneNumber"] == PHONE_A
    assert call["Message"] == "HarvestGrid test body"
    attributes = call["MessageAttributes"]
    assert isinstance(attributes, dict)
    assert attributes["AWS.SNS.SMS.SMSType"]["StringValue"] == "Transactional"
    assert attributes["AWS.SNS.SMS.MaxPrice"]["StringValue"] == "0.05"
    assert attributes["AWS.SNS.SMS.SenderID"]["StringValue"] == "HarvestGrid"
    assert receipt.provider == "sns"
    assert receipt.message_id == "sns-msg-1"
    assert receipt.status == "queued"


def test_sns_without_sender_id_omits_the_attribute() -> None:
    client = FakeSnsClient()
    SnsSmsTransport(client=client).send(message())
    attributes = client.calls[0]["MessageAttributes"]
    assert isinstance(attributes, dict)
    assert "AWS.SNS.SMS.SenderID" not in attributes


@pytest.mark.parametrize(
    "provider_code, expected",
    [
        ("Throttling", "provider_rate_limited"),
        ("AccessDenied", "provider_unauthorized"),
        ("EndpointDisabled", "provider_unavailable"),
        ("InvalidParameter", "provider_rejected"),
    ],
)
def test_sns_error_mapping(provider_code: str, expected: str) -> None:
    client = FakeSnsClient(error=FakeClientError(provider_code))
    with pytest.raises(NotificationError) as error:
        SnsSmsTransport(client=client).send(message())
    assert error.value.code == expected
    assert PHONE_A not in str(error.value)


def test_sns_rejects_a_response_without_message_id() -> None:
    with pytest.raises(NotificationError, match="provider_response_invalid"):
        SnsSmsTransport(client=FakeSnsClient(response={})).send(message())


def test_sns_rejects_an_empty_region() -> None:
    with pytest.raises(NotificationError, match="sns_configuration_invalid"):
        SnsSmsTransport(region=" ", client=FakeSnsClient())


@pytest.mark.skipif(importlib.util.find_spec("boto3") is not None, reason="boto3 installed")
def test_sns_fails_visibly_without_boto3() -> None:
    with pytest.raises(BackendUnavailable, match="boto3 is not installed"):
        SnsSmsTransport()


def test_urllib_transport_is_available_but_unused_in_tests() -> None:
    assert isinstance(UrllibHttpTransport(), UrllibHttpTransport)
