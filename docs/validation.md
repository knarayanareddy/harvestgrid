# HarvestGrid validation record

Recorded 2026-10-24. ROI, Ring webhook, weather, feature-extraction, and coordinator-dashboard slices were validated on 2026-10-24 under the same source-built CPython 3.12.12; the interpreter still has no `_ssl` module, so the dependency audit was not rerun. The dashboard was additionally validated with Node 22, pnpm 10.15.0, TypeScript 5.9.3, Vite 8.3.5, and Vitest 5.0.5. Earlier slices were validated under source-built CPython 3.12.12 with zlib, SQLite, and OpenSSL support, including the dependency audit. The `FR-HG-001` onboarding revalidation ran on 2026-10-23 under source-built CPython 3.12.12 with zlib 1.3.1 and SQLite 3.38.2; that sandbox interpreter has no OpenSSL module, so the network audit step was not rerun for this slice. Python 3.11 was not substituted in either run.

## Passing checks

| Check | Result |
| --- | --- |
| Ruff lint + format check | Passed |
| Strict mypy | Passed; 11 source files |
| Unit tests | 153/153 passed |
| Branch-aware coverage | 94.16%; required gate 90% retained; domain 100%, features_v1 100%, webhooks 96%, weather 94%, vision worker 93%, webhook server 76% (the stdlib request-handler shell is exercised through its real socket path) |
| Schema/contract tests | 46/46 passed; forecast schema copy byte-identical; tree-registration, camera-view, and donation-receipt projections validated against their JSON Schemas; ranking weights compared across Python and TypeScript |
| Coordinator dashboard checks | `pnpm typecheck` clean, 53/53 tests passed across 4 files, production build succeeded (25 modules, ~242 kB JS / 76 kB gzipped) |
| Operational/security boundary test | 1/1 passed |
| Third-party dependency audit | Not rerun this slice (sandbox interpreter lacks OpenSSL). Last recorded result: no known vulnerabilities; editable local package skipped |
| Source distribution + wheel | Both built successfully |
| Fixture walking path | Coarse-location owner/organization/tree/consent registration, approval/assignment/revocation, minimized export, short-lived access-token revocation, five-stage fixture deletion, plus a separate coordinator-verified outcome, non-sensitive receipt, forecast evaluation, closure, and a two-example synthetic evaluation aggregate with an ineligible model data gate |
| **Total tests** | **200/200 passed** (153 unit + 46 contract + 1 security), plus 53 dashboard tests |

The forecast remains `experimental`, `seasonal_baseline`, human-readiness-review-required, and visible-only. Internal-ripeness and food-safety inference remain false.

The in-memory operations slice requires owner and coordinator readiness approvals before deterministic proposals, requires coordinator assignment approval, supports idempotent multi-volunteer confirmation, and permits restricted-location return only to a confirmed assignee inside the explicit reveal window. Owner revocation cancels assignments and denies subsequent access. No notification is sent and no real address is used.

The metadata-only privacy service binds location grants to the existing confirmed-assignment workflow and caps them at the earlier of two hours or the assignment reveal expiry. Cancellation/revocation and token expiry invalidate access; owner export requires recent authentication and omits exact addresses, access notes, raw media, and tokens. Consent revocation, camera unlink, safeguarded tree deletion, and organization offboarding produce scoped local deletion jobs. Five stage receipts verify removal of raw-media, derived, forecast, and access/analytics artifact metadata; tests confirm camera-scoped deletion leaves another camera's artifacts intact and full deletion removes all target artifacts. These are fixture-store checks, not restricted-cloud or Ring deletion evidence.

A separate confirmed-workflow branch records 10.0 kg gross, 8.5 kg net, 8.0 kg accepted, and 0.5 kg rejected with a coarse spoilage reason and fixture scale/evidence metadata. Coordinator verification produces one deterministic receipt and a single-outcome evaluation; duplicate entry/verification/closure does not create another version or receipt. The receipt export excludes volunteer, address/access, rejection, scale, and evidence-hash fields. The canonical forecast has no weight estimate, so the evaluation reports `forecast_weight_unavailable` and a visible-count absolute error of 2 rather than inventing a weight error. Automatic model promotion is false. Tests also cover non-null weight errors, undefined zero-weight percentage error, invalid weight arithmetic/evidence, authorization, stale versions, and immutable post-outcome state. They exposed and now regress the forecast validator's estimate/lower/upper unpacking order.

