# FastAPI coordination service.
#
# The whole workspace is installed, not just `services/api`: `harvestgrid_api` imports the
# forecasting, notification, and weather-client distributions from the same wheel, and
# `uv sync --frozen` keeps the image consistent with `uv.lock`.
#
# Note on groups: this repository declares its runtime dependencies (fastapi, uvicorn, httpx,
# pillow) in `[dependency-groups] dev`, because the Python side is a mono-repo of importable
# libraries rather than a published app. The image therefore installs the full frozen environment
# instead of `--no-dev`, and adds nothing outside `uv.lock`. Moving those entries to
# `[project].dependencies` is the right fix before this ever ships as a service; until then the
# container runs exactly the locked set so images and CI cannot drift apart.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer first so source edits do not invalidate the (slow) resolve.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Then the workspace itself, installed into the same locked environment.
COPY packages ./packages
COPY services ./services
RUN uv sync --frozen

# Run as a non-root user; the fixture API needs no writable paths.
RUN useradd --create-home --uid 10001 harvestgrid \
    && chown -R harvestgrid:harvestgrid /app
USER harvestgrid

ENV PATH="/app/.venv/bin:${PATH}" \
    DEMO_MODE=fixture \
    API_HOST=0.0.0.0 \
    API_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=6 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

# One worker: the fixture container keeps scheduler and dispatch state in memory, so a second
# process would see a different store. Scale out only with a real store and broker.
CMD ["python", "-m", "harvestgrid_api"]
