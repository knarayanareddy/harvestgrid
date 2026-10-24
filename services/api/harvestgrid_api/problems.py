"""RFC 9457 ``application/problem+json`` errors.

Every error carries a stable ``code`` for clients, a correlation ``request_id``, and an
``instance`` path. Errors never include provider payloads, phone numbers, addresses, or stack
traces; unexpected failures collapse to a generic 500.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PROBLEM_BASE_URI = "https://errors.harvestgrid.example"
PROBLEM_MEDIA_TYPE = "application/problem+json"


@dataclass
class FieldProblem:
    path: str
    code: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code}


@dataclass
class ApiProblem(Exception):
    """A structured HTTP problem; raise this from routes and dependencies."""

    status: int
    code: str
    title: str
    detail: str = ""
    fields: tuple[FieldProblem, ...] = ()
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Exception.__init__(self, f"{self.status} {self.code}")
        if not 400 <= self.status <= 599:
            raise AssertionError(f"invalid problem status: {self.status}")

    def as_dict(self, *, instance: str, request_id: str) -> dict[str, Any]:
        document: dict[str, Any] = {
            "type": f"{PROBLEM_BASE_URI}/{self.code.replace('_', '-')}",
            "title": self.title,
            "status": self.status,
            "code": self.code,
            "instance": instance,
            "request_id": request_id,
        }
        if self.detail:
            document["detail"] = self.detail
        if self.fields:
            document["fields"] = [item.as_dict() for item in self.fields]
        return document


def unauthenticated(detail: str = "A bearer token is required.") -> ApiProblem:
    return ApiProblem(
        status=401,
        code="unauthenticated",
        title="Authentication required",
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def forbidden(code: str = "role_forbidden", detail: str = "") -> ApiProblem:
    return ApiProblem(
        status=403,
        code=code,
        title="Forbidden",
        detail=detail or "The authenticated actor may not perform this action.",
    )


def not_found(code: str, detail: str = "") -> ApiProblem:
    return ApiProblem(
        status=404,
        code=code,
        title="Not found",
        detail=detail or "The requested resource is not available to this actor.",
    )


def conflict(code: str, detail: str = "") -> ApiProblem:
    return ApiProblem(status=409, code=code, title="Conflict", detail=detail)


def unprocessable(code: str, detail: str, fields: tuple[FieldProblem, ...] = ()) -> ApiProblem:
    return ApiProblem(
        status=422,
        code=code,
        title="Request validation failed",
        detail=detail,
        fields=fields,
    )


def bad_request(code: str, detail: str) -> ApiProblem:
    return ApiProblem(status=400, code=code, title="Bad request", detail=detail)


def unavailable(code: str, detail: str) -> ApiProblem:
    return ApiProblem(status=503, code=code, title="Service unavailable", detail=detail)
