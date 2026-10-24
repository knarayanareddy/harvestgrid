import importlib.util
import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

import pytest
from harvestgrid_forecasting import (
    BackoffPolicy,
    CelerySettings,
    InMemorySchedulerStore,
    PhenologyPrior,
    RipeningStage,
    SchedulerError,
    SchedulerWorker,
    TreeSubscription,
    WeatherIngestionScheduler,
    WorkerBackendUnavailable,
    build_celery_app,
    build_rq_job,
    run_tick_payload,
    serialized_tick,
)
from harvestgrid_weather import DailyWeather, WeatherError, WeatherObservation, compute_features

T0 = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)
GEOHASH = "u173z"
STAGES: tuple[tuple[RipeningStage, float], ...] = (
    (RipeningStage.DORMANT, 0.0),
    (RipeningStage.BLOOM, 120.0),
    (RipeningStage.FRUIT_SET, 200.0),
    (RipeningStage.VERAISON, 700.0),
    (RipeningStage.HARVEST_RIPE, 1000.0),
)


def prior(cultivar_id: str = "european_plum") -> PhenologyPrior:
    return PhenologyPrior(
        cultivar_id=cultivar_id,
        content_version="european_plum_v1",
        base_temperature_c=5.0,
        stage_gdd=STAGES,
        review_status="approved",
        source="fixture_prior_for_tests",
    )


def subscription(
    tree_id: str = "tree-1",
    *,
    collection_approved: bool = True,
    consent: bool = True,
    interval: timedelta = timedelta(hours=6),
    last_polled_at: datetime | None = None,
) -> TreeSubscription:
    return TreeSubscription(
        tree_id=tree_id,
        organization_id="org-fixture",
        cultivar_id="european_plum",
        coarse_geohash=GEOHASH,
        prior=prior(),
        collection_approved=collection_approved,
        operations_consent_active=consent,
        interval=interval,
        last_polled_at=last_polled_at,
    )


def observation(kind: str, start: date, end: date, *, mean: float, sha: str) -> WeatherObservation:
    daily = tuple(
        DailyWeather(
            day=start + timedelta(days=offset),
            temperature_mean_c=mean,
            temperature_min_c=mean - 4.0,
            temperature_max_c=mean + 4.0,
            precipitation_mm=0.0,
        )
        for offset in range((end - start).days + 1)
    )
    return WeatherObservation(
        observation_id=f"weather-{kind}-{GEOHASH}-{start}-{end}-{sha[:12]}",
        kind=kind,
        provider="fixture-provider",
        endpoint=f"fixture://{kind}",
        coarse_geohash=GEOHASH,
        coarse_latitude=52.0,
        coarse_longitude=4.4,
        requested_start=start,
        requested_end=end,
        source_timestamps=tuple(day.day for day in daily),
        fetched_at=T0,
        payload_sha256=sha,
        daily=daily,
        features=compute_features(daily),
        from_cache=False,
    )


