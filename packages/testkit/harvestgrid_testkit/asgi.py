"""A minimal synchronous ASGI driver for tests.

`starlette.testclient.TestClient` needs anyio's asyncio backend, which imports `ssl`. The
source-built CPython in the validation sandbox has no `_ssl` module, so this small driver
speaks ASGI directly with `asyncio.run`. It exercises the real application stack -- routing,
middleware, dependency injection, exception handlers -- without an event-loop library, and it
works identically on a normal interpreter.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AsgiResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes
    exception: BaseException | None = None

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self.content)

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


@dataclass
class AsgiClient:
    """Call an ASGI app as if it were an HTTP client, without any I/O library."""

    app: Any
    default_headers: dict[str, str] = field(default_factory=dict)

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        content: bytes | None = None,
        query_string: str = "",
    ) -> AsgiResponse:
        merged = {key.lower(): value for key, value in self.default_headers.items()}
        merged.update({key.lower(): value for key, value in (headers or {}).items()})
        body = content
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            merged.setdefault("content-type", "application/json")
        body = body or b""
        merged.setdefault("content-length", str(len(body)))
        merged.setdefault("host", "testserver")

        return run_asgi(
            self.app,
            method=method.upper(),
            path=path,
            query_string=query_string,
            headers=merged,
            body=body,
        )

    def get(self, path: str, **kwargs: Any) -> AsgiResponse:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> AsgiResponse:
        return self.request("POST", path, **kwargs)


def run_asgi(
    app: Any,
    *,
    method: str,
    path: str,
    query_string: str = "",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> AsgiResponse:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "root_path": "",
        "query_string": query_string.encode("latin-1"),
        "headers": raw_headers,
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
    }
    captured: dict[str, Any] = {"status": None, "headers": {}, "body": b""}
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
            captured["headers"] = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in message.get("headers", [])
            }
        elif message["type"] == "http.response.body":
            captured["body"] += message.get("body", b"")

    async def run() -> None:
        await app(scope, receive, send)

    exception: BaseException | None = None
    try:
        asyncio.run(run())
    except Exception as error:  # Starlette re-raises after emitting a 500 response
        if captured["status"] is None:
            raise
        exception = error
    return AsgiResponse(
        status_code=int(captured["status"]),
        headers=dict(captured["headers"]),
        content=captured["body"],
        exception=exception,
    )
