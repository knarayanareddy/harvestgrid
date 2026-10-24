"""Scheduled weather ingestion that keeps ripening projections current.

Execution model
---------------

The scheduler core is a deterministic, side-effect-free ``tick``: given a clock, a weather
source, and a state store it polls every *due* tree, deduplicates observations and
projections, recomputes the ripening curve, and reports what changed. Celery/RQ/cron only
provide the periodic trigger, so the interesting behaviour is testable without a broker.

Guarantees that mirror the specification:

* ``NFR-HG-002`` — every tree records its own ingestion duration; a tree that exceeds the
  five-minute forecast budget is reported, not silently retried forever.
* ``NFR-HG-004`` — repeated ticks with identical provider payloads create no duplicate
  observation, projection version, or adjustment.
* ``NFR-HG-007`` — a provider outage never cancels a glean or mutates an approved
  projection; it records a backoff and, after repeated failures, recommends coordinator
  review.
* Privacy — subscriptions carry only a coarse geohash. No address or exact coordinate can
  reach this module.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from harvestgrid_weather import WeatherObservation

from .ripening import (
    DailyTemperature,
    PhenologyError,
    PhenologyPrior,
    RipeningAdjustment,
    RipeningProjection,
    adjust_projection,
    project_ripening,
    projection_fingerprint,
    validate_coarse_geohash,
)

DEFAULT_POLL_INTERVAL = timedelta(hours=6)
MIN_POLL_INTERVAL = timedelta(hours=1)
MAX_POLL_INTERVAL = timedelta(days=7)
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_FORECAST_DAYS = 10
MAX_FORECAST_DAYS = 16
PER_TREE_BUDGET = timedelta(minutes=5)
MAX_TREES_PER_TICK = 50
REVIEW_AFTER_FAILURES = 3
TRANSIENT_FAILURE_CODES = frozenset(
    {"provider_unavailable", "provider_rate_limited", "provider_timeout"}
)


class SchedulerError(ValueError):
    """Invalid scheduler input or state transition."""


class WorkerBackendUnavailable(RuntimeError):
    """Raised when a documented worker backend (Celery/RQ) is not installed."""


@dataclass(frozen=True)
class TreeSubscription:
    """One tree's weather polling registration; coarse location only."""

    tree_id: str
    organization_id: str
    cultivar_id: str
    coarse_geohash: str
    prior: PhenologyPrior
    collection_approved: bool = True
    operations_consent_active: bool = True
    interval: timedelta = DEFAULT_POLL_INTERVAL
    last_polled_at: datetime | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("tree_id", self.tree_id),
            ("organization_id", self.organization_id),
            ("cultivar_id", self.cultivar_id),
        ):
            if not value.strip():
                raise SchedulerError(f"subscription_{name}_required")
        validate_coarse_geohash(self.coarse_geohash)
        if self.prior.cultivar_id != self.cultivar_id:
            raise SchedulerError("subscription_prior_cultivar_mismatch")
        if not MIN_POLL_INTERVAL <= self.interval <= MAX_POLL_INTERVAL:
            raise SchedulerError("subscription_interval_out_of_range")
        if self.last_polled_at is not None and self.last_polled_at.tzinfo is None:
            raise SchedulerError("subscription_last_polled_requires_timezone")

    @property
    def collection_active(self) -> bool:
        return self.collection_approved and self.operations_consent_active


@dataclass(frozen=True)
class TreeForecastRecord:
    """Durable projection state for one tree, versioned for optimistic updates."""

    tree_id: str
    coarse_geohash: str
    cultivar_id: str
    projection: RipeningProjection
    observation_ids: tuple[str, ...]
    updated_at: datetime
    version: int
    fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "tree_id": self.tree_id,
            "coarse_geohash": self.coarse_geohash,
            "cultivar_id": self.cultivar_id,
            "observation_ids": list(self.observation_ids),
            "updated_at": self.updated_at.isoformat(),
            "version": self.version,
            "fingerprint": self.fingerprint,
            "projection": self.projection.as_dict(),
        }


@dataclass(frozen=True)
class TreeFailure:
    tree_id: str
    attempts: int
    last_code: str
    last_attempt_at: datetime
    next_attempt_at: datetime

    @property
    def review_recommended(self) -> bool:
        return self.attempts >= REVIEW_AFTER_FAILURES


