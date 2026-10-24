"""SMS transports: Twilio REST and Amazon SNS, behind one typed port.

Both implementations are real provider clients. They are *not* exercised live in this
repository because no Twilio/SNS credential is available; tests inject an HTTP transport or
a boto3 client and assert the exact request shape. When a dependency or credential is
missing the adapter raises instead of silently succeeding, so a demo can never claim a
message was sent when nothing was.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
TWILIO_TIMEOUT_SECONDS = 10.0
SNS_DEFAULT_REGION = "eu-west-1"
SNS_SMS_MAX_CHARS = 480


class NotificationError(RuntimeError):
    """Provider or configuration failure; messages never contain a phone number."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class BackendUnavailable(RuntimeError):
    """A documented notification backend is not installed in this environment."""


@dataclass(frozen=True)
class SmsMessage:
    to: str
    body: str
    idempotency_key: str
    locale: str = "en"

    def __post_init__(self) -> None:
        if not self.to.startswith("+") or not self.to[1:].isdigit():
            raise NotificationError("recipient_phone_invalid", "e164_required")
        if not self.body.strip():
            raise NotificationError("message_body_required")
        if len(self.body) > SNS_SMS_MAX_CHARS:
            raise NotificationError("message_body_too_long", str(len(self.body)))
        if not self.idempotency_key.strip():
            raise NotificationError("idempotency_key_required")


@dataclass(frozen=True)
class SendReceipt:
    message_id: str
    provider: str
    status: str
    segments: int = 1

    @property
    def accepted(self) -> bool:
        return self.status in {"queued", "sent", "accepted", "pending"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "provider": self.provider,
            "status": self.status,
            "segments": self.segments,
        }


class SmsTransport(Protocol):
    @property
    def provider(self) -> str: ...

    def send(self, message: SmsMessage) -> SendReceipt: ...


class HttpTransport(Protocol):
    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
    ) -> tuple[int, Mapping[str, str], bytes]: ...


class UrllibHttpTransport:
    """Stdlib HTTPS POST; never used by the default test suite."""

    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
    ) -> tuple[int, Mapping[str, str], bytes]:
        request = urllib.request.Request(  # noqa: S310 - fixed https provider endpoint
            url, data=body, headers=dict(headers), method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers or {}), error.read()
        except urllib.error.URLError as error:
            raise NotificationError("provider_unavailable", type(error.reason).__name__) from None


def _retry_after(headers: Mapping[str, str]) -> float:
    lowered = {key.lower(): value for key, value in headers.items()}
    raw = lowered.get("retry-after")
    if raw is None:
        return 1.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 1.0


@dataclass
class TwilioSmsTransport:
    """Twilio Programmable SMS via the documented Messages resource."""

    account_sid: str
    auth_token: str
    from_number: str
    http: HttpTransport
    timeout_seconds: float = TWILIO_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not self.account_sid.startswith("AC") or len(self.account_sid) < 30:
            raise NotificationError("twilio_configuration_invalid", "account_sid")
        if len(self.auth_token) < 16:
            raise NotificationError("twilio_configuration_invalid", "auth_token")
        if not self.from_number.startswith("+") or not self.from_number[1:].isdigit():
            raise NotificationError("twilio_configuration_invalid", "from_number")

    @property
    def provider(self) -> str:
        return "twilio"

    def _authorization(self) -> str:
        raw = f"{self.account_sid}:{self.auth_token}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def send(self, message: SmsMessage) -> SendReceipt:
        url = f"{TWILIO_API_BASE}/Accounts/{self.account_sid}/Messages.json"
        payload = urllib.parse.urlencode(
            {
                "To": message.to,
                "From": self.from_number,
                "Body": message.body,
            }
        ).encode("utf-8")
        status, headers, body = self.http.post(
            url,
            {
                "Authorization": self._authorization(),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            payload,
            self.timeout_seconds,
        )
        if status == 429:
            raise NotificationError(
                "provider_rate_limited", f"retry_after={_retry_after(headers):.0f}"
            )
        if status in {401, 403}:
            raise NotificationError("provider_unauthorized", f"status={status}")
        if status >= 500:
            raise NotificationError("provider_unavailable", f"status={status}")
        if status >= 400:
            raise NotificationError("provider_rejected", f"status={status}")
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise NotificationError("provider_response_invalid", "json") from None
        if not isinstance(document, dict):
            raise NotificationError("provider_response_invalid", "object_required")
        message_id = document.get("sid")
        if not isinstance(message_id, str) or not message_id:
            raise NotificationError("provider_response_invalid", "sid")
        segments = document.get("num_segments")
        return SendReceipt(
            message_id=message_id,
            provider=self.provider,
            status=str(document.get("status", "queued")),
            segments=(
                int(segments) if isinstance(segments, (int, str)) and str(segments).isdigit() else 1
            ),
        )


def _default_sns_client(region: str) -> Any:
    try:
        import boto3  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        raise BackendUnavailable("boto3 is not installed; Amazon SNS dispatch cannot run") from None
    return boto3.client("sns", region_name=region)


@dataclass
class SnsSmsTransport:
    """Amazon SNS direct SMS publish (transactional message type).

    SNS is used only for volunteers who opted in; SMS costs and destination-country
    restrictions are documented in ``docs/notifications.md``.
    """

    region: str = SNS_DEFAULT_REGION
    sender_id: str | None = None
    client: Any = None
    max_price_usd: str = "0.05"

    def __post_init__(self) -> None:
        if not self.region.strip():
            raise NotificationError("sns_configuration_invalid", "region")
        if self.client is None:
            self.client = _default_sns_client(self.region)

    @property
    def provider(self) -> str:
        return "sns"

    def send(self, message: SmsMessage) -> SendReceipt:
        attributes: dict[str, Any] = {
            "AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"},
            "AWS.SNS.SMS.MaxPrice": {"DataType": "String", "StringValue": self.max_price_usd},
        }
        if self.sender_id:
            attributes["AWS.SNS.SMS.SenderID"] = {
                "DataType": "String",
                "StringValue": self.sender_id,
            }
        try:
            response = self.client.publish(
                PhoneNumber=message.to,
                Message=message.body,
                MessageAttributes=attributes,
            )
        except Exception as error:  # botocore ClientError or transport failure
            code = getattr(error, "code", None) or type(error).__name__
            raise NotificationError(_sns_error_code(str(code)), "sns_publish_failed") from None
        message_id = response.get("MessageId") if isinstance(response, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise NotificationError("provider_response_invalid", "message_id")
        return SendReceipt(message_id=message_id, provider=self.provider, status="queued")


def _sns_error_code(code: str) -> str:
    lowered = code.casefold()
    if "throttl" in lowered or "rate" in lowered:
        return "provider_rate_limited"
    if "authoriz" in lowered or "credential" in lowered or "accessdenied" in lowered:
        return "provider_unauthorized"
    if "endpoint" in lowered or "timeout" in lowered or "connection" in lowered:
        return "provider_unavailable"
    return "provider_rejected"


@dataclass
class RecordingSmsTransport:
    """Labelled fixture transport: records messages in memory, sends nothing."""

    provider_name: str = "fixture"
    failure: NotificationError | None = None
    sent: list[SmsMessage] = field(default_factory=list)

    @property
    def provider(self) -> str:
        return self.provider_name

    def send(self, message: SmsMessage) -> SendReceipt:
        if self.failure is not None:
            raise self.failure
        self.sent.append(message)
        return SendReceipt(
            message_id=f"fixture-{len(self.sent):04d}",
            provider=self.provider_name,
            status="queued",
        )