@dataclass
class FakeWeatherSource:
    historical_mean: float = 13.0
    forecast_mean: float = 16.0
    sha: str = "a" * 64
    failure: Exception | None = None
    calls: list[tuple[str, str, date, date]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    def historical(self, *, geohash: str, start: date, end: date) -> WeatherObservation:
        if self.failure is not None:
            raise self.failure
        assert self.calls is not None
        self.calls.append(("historical", geohash, start, end))
        return observation("historical", start, end, mean=self.historical_mean, sha=self.sha)

    def forecast(self, *, geohash: str, start: date, end: date) -> WeatherObservation:
        if self.failure is not None:
            raise self.failure
        assert self.calls is not None
        self.calls.append(("forecast", geohash, start, end))
        return observation("forecast", start, end, mean=self.forecast_mean, sha=self.sha)


@dataclass
class StepClock:
    current: datetime
    step: timedelta = timedelta(0)

    def __call__(self) -> datetime:
        value = self.current
        self.current = self.current + self.step
        return value


def scheduler(
    *,
    source: FakeWeatherSource | None = None,
    store: InMemorySchedulerStore | None = None,
    clock: StepClock | None = None,
    **kwargs: object,
) -> tuple[WeatherIngestionScheduler, InMemorySchedulerStore, FakeWeatherSource]:
    weather = source or FakeWeatherSource()
    fixture_store = store or InMemorySchedulerStore()
    engine = WeatherIngestionScheduler(
        weather=weather,
        store=fixture_store,
        clock=clock or StepClock(T0),
        **kwargs,  # type: ignore[arg-type]
    )
    return engine, fixture_store, weather


def test_first_tick_ingests_weather_and_creates_version_one() -> None:
    engine, store, weather = scheduler()
    store.add(subscription())
    report = engine.tick(now=T0)

    assert report.trees_considered == 1
    assert report.trees_due == 1
    assert report.observations_new == 2
    assert report.observations_duplicate == 0
    assert report.projections_updated == 1
    assert report.projections_unchanged == 0
    assert report.failed == ()
    assert report.processed[0].status == "updated"
    assert report.processed[0].version == 1
    assert report.adjustments == ()

    record = store.latest_projection("tree-1")
    assert record is not None
    assert record.version == 1
    assert len(record.observation_ids) == 2
    assert record.projection.experimental is False
    assert store.subscriptions()[0].last_polled_at == T0
    assert weather.calls == [
        ("historical", GEOHASH, T0.date() - timedelta(days=30), T0.date()),
        ("forecast", GEOHASH, T0.date() + timedelta(days=1), T0.date() + timedelta(days=10)),
    ]


def test_repeat_tick_creates_no_duplicates() -> None:
    engine, store, _ = scheduler()
    store.add(subscription())
    engine.tick(now=T0)
    later = T0 + timedelta(hours=7)
    report = engine.tick(now=later)

    assert report.trees_due == 1
    assert report.observations_new == 0
    assert report.observations_duplicate == 2
    assert report.projections_updated == 0
    assert report.projections_unchanged == 1
    assert report.adjustments == ()
    record = store.latest_projection("tree-1")
    assert record is not None
    assert record.version == 1
    assert store.observation_count() == 2


def test_tick_inside_the_poll_interval_is_skipped() -> None:
    engine, store, weather = scheduler()
    store.add(subscription())
    engine.tick(now=T0)
    before = len(weather.calls)
    report = engine.tick(now=T0 + timedelta(hours=1))
    assert report.trees_due == 0
    assert report.processed == ()
    assert len(weather.calls) == before


def test_warmer_forecast_bumps_the_version_and_reports_an_adjustment() -> None:
    engine, store, weather = scheduler()
    store.add(subscription())
    engine.tick(now=T0)
    weather.forecast_mean = 22.0
    weather.sha = "b" * 64
    report = engine.tick(now=T0 + timedelta(hours=7))

    # A new provider payload (new digest) and a new date range mean both fetches are new.
    assert report.observations_new == 2
    assert report.observations_duplicate == 0
    assert report.projections_updated == 1
    assert len(report.adjustments) == 1
    adjustment = report.adjustments[0]
    assert adjustment.tree_id == "tree-1"
    assert adjustment.direction == "earlier"
    record = store.latest_projection("tree-1")
    assert record is not None
    assert record.version == 2


def test_unapproved_collection_is_skipped_without_a_weather_call() -> None:
    engine, store, weather = scheduler()
    store.add(subscription(collection_approved=False))
    store.add(subscription("tree-2", consent=False))
    report = engine.tick(now=T0)

    assert report.skipped_gated == ("tree-1", "tree-2")
    assert report.trees_due == 0
    assert weather.calls == []
    assert store.latest_projection("tree-1") is None


def test_provider_failure_backs_off_and_recovers() -> None:
    engine, store, weather = scheduler(backoff=BackoffPolicy(base=timedelta(minutes=10)))
    store.add(subscription())
    weather.failure = WeatherError("provider_unavailable", detail="status=503")

    report = engine.tick(now=T0)
    assert len(report.failed) == 1
    assert report.failed[0].detail == "provider_unavailable"
    failure = store.failure("tree-1")
    assert failure is not None
    assert failure.attempts == 1
    assert failure.next_attempt_at == T0 + timedelta(minutes=10)

    blocked = engine.tick(now=T0 + timedelta(minutes=5))
    assert blocked.skipped_backoff == ("tree-1",)
    assert blocked.failed == ()

    weather.failure = None
    recovered = engine.tick(now=T0 + timedelta(minutes=11))
    assert recovered.failed == ()
    assert recovered.processed[0].status == "updated"
    assert store.failure("tree-1") is None


def test_repeated_failures_recommend_coordinator_review() -> None:
    engine, store, weather = scheduler(backoff=BackoffPolicy(base=timedelta(minutes=1)))
    store.add(subscription())
    weather.failure = WeatherError("provider_unavailable")
    at = T0
    for _ in range(3):
        engine.tick(now=at)
        at = at + timedelta(minutes=10)
    report = engine.tick(now=at)
    assert report.review_recommended == ("tree-1",)
    assert store.failure("tree-1") is not None
    # A provider outage never mutates or cancels anything durable.
    assert store.latest_projection("tree-1") is None


def test_unexpected_provider_exception_is_contained() -> None:
    engine, store, weather = scheduler()
    store.add(subscription())
    weather.failure = RuntimeError("socket closed")
    report = engine.tick(now=T0)
    assert len(report.failed) == 1
    assert report.failed[0].detail == "RuntimeError"
    assert store.failure("tree-1") is not None


def test_phenology_failure_is_recorded_as_failed() -> None:
    engine, store, _ = scheduler()
    store.add(subscription())
    bad_prior = prior()
    store._subscriptions["tree-1"] = replace(  # noqa: SLF001 - fixture state setup
        store._subscriptions["tree-1"],  # noqa: SLF001
        prior=replace(bad_prior, stage_gdd=STAGES[:-1]),
    )
    report = engine.tick(now=T0)
    assert len(report.failed) == 1
    assert report.failed[0].detail == "prior_invalid"


def test_tree_cap_leaves_remaining_trees_for_the_next_tick() -> None:
    engine, store, _ = scheduler(max_trees_per_tick=1)
    store.add(subscription("tree-1"))
    store.add(subscription("tree-2"))
    report = engine.tick(now=T0)
    assert [item.tree_id for item in report.processed] == ["tree-1"]
    assert report.skipped_cap == ("tree-2",)
    second = engine.tick(now=T0 + timedelta(minutes=1))
    assert [item.tree_id for item in second.processed] == ["tree-2"]


def test_per_tree_budget_breach_is_reported() -> None:
    clock = StepClock(T0, step=timedelta(minutes=6))
    engine, store, _ = scheduler(clock=clock)
    store.add(subscription())
    report = engine.tick()
    assert report.budget_breaches == ("tree-1",)
    assert report.processed[0].duration_ms > 300_000


def test_report_serializes_without_sensitive_fields() -> None:
    engine, store, _ = scheduler()
    store.add(subscription())
    report = engine.tick(now=T0)
    payload = report.as_dict()
    text = json.dumps(payload, sort_keys=True)
    assert "tree-1" in text
    assert "latitude" not in text.lower()
    assert "address" not in text.lower()
    assert payload["observations_new"] == 2
    assert serialized_tick(payload).startswith("{")
    assert run_tick_payload(engine)["trees_considered"] == 1


def test_serialized_tick_rejects_leaked_coordinates() -> None:
    with pytest.raises(SchedulerError, match="forbidden_field"):
        serialized_tick({"latitude": 52.0})
    with pytest.raises(SchedulerError, match="forbidden_field"):
        serialized_tick({"nested": [{"access_notes": "gate code"}]})


def test_worker_runs_a_bounded_number_of_ticks() -> None:
    engine, store, _ = scheduler()
    store.add(subscription())
    sleeps: list[float] = []
    worker = SchedulerWorker(
        scheduler=engine,
        clock=lambda: T0,
        sleeper=sleeps.append,
        interval=timedelta(seconds=60),
    )
    stats = worker.run(iterations=2)
    assert stats["iterations"] == 2
    assert stats["observations_new"] >= 2
    assert stats["stopped"] is False
    assert sleeps == [60.0]
    assert len(worker.reports) == 2


def test_worker_stop_is_cooperative() -> None:
    engine, store, _ = scheduler()
    store.add(subscription())
    worker = SchedulerWorker(
        scheduler=engine, clock=lambda: T0, sleeper=lambda _seconds: worker.stop()
    )
    stats = worker.run(iterations=5)
    assert stats["iterations"] == 1
    assert stats["stopped"] is True


def test_worker_rejects_a_short_interval() -> None:
    engine, _, _ = scheduler()
    with pytest.raises(SchedulerError, match="worker_interval_too_short"):
        SchedulerWorker(
            scheduler=engine,
            clock=lambda: T0,
            sleeper=lambda _seconds: None,
            interval=timedelta(seconds=1),
        )


def test_celery_settings_describe_the_beat_schedule() -> None:
    settings = CelerySettings(broker_url="redis://fixture-broker:6379/0", beat_seconds=900.0)
    schedule = settings.beat_schedule()
    assert schedule["harvestgrid-weather-poll"]["task"] == (
        "harvestgrid.forecasting.poll_weather_tick"
    )
    assert schedule["harvestgrid-weather-poll"]["schedule"] == 900.0
    assert settings.as_dict()["broker_url"].startswith("redis://")
    engine, _, _ = scheduler()
    with pytest.raises(SchedulerError, match="celery_broker_url_required"):
        build_celery_app(scheduler=engine, settings=CelerySettings(broker_url="  "))


@pytest.mark.skipif(importlib.util.find_spec("celery") is not None, reason="celery installed")
def test_celery_is_optional_and_fails_visibly() -> None:
    engine, _, _ = scheduler()
    with pytest.raises(WorkerBackendUnavailable, match="celery is not installed"):
        build_celery_app(
            scheduler=engine,
            settings=CelerySettings(broker_url="redis://fixture-broker:6379/0"),
        )


@pytest.mark.skipif(importlib.util.find_spec("rq") is not None, reason="rq installed")
def test_rq_is_optional_and_fails_visibly() -> None:
    engine, _, _ = scheduler()
    with pytest.raises(WorkerBackendUnavailable, match="rq is not installed"):
        build_rq_job(scheduler=engine, queue=object())


class RecordingQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[object, object, str]] = []

    def enqueue(self, function: object, argument: object, job_id: str) -> str:
        self.jobs.append((function, argument, job_id))
        return job_id


