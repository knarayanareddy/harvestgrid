# HarvestGrid

**Track:** Ring
**Status:** forecast, matching, operations, verified outcome, synthetic evaluation, and privacy-lifecycle slices validated locally; Ring/media/live operations not implemented
**Agent entry point:** [`AGENTS.md`](./AGENTS.md)

HarvestGrid is intended to support privacy-preserving, coordinator-reviewed backyard harvest logistics. Visual estimates cannot establish internal ripeness or food safety.

## Local foundation

Requirements: Python 3.12, `uv`, and `make`. Dependencies are locked in `uv.lock`; domain and matching runtime code have no third-party runtime dependency.

```bash
make setup                 # uv sync --frozen and safe .env.local
make dev                   # fictional forecast, operations, outcome, and evaluation fixtures
make lint typecheck
make test-unit test-contract test-security
make build
```

Unit tests enforce at least 90% branch-aware coverage across domain and matching packages. `make test-security` runs operational-boundary tests and `pip-audit --skip-editable`; its audit runner needs working SSL/TLS.

Ring, media processing, operational forecasts, persistence, notifications, infrastructure, E2E, and deployment targets intentionally fail while absent.

## Workspace map

| Path | Purpose |
| --- | --- |
| [`SPEC.md`](./SPEC.md) | Canonical requirements and vertical slices |
| [`schemas/harvest-window.schema.json`](./schemas/harvest-window.schema.json) | Canonical forecast contract |
| [`packages/domain/harvestgrid_domain/`](./packages/domain/harvestgrid_domain/) | Forecast checks, assignments/location policy, verified weights, non-sensitive receipts, outcome evaluation, reviewed evaluation aggregates, privacy lifecycle, and fixtures |
| [`packages/matching/harvestgrid_domain_matching/`](./packages/matching/harvestgrid_domain_matching/) | Hard eligibility filters, scoring, and deterministic ties |
| [`packages/contracts/`](./packages/contracts/) | Byte-identical schema copy used by contract tests |
| [`content/cultivars/`](./content/cultivars/) | Placeholder boundary; no cultivar priors supplied |
| [`datasets/`](./datasets/) and [`models/`](./models/) | Explicit absent-data/model boundaries |
| [`tests/`](./tests/) | Unit, schema-contract, and operational-boundary tests |
| [`docs/`](./docs/) | Preflight, architecture, data inventory, dependencies, and validation |

## External blockers

A documented Ring integration and real capabilities, residential-media consent, weather/CV services, licensed datasets and cultivar priors, model artifacts/evaluation, coordinator/participant reviews, and release evidence remain blocked or unimplemented. The forecast fixture is experimental, seasonal-baseline-only, and human-review-required. Synthetic operations include a closed coordinator-verified outcome and a two-example evaluation aggregate, but create no real invitation, residential disclosure, durable workflow, donation, held-out evaluation set, or completed glean. A metadata-only privacy fixture adds minimized owner export, short-lived assignment access tokens, consent/camera revocation, staged artifact-deletion receipts, and organization offboarding. It stores no residential media and does not execute cloud/Ring deletion.

Maintain [`SPEC_TRACEABILITY.md`](./SPEC_TRACEABILITY.md) and [`SPEC_DEVIATIONS.md`](./SPEC_DEVIATIONS.md). Never place residential media, private datasets, or model binaries in Git.
