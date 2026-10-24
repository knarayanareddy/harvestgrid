"""Unit tests for the container entrypoint: ``python -m harvestgrid_api``.

The entrypoint is what the container actually runs, so its refusal paths matter more than its happy
path: a misconfigured ``DEMO_MODE`` must stop the process rather than serve fixture identity as if
it were live, and a broker URL that is not wired must announce itself instead of pretending a job
was queued.

``uvicorn`` is replaced with a stub in ``sys.modules`` rather than imported: the entrypoint imports
it lazily, and this keeps the test independent of a runtime that can be absent (uvicorn imports
``ssl``, which some minimal CPython builds lack).
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from harvestgrid_api import __main__ as entrypoint

RUN_CALLS: list[dict[str, Any]] = []


def _run(app: object, **kwargs: Any) -> None:
    RUN_CALLS.append({"app": app, **kwargs})


@pytest.fixture(name="uvicorn_stub")
def fixture_uvicorn_stub(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    stub = types.ModuleType("uvicorn")
    stub.run = _run  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", stub)
    RUN_CALLS.clear()
    for name in ("DEMO_MODE", "API_HOST", "API_PORT", "LOG_LEVEL", "FORECASTING_BROKER_URL"):
        monkeypatch.delenv(name, raising=False)
    return RUN_CALLS


def test_refuses_to_start_outside_fixture_mode(
    uvicorn_stub: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DEMO_MODE", "production")

    assert entrypoint.main() == 2
    assert uvicorn_stub == []
    message = capsys.readouterr().err
    assert "refusing to start" in message
    assert "production" in message


def test_defaults_bind_loopback_and_fixture_mode(uvicorn_stub: list[dict[str, Any]]) -> None:
    assert entrypoint.main() == 0
    assert len(uvicorn_stub) == 1
    call = uvicorn_stub[0]
    assert call["host"] == "127.0.0.1"
    assert call["port"] == 8000
    assert call["log_level"] == "info"
    # The factory is called, not a module-level app: the container is built per process.
    assert callable(call["app"])
    assert call["app"].__class__.__name__ == "FastAPI"


def test_container_environment_binds_all_interfaces(
    uvicorn_stub: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEMO_MODE", "fixture")
    # The container must listen on every interface; this is the compose contract, not a test server.
    monkeypatch.setenv("API_HOST", "0.0.0.0")  # noqa: S104
    monkeypatch.setenv("API_PORT", "8123")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    assert entrypoint.main() == 0
    call = uvicorn_stub[0]
    assert call["host"] == "0.0.0.0"  # noqa: S104
    assert call["port"] == 8123
    assert call["log_level"] == "warning"


def test_demo_mode_accepts_the_documented_aliases(
    uvicorn_stub: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    for alias in ("fixture", "FIXTURE", "demo", " demo "):
        monkeypatch.setenv("DEMO_MODE", alias)
        assert entrypoint.main() == 0, alias
    assert len(uvicorn_stub) == 4


def test_broker_url_is_reported_as_unwired(
    uvicorn_stub: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FORECASTING_BROKER_URL", "redis://redis:6379/1")

    assert entrypoint.main() == 0
    stderr = capsys.readouterr().err
    assert "runs the scheduler inline" in stderr
    assert "503" in stderr
    assert len(uvicorn_stub) == 1


def test_missing_uvicorn_reports_actionably(
    uvicorn_stub: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", None)

    assert entrypoint.main() == 2
    assert uvicorn_stub == []
    assert "uvicorn is not installed" in capsys.readouterr().err
