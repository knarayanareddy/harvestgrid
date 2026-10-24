"""FastAPI application factory for the HarvestGrid coordination API.

The app wires the forecasting scheduler and the notification dispatcher behind three routes,
adds RFC 9457 problem responses, propagates a ULID request id, and exposes its OpenAPI document
so a checked-in snapshot can catch silent contract drift.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .container import ApiContainer, build_fixture_container
from .errors import problem_response, validation_fields
from .ids import new_ulid
from .middleware import REQUEST_ID_HEADER, RequestIdMiddleware, request_id_of
from .problems import PROBLEM_MEDIA_TYPE, ApiProblem
from .routes import forecasting, notifications
from .schemas import HealthResponse

API_VERSION = "0.1.0"
API_TITLE = "HarvestGrid Coordination API"

OPENAPI_TAGS = [
    {"name": "forecasting", "description": "Coarse-weather ingestion and ripening projections"},
    {"name": "notifications", "description": "Coordinator-approved volunteer pickup notices"},
    {"name": "operations", "description": "Health and provenance"},
]


def create_app(container: ApiContainer | None = None) -> FastAPI:
    """Build the API. Passing a container keeps tests deterministic."""

    resolved = container or build_fixture_container()
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        openapi_tags=OPENAPI_TAGS,
        description=(
            "Coordinator API for ripening projections and volunteer dispatch. "
            "Fixture deployments are labelled: responses carry `provenance` and never contain "
            "addresses, raw media references, or phone numbers."
        ),
    )
    app.state.container = resolved

    app.add_middleware(RequestIdMiddleware, clock=resolved.clock)

    @app.exception_handler(ApiProblem)
    async def handle_problem(request: Request, error: ApiProblem) -> JSONResponse:
        return problem_response(error, request=request, request_id=_request_id(request))

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, error: RequestValidationError) -> JSONResponse:
        fields = validation_fields(error)
        problem = ApiProblem(
            status=422,
            code="validation_failed",
            title="Request validation failed",
            detail="One or more fields are invalid.",
            fields=fields,
        )
        return problem_response(problem, request=request, request_id=_request_id(request))

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, error: Exception) -> JSONResponse:
        # No internals, no stack traces: correlation is via request_id only.
        del error
        problem = ApiProblem(
            status=500,
            code="internal_error",
            title="Internal server error",
            detail="The request could not be completed.",
        )
        return problem_response(problem, request=request, request_id=_request_id(request))

    @app.get(
        "/healthz",
        response_model=HealthResponse,
        tags=["operations"],
        summary="Liveness plus provenance of the running configuration",
    )
    async def healthz() -> HealthResponse:
        return HealthResponse(
            status="ok",
            provenance=resolved.provenance,
            demo_mode=resolved.demo_mode,
            worker_backend=type(resolved.runner_for("org-fixture-a")).__name__,
        )

    app.include_router(forecasting.router)
    app.include_router(notifications.router)
    return app


def _request_id(request: Request) -> str:
    """Correlation id from the middleware, or a fresh one for out-of-band calls."""

    return request_id_of(request.scope) or new_ulid()


def openapi_document(container: ApiContainer | None = None) -> dict[str, Any]:
    """Deterministic OpenAPI document used by the checked-in snapshot test."""

    return create_app(container).openapi()


def default_container() -> ApiContainer:
    return build_fixture_container()


__all__ = [
    "API_TITLE",
    "API_VERSION",
    "PROBLEM_MEDIA_TYPE",
    "REQUEST_ID_HEADER",
    "create_app",
    "default_container",
    "openapi_document",
]
