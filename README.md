# HarvestGrid

**Track:** Ring
**Status:** registration, forecast, matching, operations, verified outcome, synthetic evaluation, privacy lifecycle, ROI masking, Ring webhook ingress, weather adapter, feature extraction, and coordinator dashboard validated against fixtures; live Ring/media/model operations not implemented
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
corepack pnpm dev          # http://localhost:3000, fixture data by default
```

`make test-web` runs the dashboard typecheck, tests, and production build. Unit tests enforce at least 90% branch-aware coverage across the domain, matching, vision, weather, and Ring-adapter packages. `make test-security` runs operational-boundary tests and `pip-audit --skip-editable`; its audit runner needs working SSL/TLS.

Ring device integration, live media processing, model inference, the backend API for live dashboard mode, operational forecasts, persistence, notifications, infrastructure, E2E, and deployment targets intentionally fail or stay in fixture mode while absent.

## Workspace map

| Path | Purpose |
| --- | --- |
| [`SPEC.md`](./SPEC.md) | Canonical requirements and vertical slices |
| [`schemas/harvest-window.schema.json`](./schemas/harvest-window.schema.json) | Canonical forecast contract |
| [`packages/domain/harvestgrid_domain/`](./packages/domain/harvestgrid_domain/) | Owner/organization/tree/consent registration, forecast checks, assignments/location policy, verified weights, non-sensitive receipts, outcome evaluation, reviewed evaluation aggregates, privacy lifecycle, and fixtures |
| [`packages/matching/harvestgrid_domain_matching/`](./packages/matching/harvestgrid_domain_matching/) | Hard eligibility filters, scoring, and deterministic ties |
| [`packages/vision/harvestgrid_vision/`](./packages/vision/harvestgrid_vision/) | Masked-ROI feature extraction, `features_v1` contract, heuristic and ONNX detector boundaries |
| [`packages/weather-client/harvestgrid_weather/`](./packages/weather-client/harvestgrid_weather/) | Coarse-geohash Open-Meteo adapter with recorded-shape fixtures |
| [`services/ring-adapter/`](./services/ring-adapter/) | HMAC-verified Ring webhook ingress, deduplication, media-job queue, stdlib HTTP shell |
| [`apps/web/`](./apps/web/) | React coordinator dashboard: matching board, assignment approval, receipt logging |
| [`packages/contracts/`](./packages/contracts/) | Byte-identical forecast schema copy plus tree-registration, camera-view, and donation-receipt contracts |
| [`ui/`](./ui/) | Design tokens, wireframes, and static wireframe reference |
| [`content/cultivars/`](./content/cultivars/) | Placeholder boundary; no cultivar priors supplied |
| [`datasets/`](./datasets/) and [`models/`](./models/) | Explicit absent-data/model boundaries |
| [`tests/`](./tests/) | Unit, schema-contract, and operational-boundary tests |
| [`docs/`](./docs/) | Preflight, architecture, data inventory, dependencies, and validation |

## External blockers

A documented Ring integration and real capabilities, real device credentials, durable registration persistence, the backend API that the dashboard's live mode expects, residential-media consent, live weather and CV services, licensed datasets and cultivar priors, model artifacts/evaluation, coordinator/participant reviews, and release evidence remain blocked or unimplemented. The webhook server runs with a fixture secret and a synthetic body; the weather adapter's transport is injected and never called live in tests; `OnnxDetector` raises rather than fabricating inference; and the dashboard labels every screen `FIXTURE DATA`. Registration runs only against in-memory fixture adapters with fictional identifiers and coarse geohash cells. The forecast fixture is experimental, seasonal-baseline-only, and human-review-required. Synthetic operations include a closed coordinator-verified outcome and a two-example evaluation aggregate, but create no real invitation, residential disclosure, durable workflow, donation, held-out evaluation set, or completed glean. A metadata-only privacy fixture adds minimized owner export, short-lived assignment access tokens, consent/camera revocation, staged artifact-deletion receipts, and organization offboarding. It stores no residential media and does not execute cloud/Ring deletion.

Maintain [`SPEC_TRACEABILITY.md`](./SPEC_TRACEABILITY.md) and [`SPEC_DEVIATIONS.md`](./SPEC_DEVIATIONS.md). Never place residential media, private datasets, or model binaries in Git.