class WeatherSource(Protocol):
    """Structural type satisfied by ``harvestgrid_weather.OpenMeteoWeatherAdapter``."""

    def historical(self, *, geohash: str, start: date, end: date) -> WeatherObservation: ...

    def forecast(self, *, geohash: str, start: date, end: date) -> WeatherObservation: ...


class SchedulerStore(Protocol):
    """Durable-state port; the in-memory implementation is a labelled fixture."""

    def subscriptions(self) -> tuple[TreeSubscription, ...]: ...

    def mark_polled(self, tree_id: str, at: datetime) -> None: ...

    def record_observation(self, observation: WeatherObservation) -> bool: ...

    def observation_count(self) -> int: ...

    def latest_projection(self, tree_id: str) -> TreeForecastRecord | None: ...

    def save_projection(self, record: TreeForecastRecord) -> None: ...

    def failure(self, tree_id: str) -> TreeFailure | None: ...

    def save_failure(self, failure: TreeFailure) -> None: ...

    def clear_failure(self, tree_id: str) -> None: ...


@dataclass
class InMemorySchedulerStore:
    """Fixture store: process-local, no persistence, no raw media, coarse locations only."""

    _subscriptions: dict[str, TreeSubscription] = field(default_factory=dict)
    _observations: dict[str, WeatherObservation] = field(default_factory=dict)
    _projections: dict[str, TreeForecastRecord] = field(default_factory=dict)
    _failures: dict[str, TreeFailure] = field(default_factory=dict)
    provenance: str = "fixture"

    def add(self, subscription: TreeSubscription) -> TreeSubscription:
        if subscription.tree_id in self._subscriptions:
            raise SchedulerError("subscription_already_registered")
        self._subscriptions[subscription.tree_id] = subscription
        return subscription

    def subscriptions(self) -> tuple[TreeSubscription, ...]:
        return tuple(self._subscriptions[key] for key in sorted(self._subscriptions))

    def mark_polled(self, tree_id: str, at: datetime) -> None:
        subscription = self._subscriptions.get(tree_id)
        if subscription is None:
            raise SchedulerError("subscription_not_found")
        self._subscriptions[tree_id] = replace(subscription, last_polled_at=at)

    def record_observation(self, observation: WeatherObservation) -> bool:
        if observation.observation_id in self._observations:
            return False
        self._observations[observation.observation_id] = observation
        return True

    def observation_count(self) -> int:
        return len(self._observations)

    def observations(self) -> tuple[WeatherObservation, ...]:
        return tuple(self._observations[key] for key in sorted(self._observations))

    def latest_projection(self, tree_id: str) -> TreeForecastRecord | None:
        return self._projections.get(tree_id)

    def save_projection(self, record: TreeForecastRecord) -> None:
        self._projections[record.tree_id] = record

    def failure(self, tree_id: str) -> TreeFailure | None:
        return self._failures.get(tree_id)

    def save_failure(self, failure: TreeFailure) -> None:
        self._failures[failure.tree_id] = failure

    def clear_failure(self, tree_id: str) -> None:
        self._failures.pop(tree_id, None)


@dataclass(frozen=True)
class BackoffPolicy:
    base: timedelta = timedelta(minutes=5)
    maximum: timedelta = timedelta(hours=6)

    def delay_for(self, attempts: int) -> timedelta:
        if attempts < 1:
            raise SchedulerError("backoff_attempts_must_be_positive")
        seconds = self.base.total_seconds() * (2 ** min(attempts - 1, 10))
        return timedelta(seconds=min(seconds, self.maximum.total_seconds()))


@dataclass(frozen=True)
class TreeTickOutcome:
    tree_id: str
    status: str
    detail: str = ""
    duration_ms: int = 0
    version: int | None = None
    stage: str | None = None


