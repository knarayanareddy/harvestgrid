"""FastAPI dependencies: container lookup, authentication, and idempotency helpers.

Every dependency is a coroutine function. FastAPI offloads *sync* dependencies and handlers to a
worker thread, and the API never needs one: token resolution, idempotency bookkeeping, and the
scheduler/dispatcher calls that block are the only work a route does, so route bodies stay on the
event loop and call the blocking services through ``asyncio.to_thread``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, Header, Request

from .actors import Actor, Role, require_roles
from .container import ApiContainer
from .idempotency import (
    IdempotencyStore,
    canonical_request_hash,
    require_idempotency_key,
    resolve_replay,
    store_response,
)
from .problems import ApiProblem, unauthenticated


async def get_container(request: Request) -> ApiContainer:
    container: ApiContainer | None = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover - create_app always sets this
        raise ApiProblem(
            status=500,
            code="container_missing",
            title="Internal configuration error",
            detail="The API container is not configured.",
        )
    return container


ContainerDep = Annotated[ApiContainer, Depends(get_container)]


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header is None:
        return None
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        raise unauthenticated("The Authorization header must use the Bearer scheme.")
    return credentials


async def get_actor(request: Request, container: ContainerDep) -> Actor:
    return container.tokens.resolve(_bearer_token(request))


ActorDep = Annotated[Actor, Depends(get_actor)]


async def coordinator(actor: ActorDep) -> Actor:
    require_roles(actor, Role.COORDINATOR)
    return actor


async def projection_reader(actor: ActorDep) -> Actor:
    require_roles(actor, Role.OWNER, Role.COORDINATOR, Role.EVALUATOR)
    return actor


CoordinatorDep = Annotated[Actor, Depends(coordinator)]
ProjectionReaderDep = Annotated[Actor, Depends(projection_reader)]

IdempotencyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


def idempotency_scope(actor: Actor, route: str) -> str:
    """Keys are scoped per actor and route so unrelated clients cannot collide."""

    return f"{actor.organization_id}:{actor.actor_id}:{route}"


@dataclass(frozen=True)
class IdempotentRequest:
    """A validated ``Idempotency-Key`` plus the canonical hash of the request body."""

    key: str
    request_sha256: str
    scope: str

    @classmethod
    def open(
        cls,
        *,
        actor: Actor,
        route: str,
        header_value: str | None,
        payload: Any,
    ) -> IdempotentRequest:
        key = require_idempotency_key(header_value)
        return cls(
            key=key,
            request_sha256=canonical_request_hash(payload, route=route),
            scope=idempotency_scope(actor, route),
        )

    def replay(
        self, store: IdempotencyStore, *, now: datetime
    ) -> tuple[int, dict[str, Any]] | None:
        stored = resolve_replay(
            store,
            scope=self.scope,
            key=self.key,
            request_sha256=self.request_sha256,
            now=now,
        )
        if stored is None:
            return None
        return stored.status, stored.body

    def remember(
        self,
        store: IdempotencyStore,
        *,
        status: int,
        body: dict[str, Any],
        now: datetime,
    ) -> None:
        store_response(
            store,
            scope=self.scope,
            key=self.key,
            request_sha256=self.request_sha256,
            status=status,
            body=body,
            now=now,
        )


__all__ = [
    "ActorDep",
    "ContainerDep",
    "CoordinatorDep",
    "IdempotencyHeader",
    "IdempotentRequest",
    "ProjectionReaderDep",
    "get_actor",
    "get_container",
    "idempotency_scope",
]
