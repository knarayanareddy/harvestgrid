import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from harvestgrid_ring_adapter import InMemoryMediaJobQueue, InMemoryWebhookStore, RingWebhookService
from harvestgrid_ring_adapter.server import (
    MIN_SECRET_LENGTH,
    build_server,
    fixture_service,
    main,
)

SECRET = b"fixture-http-secret-0123456789abcdef"


def envelope(event_id: str = "evt-http-1") -> bytes:
    return json.dumps(
        {
            "event_id": event_id,
            "event_type": "device.motion",
            "device_id": "device-http",
            "connection_id": "connection-http",
            "occurred_at": "2026-10-24T11:59:00Z",
        },
        separators=(",", ":"),
    ).encode("utf-8")


@contextmanager
def running_server(service: RingWebhookService) -> Iterator[str]:
    server = build_server(service, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def post(base: str, body: bytes, headers: dict[str, str]) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(  # noqa: S310 - loopback test server
        f"{base}/v1/ring/webhooks", data=body, method="POST", headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def signature(secret: bytes, timestamp: str, body: bytes) -> str:
    from harvestgrid_ring_adapter import compute_signature

    return compute_signature(secret=secret, timestamp=timestamp, body=body)


def test_http_transport_accepts_a_signed_delivery_and_rejects_a_forged_one() -> None:
    service = fixture_service(SECRET)  # real clock: the HTTP layer stamps receipt time
    timestamp = str(int(datetime.now(UTC).timestamp()))
    with running_server(service) as base:
        body = envelope()
        status, payload = post(
            base,
            body,
            {
                "Content-Type": "application/json",
                "X-Ring-Signature": signature(SECRET, timestamp, body),
                "X-Ring-Timestamp": timestamp,
            },
        )
        assert status == 200
        assert payload["status"] == "accepted"
        assert payload["event_id"] == "evt-http-1"

        forged_status, forged = post(
            base,
            envelope("evt-http-2"),
            {
                "Content-Type": "application/json",
                "X-Ring-Signature": "sha256=" + "0" * 64,
                "X-Ring-Timestamp": timestamp,
            },
        )
        assert forged_status == 401
        assert forged["code"] == "signature_mismatch"

        with urllib.request.urlopen(f"{base}/healthz", timeout=5) as response:  # noqa: S310
            assert response.status == 200
            assert json.loads(response.read())["component"] == "ring-webhook-adapter"

        get_request = urllib.request.Request(  # noqa: S310
            f"{base}/v1/ring/webhooks", method="GET"
        )
        with pytest.raises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(get_request, timeout=5)  # noqa: S310
        assert rejected.value.code == 405
        rejected.value.close()

        put_request = urllib.request.Request(  # noqa: S310
            f"{base}/v1/ring/webhooks", data=b"{}", method="PUT"
        )
        with pytest.raises(urllib.error.HTTPError) as put_rejected:
            urllib.request.urlopen(put_request, timeout=5)  # noqa: S310
        assert put_rejected.value.code == 405
        put_rejected.value.close()

    jobs = service.pending_jobs()
    assert [job.event_id for job in jobs] == ["evt-http-1"]


def test_fixture_service_uses_in_memory_adapters_and_requires_a_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = fixture_service(SECRET)
    assert isinstance(service.store, InMemoryWebhookStore)
    assert isinstance(service.queue, InMemoryMediaJobQueue)
    assert service.secret == SECRET

    monkeypatch.delenv("RING_WEBHOOK_SECRET", raising=False)
    assert main() == 2
    monkeypatch.setenv("RING_WEBHOOK_SECRET", "too-short")
    assert main() == 2
    assert MIN_SECRET_LENGTH >= 32