@dataclass(frozen=True)
class TickReport:
    started_at: datetime
    finished_at: datetime
    trees_considered: int
    trees_due: int
    processed: tuple[TreeTickOutcome, ...]
    skipped_gated: tuple[str, ...]
    skipped_backoff: tuple[str, ...]
    skipped_cap: tuple[str, ...]
    failed: tuple[TreeTickOutcome, ...]
    observations_new: int
    observations_duplicate: int
    projections_updated: int
    projections_unchanged: int
    adjustments: tuple[RipeningAdjustment, ...]
    budget_breaches: tuple[str, ...]
    review_recommended: tuple[str, ...]

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    def as_dict(self) -> dict[str, Any]:
        """JSON-serializable summary for Celery/RQ results and logs (IDs and counts only)."""

        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_seconds": round(self.duration_seconds, 3),
            "trees_considered": self.trees_considered,
            "trees_due": self.trees_due,
            "processed": [
                {"tree_id": item.tree_id, "status": item.status, "version": item.version}
                for item in self.processed
            ],
            "skipped_gated": list(self.skipped_gated),
            "skipped_backoff": list(self.skipped_backoff),
            "skipped_cap": list(self.skipped_cap),
            "failed": [{"tree_id": item.tree_id, "detail": item.detail} for item in self.failed],
            "observations_new": self.observations_new,
            "observations_duplicate": self.observations_duplicate,
            "projections_updated": self.projections_updated,
            "projections_unchanged": self.projections_unchanged,
            "adjustments": [adjustment.as_dict() for adjustment in self.adjustments],
            "budget_breaches": list(self.budget_breaches),
            "review_recommended": list(self.review_recommended),
        }


