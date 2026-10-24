# Weather ingestion scheduler and ripening curves

`services/forecasting/` polls coarse-location weather on a schedule and keeps a
temperature-driven ripening projection current for every tree whose collection is approved.

## Execution model

| Layer | Module | Responsibility |
| --- | --- | --- |
| Trigger | Celery beat / RQ / cron | Fires one tick every 15 minutes (`CelerySettings.beat_seconds = 900`) |
| Core | `harvestgrid_forecasting.scheduler.WeatherIngestionScheduler.tick` | Selects due trees, fetches weather, deduplicates, recomputes curves, reports |
| Curve | `harvestgrid_forecasting.ripening.project_ripening` | Pure GDD → stage → harvest-window function |
| State | `SchedulerStore` port | `subscriptions`, `record_observation`, `latest_projection`, `failure` |
| Adapters | `InMemorySchedulerStore` | Labelled fixture store used by tests |
| Workers | `SchedulerWorker`, `build_celery_app`, `build_rq_job` | Local loop and broker-backed task factories |

`tick()` is deterministic and does no I/O of its own beyond the injected weather source and
store, so every behaviour below is unit-tested without a broker. Celery and RQ are optional
dependencies: when they are absent the factories raise `WorkerBackendUnavailable` rather
than leaving an operator to believe a schedule exists.

## Per-tree tick

1. Skip trees whose camera view or operations consent is not current (`skipped_gated`).
2. Skip trees inside their poll interval, or inside a failure backoff (`skipped_backoff`).
3. Cap the batch at `max_trees_per_tick`; leftovers are reported as `skipped_cap`.
4. Fetch historical (30-day lookback) and forecast (10 days) weather for the **coarse
   geohash** only.
5. Record observations keyed by provider payload digest; repeats increment
   `observations_duplicate` and create nothing.
6. Recompute the projection. An unchanged fingerprint leaves the version alone; a changed
   one writes `version + 1` and emits a `RipeningAdjustment`.
7. Clear any failure state, mark the tree polled, and measure per-tree duration.

## Failure behaviour

| Situation | Result |
| --- | --- |
| Provider 5xx / timeout / rate limit (`WeatherError`) | `failed` outcome, exponential backoff `5m, 10m, 20m …` capped at 6 hours |
| Three consecutive failures | Tree appears in `review_recommended` for coordinator review |
| Any unexpected exception | Contained per tree; the tick continues with the next tree |
| Slow tree (> 5 minutes) | Recorded in `budget_breaches` (`NFR-HG-002`) |

A provider outage never cancels a glean, mutates an approved projection, or marks produce
unripe — it only delays ingestion (`NFR-HG-007`).

## Ripening model

`PhenologyPrior` supplies cultivar GDD thresholds; `project_ripening` accumulates
`max(0, mean_daily_temperature − base_temperature)` over observed days and maps the total to
a stage. The remaining GDD to `harvest_ripe` is divided by the forecast daily rate to project
the window; the spread widens with the horizon, with missing days, and beyond forecast
coverage.

Honesty rules:

- A prior whose `review_status` is not `approved` always yields `experimental: true`,
  `confidence: "low"`, and reason `prior_not_approved`.
- Fewer than 14 observed days is experimental regardless of the prior.
- Missing temperatures are counted, never filled with zeros.
- `internal_ripeness_inferred` and `food_safety_inferred` are always `false`; the projection
  is a phenology estimate from air temperature, not an assessment of the fruit.
- Only geohash precision 4–5 is accepted, so no exact coordinate can enter the model.

## Configuration

| Setting | Default | Notes |
| --- | --- | --- |
| `DEFAULT_POLL_INTERVAL` | 6 h | Per-tree override allowed between 1 h and 7 days |
| `DEFAULT_LOOKBACK_DAYS` | 30 | Bounded to 366 |
| `DEFAULT_FORECAST_DAYS` | 10 | Bounded to the provider's 16-day horizon |
| `MAX_TREES_PER_TICK` | 50 | Bounds a single task's runtime |
| `PER_TREE_BUDGET` | 5 min | Breach is reported, not silently retried |
| `BackoffPolicy` | 5 min → 6 h | Exponential, capped |

## Evidence limits

No live Open-Meteo request runs in the default suite: the weather adapter's transport is
injected and tests use recorded-shape synthetic JSON. No Celery or RQ broker was started, so
the broker-backed path is covered only for its configuration payload and its
fail-visibly behaviour. No cultivar prior has been reviewed, so every projection produced
today is `experimental` by construction.