The reviewed evaluation-set fixture requires an attestation and at least two coordinator-verified examples, detects replay conflicts, rejects mixed model versions, computes start/end MAE, interval coverage/width, visible-count MAE, and weight MAE/MAPE where defined, and exports aggregates without tree/site/recipient/location identifiers. The separate data gate requires 100 tree-season sequences, 20 sites, and held-out site/season splits; the walking fixture remains ineligible and automatic promotion is always false.

## FR-HG-001 registration slice

`packages/domain/harvestgrid_domain/onboarding.py` adds typed registration values, a compare-and-set store port, a cultivar-catalog port, and an application service for organizations, owners/members, trees, cultivars, coarse locations, and explicit operational consents. `onboarding_adapters.py` supplies the local fixture adapters: an in-memory metadata store and a JSON cultivar catalog that reads `content/cultivars/` without inventing priors.

Registration is deterministic and idempotent. Identical retries return the stored result with `duplicate=True`; materially different retries raise a conflict code. Trees are keyed by a coarse geohash cell of 4-5 characters; exact coordinates, higher precision, and comma/latitude-longitude strings are rejected, so no exact location can enter the general record. Regions are optional coarse labels.

Camera-collection and organizational-operations consent are both mandatory and separately recorded; crop-only training consent is optional, must be explicit, and is never implied by collection. Records must be recent (within 400 days), timezone-aware, accepted by the tree owner, and matched to the current versioned policy. Collection remains blocked after registration because no camera view can be approved in this slice; the snapshot reports `awaiting_camera_view_approval` plus the reasons. The canonical cultivar content is a draft, so registered trees report `forecast_experiment_only`, and human readiness review remains required.

Contract tests validate the serialized tree projection against a JSON Schema that forbids address, coordinate, and raw-media fields; they also pin the error-code catalogue, consent policy versions, and the canonical cultivar gate (100 tree-season sequences, 20 sites, held-out site/season splits). Unit tests cover happy paths plus missing/expired/wrong-actor/stale-policy consent, authentication, organization suspension, wrong-organization membership, cohort mismatch, unsupported cultivar, species mismatch, bloom-date bounds, geohash precision, conflicting replays, and store compare-and-set failures.

## ROI, Ring webhook, weather, and feature-extraction slices

`packages/domain/harvestgrid_domain/roi.py` makes a camera view an explicit, versioned artifact. A draft becomes `approved` only after owner approval *and* coordinator approval at the expected versions; rejection, stale versions, out-of-range geometry, a self-intersecting polygon, an oversized ROI, or a mask that covers the whole ROI keeps collection blocked with a stable error code. Twenty `ROI_ERROR_CODES` cover the failure surface. Masks are subtracted from the ROI geometry, not applied after sampling, so `allowed_cells` never yields a neighbour-property, sidewalk, or street cell. `view_document()` serializes an approved view that the camera-view JSON Schema validates; the schema forbids exact addresses, coordinates, raw-media URIs, and person/vehicle detection, and the tests confirm each of those rejections.

`services/ring-adapter/` implements the webhook ingress contract. Requests are checked in a fixed order: method/path, content type, a 64 KB cap, HMAC-SHA256 over `sha256(timestamp + "." + raw_body)` with a ±5-minute window, JSON parsing, a token bucket (30 burst, 0.5/s), event-id deduplication, then payload-hash deduplication. Valid events enqueue a media job and acknowledge inside the five-second budget; invalid signatures return 401 without creating a job, replays return 409, and rate limits return 429 with `Retry-After`. Forbidden keys (`person`, `license_plate`, `address`, and peers) are rejected outright, so surveillance metadata cannot enter the pipeline even by accident. The stdlib HTTP shell exposes `/healthz` and one POST path, answers unknown paths with 404, wrong methods with 405 plus `Allow`, and refuses to start without a `RING_WEBHOOK_SECRET` of at least 32 characters. All tests use fixture secrets and a fake clock; no Ring device or credential was involved.

