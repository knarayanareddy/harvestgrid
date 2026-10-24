# HarvestGrid

**Track:** Ring
**Status:** registration, forecast, matching, operations, verified outcome, synthetic evaluation, privacy lifecycle, ROI masking, Ring webhook ingress, weather adapter, scheduled ripening curves, SMS dispatch, ONNX inference runtime, feature extraction, coordinator dashboard, a local docker compose stack, and a standalone interactive demo validated against fixtures; live Ring/media/model/provider operations, persistence, and container deploys not implemented
**Agent entry point:** [`AGENTS.md`](./AGENTS.md)

HarvestGrid is intended to support privacy-preserving, coordinator-reviewed backyard harvest logistics. Visual estimates cannot establish internal ripeness or food safety.

## Local foundation

Requirements: Python 3.12, `uv`, and `make` for the Python slices; Node 22 with `corepack` for the coordinator dashboard. Dependencies are locked in `uv.lock` and `apps/web/pnpm-lock.yaml`; domain, matching, vision, and weather runtime code have no third-party runtime dependency.

```bash
make setup                 # uv sync --frozen and safe .env.local
make dev                   # fictional forecast, operations, outcome, and evaluation fixtures
make lint typecheck
make test-unit test-contract test-security
make build

cd apps/web
corepack pnpm install
corepack pnpm typecheck && corepack pnpm test && corepack pnpm build
corepack pnpm dev          # http://localhost:5173/harvestgrid/, fixture data by default
```

### One command for the whole stack

```bash
cp .env.example .env       # optional; every value has a fixture-safe default
docker compose up --build  # Postgres 16 + PostGIS :5432, Redis 7 :6379, API :8000, dashboard :5173
```

The dashboard is at <http://localhost:5173> and the API at <http://localhost:8000/docs>. PostGIS and
Redis are provisioned, healthy, and passed to the API as `DATABASE_URL`/`REDIS_URL`, but the API
still stores state in process — no migration and no Redis client exist yet, and the compose file
says so. See [`docs/local-stack.md`](./docs/local-stack.md) for the env wiring, healthchecks, and
troubleshooting.

### Interactive demo

`VITE_DEMO_MODE=true` renders a standalone, backend-free demo with an in-browser fixture store:
register a tree and grant camera consent, drag the privacy ROI and mask boundary, replay a signed
Ring webhook, move the ripening slider, and dispatch volunteers.

* Live page: <https://knarayanareddy.github.io/harvestgrid/> (deployed by
  [`.github/workflows/pages.yml`](./.github/workflows/pages.yml) from every `main` push touching
  `apps/web/`)
* Local: `make web-dev` → <http://localhost:5173/harvestgrid/>
* Exactly what Pages serves: `make web-build-demo`
* Recorded walkthrough: `make demo-record` → `docs/media/harvestgrid-demo.webm` plus a cue sheet; the
  scene plan, the recorder, and the DOM tests live together, so selectors cannot drift. The
  coordinator console embeds it in its **Demo Video** tab beside Map View, Yield Forecasts, and
  Gleaning Dispatch. See [`docs/demo.md`](./docs/demo.md).

`make test-web` runs the dashboard typecheck, tests, and production build. `uv run pytest tests/unit` covers the forecasting scheduler, SMS dispatch, and ONNX runtime; see [`docs/forecasting-scheduler.md`](./docs/forecasting-scheduler.md) and [`docs/notifications.md`](./docs/notifications.md). Unit tests enforce at least 90% branch-aware coverage across the domain, matching, vision, weather, and Ring-adapter packages. `make test-security` runs operational-boundary tests and `pip-audit --skip-editable`; its audit runner needs working SSL/TLS.

Ring device integration, live media processing, model inference, the backend API for live dashboard mode, operational forecasts, persistence (compose provisions Postgres 16 + PostGIS and Redis 7, but the API opens neither), notifications, cloud infrastructure, E2E, and deployment targets intentionally fail or stay in fixture mode while absent.

## Workspace map