def test_rq_job_payload_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    fake_rq = types.ModuleType("rq")
    monkeypatch.setitem(sys.modules, "rq", fake_rq)
    engine, _, _ = scheduler()
    queue = RecordingQueue()
    job_id = build_rq_job(scheduler=engine, queue=queue)
    assert job_id == "harvestgrid-weather-poll"
    function, argument, explicit_id = queue.jobs[0]
    assert function is run_tick_payload
    assert argument is engine
    assert explicit_id == "harvestgrid-weather-poll"


def test_subscription_validation() -> None:
    with pytest.raises(SchedulerError, match="subscription_tree_id_required"):
        subscription(tree_id=" ")
    with pytest.raises(SchedulerError, match="subscription_prior_cultivar_mismatch"):
        TreeSubscription(
            tree_id="tree-1",
            organization_id="org-fixture",
            cultivar_id="other_plum",
            coarse_geohash=GEOHASH,
            prior=prior(),
        )
    with pytest.raises(SchedulerError, match="subscription_interval_out_of_range"):
        subscription(interval=timedelta(minutes=5))
    with pytest.raises(SchedulerError, match="subscription_last_polled_requires_timezone"):
        subscription(last_polled_at=datetime(2026, 7, 1, 6, 0))
    with pytest.raises(Exception, match="coarse_location_invalid"):
        TreeSubscription(
            tree_id="tree-1",
            organization_id="org-fixture",
            cultivar_id="european_plum",
            coarse_geohash="52.0067,4.3556",
            prior=prior(),
        )


