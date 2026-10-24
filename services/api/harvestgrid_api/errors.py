"""Problem response rendering and request-validation field extraction."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .problems import PROBLEM_MEDIA_TYPE, ApiProblem, FieldProblem

#: Pydantic location prefixes that add no meaning for API clients.
_IGNORED_LOCATIONS = frozenset({"body", "query", "path", "header", "cookie"})


def _json_path(location: tuple[Any, ...]) -> str:
    segments = [str(part) for part in location if part not in _IGNORED_LOCATIONS]
    if not segments:
        return "/"
    return "/" + "/".join(segments)


def validation_fields(error: RequestValidationError) -> tuple[FieldProblem, ...]:
    """Convert pydantic errors into ``{"path", "code"}`` pairs without echoing input."""

    seen: set[tuple[str, str]] = set()
    fields: list[FieldProblem] = []
    for item in error.errors():
        path = _json_path(tuple(item.get("loc", ())))
        code = str(item.get("type", "invalid")).replace(".", "_")
        if (path, code) in seen:
            continue
        seen.add((path, code))
        fields.append(FieldProblem(path=path, code=code))
    return tuple(fields)


def problem_response(problem: ApiProblem, *, request: Request, request_id: str) -> JSONResponse:
    body = problem.as_dict(instance=request.url.path, request_id=request_id)
    headers = dict(problem.headers)
    headers.setdefault("X-Request-Id", request_id)
    return JSONResponse(
        status_code=problem.status,
        content=body,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )
