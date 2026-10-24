"""Run the coordination API with uvicorn: ``python -m harvestgrid_api``.

Configuration is environment-only, because that is what container orchestration provides:

* ``API_HOST`` / ``API_PORT`` — bind address (default ``127.0.0.1:8000``; containers set
  ``0.0.0.0``).
* ``DEMO_MODE`` — must be ``fixture`` for the shipped container. Anything else is refused rather
  than silently serving synthetic identity as if it were live.
* ``FORECASTING_BROKER_URL`` — when set, the API still uses the inline runner, because broker
  submission is not implemented; the route would answer ``503``. Setting it here is documented as
  unsupported so nobody believes a queue is draining.
* ``DATABASE_URL`` / ``REDIS_URL`` — provisioned by docker-compose for the next slice. The fixture
  container deliberately does not connect to them; see ``docs/local-stack.md``.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    mode = os.environ.get("DEMO_MODE", "fixture").strip().lower()
    if mode not in {"fixture", "demo"}:
        print(
            f"refusing to start: DEMO_MODE={mode!r}. This image has fixture identity, a synthetic "
            "weather source, and a recording SMS transport; only DEMO_MODE=fixture is supported.",
            file=sys.stderr,
        )
        return 2

    broker = os.environ.get("FORECASTING_BROKER_URL", "").strip()
    if broker:
        print(
            "warning: FORECASTING_BROKER_URL is set, but this build runs the scheduler inline. "
            "The forecasting trigger will answer 503 worker_backend_unavailable rather than "
            "pretend a job was queued.",
            file=sys.stderr,
        )

    try:
        import uvicorn
    except ImportError as error:  # pragma: no cover - the dev group is installed in this image
        # The cause matters: uvicorn itself pulls in `ssl`, so a minimal CPython build fails here
        # with `No module named '_ssl'` rather than a missing package.
        print(
            f"uvicorn is not installed ({error}); install the dev dependency group",
            file=sys.stderr,
        )
        return 2

    from .app import create_app

    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8000"))
    log_level = os.environ.get("LOG_LEVEL", "info").lower()
    uvicorn.run(create_app(), host=host, port=port, log_level=log_level, access_log=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