`packages/weather-client/harvestgrid_weather/open_meteo.py` accepts geohash precision 4-5 only, truncates coordinates to one decimal place, and records `exact_coordinates_included: false` in provenance. It bounds ranges to 366 days, limits forecasts to the provider's 16-day horizon, keeps missing daily values as `None` instead of zero, computes growing-degree-days from a 5 °C base, retries transient 5xx responses with bounded backoff, surfaces 429 with `Retry-After`, and caches responses for six hours. The transport is injected, so every test runs offline against synthetic provider JSON.

`packages/vision/harvestgrid_vision/` produces the 16-column `features_v1` row from an approved, already-masked view. Frames failing registration, luminance, contrast, ROI-coverage, or composite quality gates are rejected with reason codes and emit no feature row (`feature_row()` returns `None`), so a bad frame cannot look like a zero-fruit count. Missing values are `-1.0`. Detections are 4-connected components above an area floor; counts carry lower/upper bounds and area percentiles. `HeuristicClusterDetector` is labelled `heuristic_fixture`, `OnnxDetector` raises `InferenceBackendUnavailable` rather than pretending to run a model, and every observation hard-codes `internal_ripeness_inferred`, `food_safety_inferred`, `disease_inferred`, and `raw_media_included` to `false`.

## Coordinator dashboard

`apps/web` is a React 19 + Vite + TypeScript single-page coordinator dashboard. Its `CoordinatorApi` port exposes only board loading, assignment approval, and receipt recording — there is no method that could return an address, raw media, or Ring data. The default `FixtureCoordinatorApi` renders a persistent `FIXTURE DATA` banner and a live-mode switch that is opt-in through `VITE_DEMO_MODE`.

Matching re-implements the hard filters and the five SPEC §9.2 weights in the browser so the coordinator sees the same eligibility result before submitting. A contract test parses both `engine.py` and `matching.ts` and fails if the weights diverge. Ineligible volunteers receive `score: null`, never a low score; the deterministic order is descending score, then the server tie key, then volunteer id. Approval stays disabled until owner approval is current, coordinator readiness is confirmed, the glean is in a matchable state, and at least one eligible volunteer is selected.

Receipt entry converts kilogram strings to integer grams before comparing them, so float drift cannot break `accepted + rejected == net`. It rejects over-precise values, weights above 10,000 kg, rejections without a reason, and manual weights entered without coordinator authority and a note. The export projection is checked against `packages/contracts/donation-receipt.schema.json` and contains no address, access note, volunteer, rejection, scale, or evidence field. The access-reveal preview denies non-volunteers, cancelled assignments, inactive consent, missing owner approval, and times outside the reveal window, and caps any grant at two hours or the assignment expiry.

## Reproduce

```bash
make lint typecheck test-unit test-contract test-security build dev
cd apps/web && corepack pnpm install && corepack pnpm typecheck && corepack pnpm test && corepack pnpm build
```

`make test-web` runs the three dashboard checks. The dev server binds `0.0.0.0:3000` and proxies `/v1` to `API_BASE_URL_INTERNAL` (default `http://127.0.0.1:8080`); fixture mode needs no backend.

Use a Python 3.12 runtime with zlib, SQLite, and SSL/TLS so the normal coverage and audit commands are self-contained. Do not skip the audit or run project tests under 3.11.

## Not evidenced

No Ring device/API/simulator, real observation, residential media, real ROI/masking, live weather request, CV model, cultivar prior, operational forecast, model-quality result, actual invitation/notification, durable coordinator workflow, production location authorization, real weighed glean/donation, reviewed evaluation-set manifest, model training/promotion, production/cloud verified deletion, participant/pilot, performance, deployment, or release behavior was exercised. The webhook service was only exercised with fixture secrets and synthetic bodies; the weather adapter only with recorded-shape synthetic JSON; the vision worker only with synthetic pixel grids and no ONNX artifact; and the dashboard only with fixture data in jsdom and a local dev server. Three-second-to-fifteen-minute latency requirements, browser E2E against a live API, and mobile/large-organization performance were not measured. Registration is an in-memory fixture behind typed ports: no FastAPI endpoint, durable database, Cognito authentication, or real owner/organization onboarding ran, and no camera view was approved. The verified weights, receipt, and two-example evaluation aggregate are synthetic in-memory values, not a real held-out evaluation set or model-quality claim. No operational forecast or trained model artifact exists.