| Path | Purpose |
| --- | --- |
| [`SPEC.md`](./SPEC.md) | Canonical requirements and vertical slices |
| [`schemas/harvest-window.schema.json`](./schemas/harvest-window.schema.json) | Canonical forecast contract |
| [`packages/domain/harvestgrid_domain/`](./packages/domain/harvestgrid_domain/) | Owner/organization/tree/consent registration, forecast checks, assignments/location policy, verified weights, non-sensitive receipts, outcome evaluation, reviewed evaluation aggregates, privacy lifecycle, and fixtures |
| [`packages/matching/harvestgrid_domain_matching/`](./packages/matching/harvestgrid_domain_matching/) | Hard eligibility filters, scoring, and deterministic ties |
| [`packages/vision/harvestgrid_vision/`](./packages/vision/harvestgrid_vision/) | Masked-ROI feature extraction, `features_v1` contract, verified-artifact ONNX Runtime detector |
| [`packages/vision/weights/`](./packages/vision/weights/) | Weight manifest and policy; no ONNX binary is committed, one real reference checkpoint is digest-pinned |
| [`packages/weather-client/harvestgrid_weather/`](./packages/weather-client/harvestgrid_weather/) | Coarse-geohash Open-Meteo adapter with recorded-shape fixtures |
| [`services/ring-adapter/`](./services/ring-adapter/) | HMAC-verified Ring webhook ingress, deduplication, media-job queue, stdlib HTTP shell |
| [`services/forecasting/`](./services/forecasting/) | Scheduled coarse-weather ingestion and temperature-driven ripening curves |
| [`services/notifications/`](./services/notifications/) | Coordinator-approved pickup alerts over Twilio or Amazon SNS |
| [`services/api/`](./services/api/) | FastAPI coordination API: scheduler trigger, tree projection, volunteer dispatch, problem-document errors |
| [`packages/testkit/`](./packages/testkit/) | Dependency-light ASGI driver used by the API tests |
| [`tools/fetch_weights.py`](./tools/fetch_weights.py) | Verifies and installs a manifest-declared ONNX artifact (download, archive, or local) |
| [`tools/verify_vision.py`](./tools/verify_vision.py) | Runs an installed artifact on a real photo and proves ROI masking filters boxes |
| [`docs/api.md`](./docs/api.md) | API routes, idempotency, error codes, fixture tokens |
| [`docs/local-stack.md`](./docs/local-stack.md) | docker compose topology, env wiring, healthchecks, what is fixture |
| [`docs/demo.md`](./docs/demo.md) | Interactive demo, recorder usage, GitHub Pages deployment |
| [`apps/web/`](./apps/web/) | React coordinator dashboard (Map View, Yield Forecasts, Gleaning Dispatch, Demo Video) plus the standalone `VITE_DEMO_MODE` demo |
| [`docker-compose.yml`](./docker-compose.yml) | Local stack: Postgres 16 + PostGIS, Redis 7, FastAPI API, dashboard; see [`docs/local-stack.md`](./docs/local-stack.md) |
| [`scripts/record-demo.ts`](./scripts/record-demo.ts) | Playwright recorder that walks the demo scene plan and writes `docs/media/harvestgrid-demo.webm` |
| [`packages/contracts/`](./packages/contracts/) | Byte-identical forecast schema copy plus tree-registration, camera-view, and donation-receipt contracts |
| [`ui/`](./ui/) | Design tokens, wireframes, and static wireframe reference |
| [`content/cultivars/`](./content/cultivars/) | Placeholder boundary; no cultivar priors supplied |
| [`datasets/`](./datasets/) and [`models/`](./models/) | Explicit absent-data/model boundaries |
| [`tests/`](./tests/) | Unit, schema-contract, and operational-boundary tests |
| [`docs/`](./docs/) | Preflight, architecture, data inventory, dependencies, and validation |

## External blockers

A documented Ring integration and real capabilities, real device credentials, durable registration persistence, the backend API that the dashboard's live mode expects, residential-media consent, live weather and CV services, a broker for Celery/RQ, live Twilio/SNS credentials, licensed datasets and cultivar priors, trained model artifacts/evaluation, coordinator/participant reviews, and release evidence remain blocked or unimplemented. The forecasting scheduler and SMS transports run only against fixtures and injected fakes. `packages/vision/weights/manifest.json` keeps the plum detector `blocked` for lack of a consented dataset, and `tools/fetch_weights.py` refuses to install anything whose digest or license was not verified; the one `ready` artifact is a real but non-operational fruit-quality reference checkpoint (`docs/models/fruit-quality-yolov8n-320-v1.md`). The coordination API authenticates with fixture bearer tokens and runs the scheduler inline; no broker, telephony account, or identity provider is configured. The webhook server runs with a fixture secret and a synthetic body; the weather adapter's transport is injected and never called live in tests; the ONNX detector verifies a SHA-256 artifact before inference and the weight manifest records why no fruit checkpoint exists; and the dashboard labels every screen `FIXTURE DATA`. Registration runs only against in-memory fixture adapters with fictional identifiers and coarse geohash cells. The forecast fixture is experimental, seasonal-baseline-only, and human-review-required. Synthetic operations include a closed coordinator-verified outcome and a two-example evaluation aggregate, but create no real invitation, residential disclosure, durable workflow, donation, held-out evaluation set, or completed glean. A metadata-only privacy fixture adds minimized owner export, short-lived assignment access tokens, consent/camera revocation, staged artifact-deletion receipts, and organization offboarding. It stores no residential media and does not execute cloud/Ring deletion.

Maintain [`SPEC_TRACEABILITY.md`](./SPEC_TRACEABILITY.md) and [`SPEC_DEVIATIONS.md`](./SPEC_DEVIATIONS.md). Never place residential media, private datasets, or model binaries in Git.
