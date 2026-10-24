"""Dependency container wiring the API to the forecasting and notification services.

Everything is injected so tests can supply a fixed clock, a seeded scheduler store, a
recording SMS transport, and a deterministic idempotency store. The default container is an
explicitly labelled fixture: synthetic weather, no telephony account, in-memory state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from harvestgrid_forecasting import (
    InMemorySchedulerStore,
    SchedulerStore,
    TreeFailure,
    TreeForecastRecord,
    TreeSubscription,
    WeatherIngestionScheduler,
    WeatherSource,
)
from harvestgrid_forecasting.fixtures import (
    SyntheticWeatherSource,
    seed_fixture_store,
)
from harvestgrid_notifications import (
    InMemoryNotificationLedger,
    NotificationDispatchService,
    RecordingSmsTransport,
    SmsTransport,
    VolunteerContact,
)
from harvestgrid_weather import WeatherObservation

from .actors import Actor, FixtureTokenIssuer, Role
from .idempotency import IdempotencyStore, InMemoryIdempotencyStore
from .workers import InlineJobRunner, InMemoryJobRegistry, JobRunner

FIXTURE_PROVENANCE = "fixture"


def _default_clock() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class VolunteerProfile:
    """Directory entry the API resolves a volunteer id to. Phones never arrive in requests."""

    contact: VolunteerContact
    organization_id: str


@dataclass(frozen=True)
class RestrictedGleanDetail:
    """Exact address/access note used **only** to prove a message does not contain them."""

    organization_id: str
    address: str
    access_notes: str = ""

    def markers(self) -> tuple[str, ...]:
        return tuple(part for part in (self.address, self.access_notes) if part.strip())


class OrganizationScopedStore:
    """Restricts a scheduler store to one organization.

    The scheduler core stays organization-agnostic; scoping lives at the API boundary so a
    coordinator can never tick or read another organization's trees, and a foreign tree id is
    indistinguishable from a missing one.
    """

    def __init__(self, inner: SchedulerStore, organization_id: str) -> None:
        self._inner = inner
        self._organization_id = organization_id

    def subscriptions(self) -> tuple[TreeSubscription, ...]:
        return tuple(
            item
            for item in self._inner.subscriptions()
            if item.organization_id == self._organization_id
        )

    def subscription(self, tree_id: str) -> TreeSubscription | None:
        return next((item for item in self.subscriptions() if item.tree_id == tree_id), None)

    def mark_polled(self, tree_id: str, at: datetime) -> None:
        self._inner.mark_polled(tree_id, at)

    def record_observation(self, observation: WeatherObservation) -> bool:
        return self._inner.record_observation(observation)

    def observation_count(self) -> int:
        return self._inner.observation_count()

    def latest_projection(self, tree_id: str) -> TreeForecastRecord | None:
        if self.subscription(tree_id) is None:
            return None
        return self._inner.latest_projection(tree_id)

    def save_projection(self, record: TreeForecastRecord) -> None:
        self._inner.save_projection(record)

    def failure(self, tree_id: str) -> TreeFailure | None:
        if self.subscription(tree_id) is None:
            return None
        return self._inner.failure(tree_id)

    def save_failure(self, failure: TreeFailure) -> None:
        self._inner.save_failure(failure)

    def clear_failure(self, tree_id: str) -> None:
        self._inner.clear_failure(tree_id)


@dataclass
class ApiContainer:
    """Explicit dependency graph for the API."""

    tokens: FixtureTokenIssuer
    store: SchedulerStore
    weather: WeatherSource
    transport: SmsTransport
    ledger: InMemoryNotificationLedger
    volunteers: dict[str, VolunteerProfile]
    restricted: dict[str, RestrictedGleanDetail]
    jobs: InMemoryJobRegistry
    idempotency: IdempotencyStore
    clock: Callable[[], datetime]
    runner_factory: Callable[[OrganizationScopedStore], JobRunner]
    provenance: str = FIXTURE_PROVENANCE
    demo_mode: bool = True
    _dispatch_services: dict[str, NotificationDispatchService] = field(default_factory=dict)

    # -- per-organization views -------------------------------------------------
    def scoped_store(self, organization_id: str) -> OrganizationScopedStore:
        return OrganizationScopedStore(self.store, organization_id)

    def scheduler_for(self, organization_id: str) -> WeatherIngestionScheduler:
        return WeatherIngestionScheduler(
            weather=self.weather,
            store=self.scoped_store(organization_id),
            clock=self.clock,
        )

    def runner_for(self, organization_id: str) -> JobRunner:
        return self.runner_factory(self.scoped_store(organization_id))

    def dispatch_service(self, organization_id: str) -> NotificationDispatchService:
        existing = self._dispatch_services.get(organization_id)
        if existing is not None:
            return existing
        service = NotificationDispatchService(
            transport=self.transport,
            ledger=self.ledger,
            clock=self.clock,
        )
        self._dispatch_services[organization_id] = service
        return service

    def volunteer(self, volunteer_id: str, organization_id: str) -> VolunteerProfile | None:
        profile = self.volunteers.get(volunteer_id)
        if profile is None or profile.organization_id != organization_id:
            return None
        return profile

    def restricted_for(self, glean_id: str, organization_id: str) -> RestrictedGleanDetail | None:
        detail = self.restricted.get(glean_id)
        if detail is None or detail.organization_id != organization_id:
            return None
        # Restricted details are only ever used as leak markers; org scoping still applies so a
        # foreign organization cannot even confirm that the glean exists.
        return detail

    def now(self) -> datetime:
        return self.clock().astimezone(UTC)


#: Fixture bearer tokens. Deliberately synthetic and documented; no real identity system runs.
FIXTURE_TOKENS: dict[str, Actor] = {
    "fixture-coordinator-a": Actor(
        actor_id="coordinator-a",
        roles=frozenset({Role.COORDINATOR}),
        organization_id="org-fixture-a",
        display_name="Coordinator A (fixture)",
    ),
    "fixture-owner-a": Actor(
        actor_id="owner-a",
        roles=frozenset({Role.OWNER}),
        organization_id="org-fixture-a",
        display_name="Owner A (fixture)",
    ),
    "fixture-evaluator-a": Actor(
        actor_id="evaluator-a",
        roles=frozenset({Role.EVALUATOR}),
        organization_id="org-fixture-a",
        display_name="Evaluator A (fixture)",
    ),
    "fixture-volunteer-a": Actor(
        actor_id="volunteer-a",
        roles=frozenset({Role.VOLUNTEER}),
        organization_id="org-fixture-a",
        display_name="Volunteer A (fixture)",
    ),
    "fixture-coordinator-b": Actor(
        actor_id="coordinator-b",
        roles=frozenset({Role.COORDINATOR}),
        organization_id="org-fixture-b",
        display_name="Coordinator B (fixture)",
    ),
}

#: Fixture volunteer directory; the phone numbers are obviously fake and never dialled.
FIXTURE_VOLUNTEERS: dict[str, VolunteerProfile] = {
    "volunteer-a": VolunteerProfile(
        contact=VolunteerContact(
            volunteer_id="volunteer-a",
            phone_e164="+31600000001",
            sms_opt_in=True,
            sms_opt_in_version="sms-opt-in-v1",
        ),
        organization_id="org-fixture-a",
    ),
    "volunteer-b": VolunteerProfile(
        contact=VolunteerContact(
            volunteer_id="volunteer-b",
            phone_e164="+31600000002",
            sms_opt_in=True,
            sms_opt_in_version="sms-opt-in-v1",
        ),
        organization_id="org-fixture-a",
    ),
    "volunteer-c": VolunteerProfile(
        contact=VolunteerContact(
            volunteer_id="volunteer-c",
            phone_e164="+31600000003",
            sms_opt_in=False,
            sms_opt_in_version=None,
        ),
        organization_id="org-fixture-a",
    ),
}

#: Fictional restricted details used only to prove messages exclude them.
FIXTURE_RESTRICTED: dict[str, RestrictedGleanDetail] = {
    "glean-fixture-01": RestrictedGleanDetail(
        organization_id="org-fixture-a",
        address="FIXTURE 1 Example Street",
        access_notes="gate code 0000",
    )
}


def build_fixture_container(
    *,
    clock: Callable[[], datetime] | None = None,
    store: SchedulerStore | None = None,
    weather: WeatherSource | None = None,
    transport: SmsTransport | None = None,
    tokens: dict[str, Actor] | None = None,
    volunteers: dict[str, VolunteerProfile] | None = None,
    restricted: dict[str, RestrictedGleanDetail] | None = None,
) -> ApiContainer:
    """Build the labelled fixture container used by demo mode and the default test suite."""

    resolved_clock = clock or _default_clock
    scheduler_store = store if store is not None else InMemorySchedulerStore()
    if store is None:
        seed_fixture_store(scheduler_store)
    registry = InMemoryJobRegistry()
    provenance = FIXTURE_PROVENANCE

    def runner_factory(scoped: OrganizationScopedStore) -> JobRunner:
        del scoped
        return InlineJobRunner(clock=resolved_clock, registry=registry, provenance=provenance)

    return ApiContainer(
        tokens=FixtureTokenIssuer(tokens or FIXTURE_TOKENS),
        store=scheduler_store,
        weather=weather or SyntheticWeatherSource(),
        transport=transport or RecordingSmsTransport(),
        ledger=InMemoryNotificationLedger(),
        volunteers=volunteers or FIXTURE_VOLUNTEERS,
        restricted=restricted or FIXTURE_RESTRICTED,
        jobs=registry,
        idempotency=InMemoryIdempotencyStore(),
        clock=resolved_clock,
        runner_factory=runner_factory,
        provenance=provenance,
        demo_mode=True,
    )