@dataclass
class WeatherIngestionScheduler:
    """Polls coarse weather per tree, deduplicates, and refreshes ripening curves."""

    weather: WeatherSource
    store: SchedulerStore
    clock: Callable[[], datetime]
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    forecast_days: int = DEFAULT_FORECAST_DAYS
    max_trees_per_tick: int = MAX_TREES_PER_TICK
    per_tree_budget: timedelta = PER_TREE_BUDGET
    backoff: BackoffPolicy = field(default_factory=BackoffPolicy)

    def __post_init__(self) -> None:
        if self.lookback_days < 1 or self.lookback_days > 366:
            raise SchedulerError("lookback_days_out_of_range")
        if self.forecast_days < 0 or self.forecast_days > MAX_FORECAST_DAYS:
            raise SchedulerError("forecast_days_out_of_range")
        if self.max_trees_per_tick < 1:
            raise SchedulerError("max_trees_per_tick_must_be_positive")
        if self.per_tree_budget <= timedelta(0):
            raise SchedulerError("per_tree_budget_must_be_positive")

    def _now(self, now: datetime | None = None) -> datetime:
        value = now if now is not None else self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise SchedulerError("scheduler_clock_requires_timezone")
        return value.astimezone(UTC)

    def is_due(self, subscription: TreeSubscription, now: datetime) -> bool:
        if not subscription.collection_active:
            return False
        if subscription.last_polled_at is None:
            return True
        return now - subscription.last_polled_at >= subscription.interval

    def tick(self, *, now: datetime | None = None) -> TickReport:
        started_at = self._now(now)
        subscriptions = self.store.subscriptions()
        due: list[TreeSubscription] = []
        gated: list[str] = []
        backoff: list[str] = []
        for subscription in subscriptions:
            if not subscription.collection_active:
                gated.append(subscription.tree_id)
                continue
            if not self.is_due(subscription, started_at):
                continue
            failure = self.store.failure(subscription.tree_id)
            if failure is not None and failure.next_attempt_at > started_at:
                backoff.append(subscription.tree_id)
                continue
            due.append(subscription)

        selected = due[: self.max_trees_per_tick]
        skipped_cap = [item.tree_id for item in due[self.max_trees_per_tick :]]

        processed: list[TreeTickOutcome] = []
        failed: list[TreeTickOutcome] = []
        adjustments: list[RipeningAdjustment] = []
        budget_breaches: list[str] = []
        review: list[str] = []
        new_observations = 0
        duplicate_observations = 0
        updated = 0
        unchanged = 0

        for subscription in selected:
            tree_started = self._now()
            try:
                outcome, counts = self._ingest_tree(subscription, started_at)
            except PhenologyError as error:
                failure = self._record_failure(subscription, started_at, error.code)
                if failure.review_recommended:
                    review.append(subscription.tree_id)
                failed.append(
                    TreeTickOutcome(
                        tree_id=subscription.tree_id,
                        status="failed",
                        detail=error.code,
                    )
                )
                continue
            except SchedulerError as error:
                failure = self._record_failure(subscription, started_at, "store_error")
                if failure.review_recommended:
                    review.append(subscription.tree_id)
                failed.append(
                    TreeTickOutcome(
                        tree_id=subscription.tree_id,
                        status="failed",
                        detail=str(error),
                    )
                )
                continue
            except Exception as error:  # provider/transport failures must not stop the tick
                code = getattr(error, "code", None) or type(error).__name__
                failure = self._record_failure(subscription, started_at, str(code))
                if failure.review_recommended:
                    review.append(subscription.tree_id)
                failed.append(
                    TreeTickOutcome(
                        tree_id=subscription.tree_id,
                        status="failed",
                        detail=str(code),
                    )
                )
                continue

            elapsed = self._now() - tree_started
            if elapsed > self.per_tree_budget:
                budget_breaches.append(subscription.tree_id)
            processed.append(
                TreeTickOutcome(
                    tree_id=subscription.tree_id,
                    status=outcome.status,
                    duration_ms=int(elapsed.total_seconds() * 1000),
                    version=outcome.version,
                    stage=outcome.stage,
                )
            )
            new_observations += counts["new"]
            duplicate_observations += counts["duplicate"]
            updated += counts["updated"]
            unchanged += counts["unchanged"]
            if counts["adjustment"] is not None:
                adjustments.append(counts["adjustment"])

        return TickReport(
            started_at=started_at,
            finished_at=self._now(),
            trees_considered=len(subscriptions),
            trees_due=len(due),
            processed=tuple(processed),
            skipped_gated=tuple(gated),
            skipped_backoff=tuple(backoff),
            skipped_cap=tuple(skipped_cap),
            failed=tuple(failed),
            observations_new=new_observations,
            observations_duplicate=duplicate_observations,
            projections_updated=updated,
            projections_unchanged=unchanged,
            adjustments=tuple(adjustments),
            budget_breaches=tuple(budget_breaches),
            review_recommended=tuple(review),
        )

    def _record_failure(
        self, subscription: TreeSubscription, at: datetime, code: str
    ) -> TreeFailure:
        previous = self.store.failure(subscription.tree_id)
        attempts = 1 if previous is None else previous.attempts + 1
        failure = TreeFailure(
            tree_id=subscription.tree_id,
            attempts=attempts,
            last_code=code,
            last_attempt_at=at,
            next_attempt_at=at + self.backoff.delay_for(attempts),
        )
        self.store.save_failure(failure)
        return failure

    def _ingest_tree(
        self, subscription: TreeSubscription, now: datetime
    ) -> tuple[TreeTickOutcome, dict[str, Any]]:
        as_of = now.date()
        observed = self.weather.historical(
            geohash=subscription.coarse_geohash,
            start=as_of - timedelta(days=self.lookback_days),
            end=as_of,
        )
        forecast: WeatherObservation | None = None
        if self.forecast_days:
            forecast = self.weather.forecast(
                geohash=subscription.coarse_geohash,
                start=as_of + timedelta(days=1),
                end=as_of + timedelta(days=self.forecast_days),
            )

        new = 0
        duplicate = 0
        for observation in (observed, forecast):
            if observation is None:
                continue
            if self.store.record_observation(observation):
                new += 1
            else:
                duplicate += 1

        projection = project_ripening(
            prior=subscription.prior,
            coarse_geohash=subscription.coarse_geohash,
            as_of=as_of,
            observed=_to_daily(observed),
            forecast=_to_daily(forecast) if forecast is not None else (),
        )
        fingerprint = projection_fingerprint(projection)
        previous = self.store.latest_projection(subscription.tree_id)
        adjustment: RipeningAdjustment | None = None
        if previous is not None and previous.fingerprint == fingerprint:
            version = previous.version
            unchanged = 1
            updated = 0
            record = previous
        else:
            version = 1 if previous is None else previous.version + 1
            unchanged = 0
            updated = 1
            record = TreeForecastRecord(
                tree_id=subscription.tree_id,
                coarse_geohash=subscription.coarse_geohash,
                cultivar_id=subscription.cultivar_id,
                projection=projection,
                observation_ids=tuple(
                    item.observation_id for item in (observed, forecast) if item is not None
                ),
                updated_at=now,
                version=version,
                fingerprint=fingerprint,
            )
            self.store.save_projection(record)
            if previous is not None:
                adjustment = adjust_projection(
                    tree_id=subscription.tree_id,
                    previous=previous.projection,
                    current=projection,
                )

        self.store.mark_polled(subscription.tree_id, now)
        self.store.clear_failure(subscription.tree_id)
        return (
            TreeTickOutcome(
                tree_id=subscription.tree_id,
                status="updated" if updated else "unchanged",
                version=version,
                stage=projection.stage.value,
            ),
            {
                "new": new,
                "duplicate": duplicate,
                "updated": updated,
                "unchanged": unchanged,
                "adjustment": adjustment,
            },
        )


