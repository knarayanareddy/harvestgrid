"""Pure ASGI middleware.

``BaseHTTPMiddleware`` is deliberately avoided: it spawns an anyio task group per request and
breaks streaming and context propagation. A correlation id only needs the ASGI message stream.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from starlette.datastructures import MutableHeaders

from .ids import new_ulid

ASGIReceive = Callable[[], Awaitable[dict[str, Any]]]
ASGISend = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[dict[str, Any], ASGIReceive, ASGISend], Awaitable[None]]

REQUEST_ID_HEADER = "X-Request-Id"


class RequestIdMiddleware:
    """Attach a fresh ULID to every request and echo it back to the caller.

    The id is stored in ``scope['state']`` so handlers and problem documents can correlate a
    response with the log line the same request produced, without trusting a client-supplied
    header.
    """

    def __init__(self, app: ASGIApp, *, clock: Callable[[], Any]) -> None:
        self.app = app
        self.clock = clock

    async def __call__(self, scope: dict[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = new_ulid(self.clock)
        state = scope.setdefault("state", {})
        state["request_id"] = request_id

        async def send_with_request_id(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        await self.app(scope, receive, send_with_request_id)


def request_id_of(scope: Mapping[str, Any]) -> str | None:
    state = scope.get("state") or {}
    value = state.get("request_id")
    return value if isinstance(value, str) and value else None


__all__ = ["REQUEST_ID_HEADER", "RequestIdMiddleware", "request_id_of"]
