"""Actor identities and role checks.

Authentication here is a **labelled fixture**: opaque bearer tokens map to actors with roles
and an organization. No Cognito user pool exists in this repository, and the fixture tokens
are obviously synthetic. What is real is the authorization logic: role gates, organization
scoping, and non-leaking denials, which are the parts the domain depends on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .problems import forbidden, unauthenticated


class Role(StrEnum):
    OWNER = "owner"
    COORDINATOR = "coordinator"
    VOLUNTEER = "volunteer"
    RECIPIENT = "recipient"
    EVALUATOR = "evaluator"


@dataclass(frozen=True)
class Actor:
    actor_id: str
    roles: frozenset[Role]
    organization_id: str
    display_name: str = ""

    def __post_init__(self) -> None:
        if not self.actor_id.strip():
            raise ValueError("actor_id required")
        if not self.organization_id.strip():
            raise ValueError("organization_id required")
        if not self.roles:
            raise ValueError("actor requires at least one role")

    def has_any(self, *roles: Role) -> bool:
        return bool(self.roles.intersection(roles))


class FixtureTokenIssuer:
    """Maps opaque fixture tokens to actors; unknown tokens are rejected."""

    def __init__(self, tokens: dict[str, Actor]) -> None:
        if not tokens:
            raise ValueError("fixture token map must not be empty")
        self._tokens = dict(tokens)

    def resolve(self, token: str | None) -> Actor:
        if token is None or not token.strip():
            raise unauthenticated()
        actor = self._tokens.get(token.strip())
        if actor is None:
            raise unauthenticated("The bearer token is not recognized.")
        return actor

    def tokens_for(self, actor_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(token for token, actor in self._tokens.items() if actor.actor_id == actor_id)
        )


def require_roles(actor: Actor, *roles: Role) -> None:
    if actor.has_any(*roles):
        return
    granted = ",".join(sorted(role.value for role in actor.roles))
    needed = ",".join(sorted(role.value for role in roles))
    raise forbidden(detail=f"Requires one of: {needed}. Token roles: {granted}.")
