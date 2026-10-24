"""HarvestGrid coordination API: forecasting projections and volunteer dispatch."""

from .actors import Actor, FixtureTokenIssuer, Role, require_roles
from .app import API_TITLE, API_VERSION, create_app, default_container, openapi_document
from .container import (
    FIXTURE_TOKENS,
    FIXTURE_VOLUNTEERS,
    ApiContainer,
    OrganizationScopedStore,
    VolunteerProfile,
    build_fixture_container,
)
from .idempotency import (
    IDEMPOTENCY_HEADER,
    InMemoryIdempotencyStore,
    canonical_request_hash,
    require_idempotency_key,
)
from .problems import ApiProblem, FieldProblem
from .workers import (
    BrokerJobRunner,
    InlineJobRunner,
    InMemoryJobRegistry,
    JobRecord,
    WorkerBackendUnavailable,
    completed_job,
    digest_payload,
)

__all__ = [
    "API_TITLE",
    "API_VERSION",
    "FIXTURE_TOKENS",
    "FIXTURE_VOLUNTEERS",
    "IDEMPOTENCY_HEADER",
    "Actor",
    "ApiContainer",
    "ApiProblem",
    "BrokerJobRunner",
    "FieldProblem",
    "FixtureTokenIssuer",
    "InMemoryIdempotencyStore",
    "InMemoryJobRegistry",
    "InlineJobRunner",
    "JobRecord",
    "OrganizationScopedStore",
    "Role",
    "VolunteerProfile",
    "WorkerBackendUnavailable",
    "build_fixture_container",
    "canonical_request_hash",
    "completed_job",
    "create_app",
    "default_container",
    "digest_payload",
    "openapi_document",
    "require_idempotency_key",
    "require_roles",
]