def _to_daily(observation: WeatherObservation) -> tuple[DailyTemperature, ...]:
    return tuple(
        DailyTemperature(
            day=day.day,
            temperature_min_c=day.temperature_min_c,
            temperature_max_c=day.temperature_max_c,
        )
        for day in observation.daily
    )


def run_tick_payload(scheduler: WeatherIngestionScheduler) -> dict[str, Any]:
    """Serializable tick result used by Celery/RQ tasks and cron entry points."""

    return scheduler.tick().as_dict()


@dataclass
class SchedulerWorker:
    """Long-running loop; Celery/RQ replace this in deployed environments."""

    scheduler: WeatherIngestionScheduler
    clock: Callable[[], datetime]
    sleeper: Callable[[float], None]
    interval: timedelta = timedelta(minutes=15)
    _stopped: bool = False
    reports: list[TickReport] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.interval < timedelta(seconds=30):
            raise SchedulerError("worker_interval_too_short")

    def stop(self) -> None:
        self._stopped = True

    def run_once(self) -> TickReport:
        report = self.scheduler.tick()
        self.reports.append(report)
        return report

    def run(self, *, iterations: int | None = None) -> dict[str, Any]:
        completed = 0
        while not self._stopped and (iterations is None or completed < iterations):
            report = self.run_once()
            completed += 1
            if iterations is not None and completed >= iterations:
                break
            self.sleeper(self.interval.total_seconds())
            if report.trees_due == 0:
                continue
        return {
            "iterations": completed,
            "observations_new": sum(item.observations_new for item in self.reports),
            "projections_updated": sum(item.projections_updated for item in self.reports),
            "failed": sum(len(item.failed) for item in self.reports),
            "stopped": self._stopped,
        }


@dataclass(frozen=True)
class CelerySettings:
    """Pinned Celery configuration for the forecasting beat schedule."""

    broker_url: str
    beat_seconds: float = 900.0
    task_name: str = "harvestgrid.forecasting.poll_weather_tick"

    def beat_schedule(self) -> dict[str, Any]:
        return {
            "harvestgrid-weather-poll": {
                "task": self.task_name,
                "schedule": self.beat_seconds,
                "options": {"expires": self.beat_seconds},
            }
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "broker_url": self.broker_url,
            "beat_seconds": self.beat_seconds,
            "task_name": self.task_name,
            "beat_schedule": self.beat_schedule(),
        }


def build_celery_app(*, scheduler: WeatherIngestionScheduler, settings: CelerySettings) -> Any:
    """Build a Celery app with one periodic task.

    Celery is an optional runtime dependency: when it is absent this raises
    :class:`WorkerBackendUnavailable` instead of pretending the schedule exists.
    """

    if not settings.broker_url.strip():
        raise SchedulerError("celery_broker_url_required")
    try:
        from celery import Celery  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        raise WorkerBackendUnavailable(
            "celery is not installed; the periodic weather schedule cannot be started"
        ) from None

    app = Celery("harvestgrid-forecasting", broker=settings.broker_url)
    app.conf.beat_schedule = settings.beat_schedule()
    app.conf.timezone = "UTC"
    app.conf.task_serializer = "json"
    app.conf.accept_content = ["json"]

    @app.task(name=settings.task_name, ignore_result=False)  # type: ignore[misc]
    def poll_weather_tick() -> dict[str, Any]:
        return run_tick_payload(scheduler)

    app.harvestgrid_task = poll_weather_tick
    return app


def build_rq_job(*, scheduler: WeatherIngestionScheduler, queue: Any) -> Any:
    """Enqueue one tick on an RQ queue; RQ is optional and fails visibly when absent."""

    try:
        import rq  # type: ignore[import-not-found]  # noqa: F401
    except ModuleNotFoundError:
        raise WorkerBackendUnavailable(
            "rq is not installed; the periodic weather schedule cannot be started"
        ) from None
    return queue.enqueue(run_tick_payload, scheduler, job_id="harvestgrid-weather-poll")


def serialized_tick(payload: dict[str, Any]) -> str:
    """JSON encoding guard for task results: no secrets, no coordinates, IDs only."""

    forbidden = ("address", "latitude", "longitude", "phone", "access_notes")
    text = json.dumps(payload, sort_keys=True)
    lowered = text.lower()
    for token in forbidden:
        if token in lowered:
            raise SchedulerError(f"tick_payload_contains_forbidden_field:{token}")
    return text
