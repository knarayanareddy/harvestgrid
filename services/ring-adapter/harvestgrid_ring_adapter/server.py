"""Stdlib HTTP adapter for Ring webhook ingestion (fixture store, no Ring calls out).

This is a thin transport wrapper: it reads bounded raw bytes, passes them to
:class:`harvestgrid_ring_adapter.webhooks.RingWebhookService`, and writes a small JSON
response. Nothing here calls Ring, and no media is downloaded.

Run locally with (secret is required; there is no default):

```bash
RING_WEBHOOK_SECRET=<32+ chars> uv run --package harvestgrid-ring-adapter \
  python -m harvestgrid_ring_adapter.server
```
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .webhooks import (
    InMemoryMediaJobQueue,
    InMemoryWebhookStore,
    RingWebhookService,
    WebhookRequest,
)

LOGGER = logging.getLogger("harvestgrid.ring_adapter")
MIN_SECRET_LENGTH = 32
# Containers must bind all interfaces; a security group restricts ingress.
DEFAULT_BIND_HOST = "0.0.0.0"  # noqa: S104


class RingWebhookHTTPServer(ThreadingHTTPServer):
    """Server carrying the injected service; tests use port ``0`` (ephemeral)."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: RingWebhookService) -> None:
        super().__init__(address, RingWebhookRequestHandler)
        self.service = service


class RingWebhookRequestHandler(BaseHTTPRequestHandler):
    server_version = "HarvestGridRingAdapter/0.1"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def _write(
        self, status: int, payload: dict[str, Any], extra: dict[str, str] | None = None
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _service(self) -> RingWebhookService:
        server = self.server
        if not isinstance(server, RingWebhookHTTPServer):
            raise RuntimeError("server is not a RingWebhookHTTPServer")
        return server.service

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path.split("?")[0] == "/healthz":
            self._write(200, {"status": "ok", "component": "ring-webhook-adapter"})
            return
        self._write(405, {"status": "rejected", "code": "method_not_allowed"}, {"Allow": "POST"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        service = self._service()
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < 0:
            length = 0
        body = self.rfile.read(min(length, service.max_body_bytes + 1))
        headers = {key: value for key, value in self.headers.items()}
        request = WebhookRequest(
            method="POST",
            path=self.path.split("?")[0],
            headers=headers,
            body=body,
            received_at=datetime.now(UTC),
        )
        ack = service.handle(request)
        extra = {"Allow": "POST"} if ack.status == 405 else {}
        if ack.status == 429:
            detail = str(ack.body.get("detail", ""))
            retry_after = detail.split("retry_after=")[-1] if "retry_after=" in detail else "1"
            extra["Retry-After"] = retry_after
        self._write(ack.status, dict(ack.body), extra)

    def do_PUT(self) -> None:  # noqa: N802
        self._write(405, {"status": "rejected", "code": "method_not_allowed"}, {"Allow": "POST"})

    do_DELETE = do_PUT
    do_PATCH = do_PUT

    def log_message(self, format: str, *args: object) -> None:
        # Deliberately excludes headers and bodies: no signatures or payloads in logs.
        LOGGER.info("%s %s", self.command, self.path.split("?")[0])


def build_server(
    service: RingWebhookService, *, host: str = DEFAULT_BIND_HOST, port: int = 8080
) -> RingWebhookHTTPServer:
    return RingWebhookHTTPServer((host, port), service)


def fixture_service(secret: bytes, *, clock: Any = None) -> RingWebhookService:
    """Local service wired to in-memory adapters, clearly labeled as fixture storage."""

    return RingWebhookService(
        secret=secret,
        store=InMemoryWebhookStore(),
        queue=InMemoryMediaJobQueue(),
        clock=clock or (lambda: datetime.now(UTC)),
    )


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper())
    secret = os.environ.get("RING_WEBHOOK_SECRET", "")
    if len(secret.encode("utf-8")) < MIN_SECRET_LENGTH:
        print(
            "RING_WEBHOOK_SECRET must be set to at least "
            f"{MIN_SECRET_LENGTH} bytes; refusing to start without signature verification.",
            file=sys.stderr,
        )
        return 2
    port = int(os.environ.get("RING_ADAPTER_PORT", "8080"))
    host = os.environ.get("RING_ADAPTER_HOST", DEFAULT_BIND_HOST)
    service = fixture_service(secret.encode("utf-8"))
    server = build_server(service, host=host, port=port)
    print(
        json.dumps(
            {
                "component": "ring-webhook-adapter",
                "mode": "FIXTURE STORE — no Ring API call, no media download, no durable queue",
                "listen": f"{host}:{server.server_address[1]}",
                "ack_budget_seconds": service.ack_budget.total_seconds(),
                "signature": "HMAC-SHA256 over '<timestamp>.<raw body>'",
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