def test_scheduler_configuration_validation() -> None:
    _, _, _ = scheduler()
    weather = FakeWeatherSource()
    store = InMemorySchedulerStore()
    for kwargs, match in (
        ({"lookback_days": 0}, "lookback_days_out_of_range"),
        ({"lookback_days": 400}, "lookback_days_out_of_range"),
        ({"forecast_days": 20}, "forecast_days_out_of_range"),
        ({"max_trees_per_tick": 0}, "max_trees_per_tick_must_be_positive"),
        ({"per_tree_budget": timedelta(0)}, "per_tree_budget_must_be_positive"),
    ):
        with pytest.raises(SchedulerError, match=match):
            WeatherIngestionScheduler(weather=weather, store=store, clock=lambda: T0, **kwargs)


def test_clock_must_be_timezone_aware() -> None:
    engine, store, _ = scheduler(clock=StepClock(datetime(2026, 7, 1, 6, 0)))
    store.add(subscription())
    with pytest.raises(SchedulerError, match="scheduler_clock_requires_timezone"):
        engine.tick()


def test_store_rejects_duplicate_subscriptions_and_unknown_trees() -> None:
    store = InMemorySchedulerStore()
    store.add(subscription())
    with pytest.raises(SchedulerError, match="subscription_already_registered"):
        store.add(subscription())
    with pytest.raises(SchedulerError, match="subscription_not_found"):
        store.mark_polled("tree-missing", T0)


def test_backoff_policy_is_exponential_and_capped() -> None:
    policy = BackoffPolicy(base=timedelta(minutes=1), maximum=timedelta(minutes=10))
    assert policy.delay_for(1) == timedelta(minutes=1)
    assert policy.delay_for(2) == timedelta(minutes=2)
    assert policy.delay_for(3) == timedelta(minutes=4)
    assert policy.delay_for(10) == timedelta(minutes=10)
    with pytest.raises(SchedulerError, match="backoff_attempts_must_be_positive"):
        policy.delay_for(0)


def test_is_due_respects_interval_and_Gates() -> None:
    engine, _, _ = scheduler()
    assert engine.is_due(subscription(), T0) is True
    assert engine.is_due(subscription(last_polled_at=T0), T0 + timedelta(hours=5)) is False
    assert engine.is_due(subscription(last_polled_at=T0), T0 + timedelta(hours=6)) is True
    assert engine.is_due(subscription(collection_approved=False), T0) is False
