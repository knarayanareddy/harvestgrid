# HarvestGrid validation record

Recorded 2026-10-24. The coordination API, verified reference checkpoint, and React revalidation
were added on 2026-10-24 under the same source-built CPython 3.12.12 (zlib 1.3.1, SQLite 3.38.2,
no `_ssl`), so `starlette.testclient` cannot start there; the API tests use a purpose-built ASGI
driver in `packages/testkit/` instead. `pillow` 12.0.0 joined the dev dependencies to decode
verification photographs. The forecasting scheduler, SMS dispatch, and ONNX runtime slices were validated on 2026-10-24 under the same source-built CPython 3.12.12 with zlib 1.3.1 and SQLite 3.38.2, adding `onnxruntime` 1.23.2, `onnx` 1.19.1, and `numpy` 2.3.4 as test-only dev dependencies. The interpreter still has no `_ssl` module, so the dependency audit was not rerun. ROI, Ring webhook, weather, feature-extraction, and coordinator-dashboard slices were validated on 2026-10-24 under the same source-built CPython 3.12.12; the interpreter still has no `_ssl` module, so the dependency audit was not rerun. The dashboard was additionally validated with Node 22, pnpm 10.15.0, TypeScript 5.9.3, Vite 8.3.5, and Vitest 5.0.5. Earlier slices were validated under source-built CPython 3.12.12 with zlib, SQLite, and OpenSSL support, including the dependency audit. The `FR-HG-001` onboarding revalidation ran on 2026-10-23 under source-built CPython 3.12.12 with zlib 1.3.1 and SQLite 3.38.2; that sandbox interpreter has no OpenSSL module, so the network audit step was not rerun for this slice. Python 3.11 was not substituted in either run.

## Passing checks

| Check | Result |
| --- | --- |
| Ruff lint + format check | Passed |
| Strict mypy (`mypy .`) | Passed; 48 source files, tests and tools excluded from strict mode |
| Unit tests | 388/388 passed, 1 skipped (skip is the opt-in live-network test) |
| Branch-aware coverage | 93.97%; required gate 90% retained; coordination API 92%, forecasting 93–94%, notifications 94–99%, ONNX runtime 94%, weights 87%, webhooks 96%, weather 94% |
| Schema/contract tests | 70/70 passed; forecast schema copy byte-identical; tree-registration, camera-view, donation-receipt, ripening-projection, and notification-dispatch contracts validated; ranking weights compared across Python and TypeScript |
| Coordinator dashboard checks | `pnpm typecheck` clean, 53/53 tests passed across 4 files, production build succeeded (25 modules, ~242 kB JS / 76 kB gzipped) |
| Operational/security boundary test | 1/1 passed |
| Third-party dependency audit | Not rerun this slice (sandbox interpreter lacks OpenSSL). Last recorded result: no known vulnerabilities; editable local package skipped |
| Source distribution + wheel | Both built successfully |
| Fixture walking path | Coarse-location owner/organization/tree/consent registration, approval/assignment/revocation, minimized export, short-lived access-token revocation, five-stage fixture deletion, plus a separate coordinator-verified outcome, non-sensitive receipt, forecast evaluation, closure, and a two-example synthetic evaluation aggregate with an ineligible model data gate |
| **Total tests** | **459/459 passed** (388 unit + 70 contract + 1 security), 1 conditional skip, plus 53 dashboard tests |

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

## Weather ingestion scheduler and ripening curves

`services/forecasting/` adds a scheduled coarse-weather ingestion worker and a temperature-driven ripening model.

`WeatherIngestionScheduler.tick()` is a deterministic function of clock, weather source, and state store. It skips trees whose camera view or operations consent is not current, respects per-tree poll intervals (1 hour to 7 days, default 6 hours), applies exponential failure backoff (5 minutes doubling to a 6-hour cap), caps a batch at 50 trees, and reports leftovers instead of silently dropping them. Observations are keyed by provider payload digest, so a repeated tick with identical bytes records `observations_duplicate` and writes nothing; a projection is only re-versioned when its fingerprint changes, which is what emits a `RipeningAdjustment` for the coordinator.

Failure handling is deliberate: a provider error marks the tree failed and backs off, an unexpected exception is contained to that tree, and three consecutive failures flag the tree in `review_recommended`. Per-tree runtime is measured and a breach of the five-minute forecast budget is reported. Tests cover the happy path, duplicate ticks, interval skipping, gated trees, backoff and recovery, repeated-failure escalation, the batch cap, budget breach, serialization guards, worker iteration/stop, and the Celery/RQ adapters failing visibly when the dependency is absent.

`project_ripening` accumulates `max(0, mean_temperature − base)` and maps the total to a stage, progress, and harvest window using the forecast daily rate when available and the trailing observed rate otherwise. The window widens with the horizon, missing days, and any extrapolation beyond forecast coverage. An unreviewed prior, fewer than 14 observed days, or a missing rate keeps the projection `experimental` with `low` confidence, and the projection always reports `internal_ripeness_inferred` and `food_safety_inferred` as false. Adjustment tests confirm a warmer forecast moves the window earlier, a cooler one later, and identical inputs produce no material change.

Honest limits: no live Open-Meteo request was made (transport is injected), no broker was started, and no cultivar prior has been reviewed — so every projection produced today is experimental and low-confidence by construction.

## Volunteer dispatch and SMS adapters

`services/notifications/` adds coordinator-approved pickup alerts over two real transports.

Dispatch requires `approved_by == coordinator_id`, a current owner approval, and a glean state of `invited`/`confirmed` (or `cancelled` for a cancellation alert); anything else raises `dispatch_not_authorized` before a message is built. Each recipient is then checked for suppression, channel, explicit SMS opt-in with a policy version, and a phone number; quiet hours (21:00–07:00 local, timezone-aware) defer rather than send; the per-volunteer daily cap and per-tick cap limit volume; and the `glean/state/recipient/template/version` key suppresses repeats.

Message content is minimised by construction: `PickupAlert` has no address, access-code, coordinate, owner, or recipient field. The renderer refuses to emit text containing any restricted marker supplied for comparison, an obvious street address, or an embedded phone number, and enforces a three-segment ceiling. Tests assert the address and access note never appear in the body or the report, and that the ledger holds only a SHA-256 body digest.

`TwilioSmsTransport` posts to the documented Messages resource with Basic auth and maps 401/403, 429 (preserving `Retry-After`), 5xx, and 4xx to stable codes; `SnsSmsTransport` publishes with a Transactional SMS type and a `MaxPrice` guard and requires `boto3`, raising `BackendUnavailable` without it. Both are tested against injected fakes that assert request shape, error mapping, and that neither the token nor the phone number appears in an exception.

Honest limits: no Twilio account, SNS topic, or real phone number exists here, so delivery, cost, and country-specific sender behaviour are unverified.

## ONNX inference runtime and weight policy

`packages/vision/harvestgrid_vision/onnx_detector.py` implements the real inference path: the artifact digest is verified before any session is created, an RGB frame plane is letterboxed to the model input, both `(C, N)` and `(N, C)` YOLOv8 layouts are decoded, class-agnostic NMS is applied, and every detection whose box is mostly outside the privacy-masked allowed region is dropped before it becomes a `Detection`.

The weights themselves are **not** supplied. `packages/vision/weights/manifest.json` records `plum-yolov8n-int8-v1` as `blocked` because no owner-consented plum dataset, reviewed model card, or held-out evaluation exists, because generic COCO/ImageNet weights contain no fruit class, and because this environment cannot reach GitHub release assets. `tools/fetch_weights.py` refuses blocked and test-only entries, verifies SHA-256 and size on any local install, writes a provenance sidecar, and will not install over an existing artifact without `--force`.

A synthetic constant-output ONNX graph is built inside `tmp_path` during tests to exercise a genuine `onnxruntime` session end to end; it is untrained, detects nothing, is never committed, and is not referenced by any product path. Fifteen runtime/decoding/geometry tests plus thirteen manifest and install tests pin this behaviour.

## Coordinator dashboard

`apps/web` is a React 19 + Vite + TypeScript single-page coordinator dashboard. Its `CoordinatorApi` port exposes only board loading, assignment approval, and receipt recording — there is no method that could return an address, raw media, or Ring data. The default `FixtureCoordinatorApi` renders a persistent `FIXTURE DATA` banner and a live-mode switch that is opt-in through `VITE_DEMO_MODE=live`.

`VITE_DEMO_MODE=true` mounts a separate, standalone demo (`apps/web/src/demo/`) driven by an in-browser `DemoStore`: tree onboarding with explicit camera consent, an ROI/mask polygon editor whose every geometry change resets both approvals, a signed-webhook replay that filters detections by allowed-region coverage, a growing-degree-day ripening curve with a warmth offset, and coordinator-approved dispatch. Twenty-five jsdom tests pin the invariants, including a replay of the recorder's scene plan against the rendered page: approvals invalidate on any vertex or mask edit, dispatch is refused without owner and coordinator approval plus confirmed readiness and at least one assignment, the opted-out volunteer yields `sms_opt_in_required`, 22:30 UTC defers under quiet hours, repeats are `duplicate_suppressed`, and outcomes expose only masked numbers and 64-hex digests. The same fixture banner and the `experimental` / `humanReadinessReviewRequired` labels appear here, because a standalone page is where a fixture screenshot is most likely to be mistaken for live evidence; see [`docs/demo.md`](./demo.md).

Matching re-implements the hard filters and the five SPEC §9.2 weights in the browser so the coordinator sees the same eligibility result before submitting. A contract test parses both `engine.py` and `matching.ts` and fails if the weights diverge. Ineligible volunteers receive `score: null`, never a low score; the deterministic order is descending score, then the server tie key, then volunteer id. Approval stays disabled until owner approval is current, coordinator readiness is confirmed, the glean is in a matchable state, and at least one eligible volunteer is selected.

Receipt entry converts kilogram strings to integer grams before comparing them, so float drift cannot break `accepted + rejected == net`. It rejects over-precise values, weights above 10,000 kg, rejections without a reason, and manual weights entered without coordinator authority and a note. The export projection is checked against `packages/contracts/donation-receipt.schema.json` and contains no address, access note, volunteer, rejection, scale, or evidence field. The access-reveal preview denies non-volunteers, cancelled assignments, inactive consent, missing owner approval, and times outside the reveal window, and caps any grant at two hours or the assignment expiry.

## Reproduce

```bash
make lint typecheck test-unit test-contract test-security build dev
cd apps/web && corepack pnpm install && corepack pnpm typecheck && corepack pnpm test && corepack pnpm build
```

```bash
docker compose config -q          # compose schema + interpolation
docker compose up -d db redis && docker compose ps
cd apps/web && corepack pnpm dev  # then: make demo-selftest, or make demo-record with a browser
```

`make test-web` runs the three dashboard checks. The dev server binds `0.0.0.0:5173` and proxies `/v1` to `API_BASE_URL_INTERNAL` (default `http://127.0.0.1:8000`); fixture mode needs no backend. `base` defaults to `/harvestgrid/`, matching the GitHub Pages deployment, and `make web-build-demo` reproduces that bundle exactly.

Use a Python 3.12 runtime with zlib, SQLite, and SSL/TLS so the normal coverage and audit commands are self-contained. Do not skip the audit or run project tests under 3.11.

## Not evidenced

No Ring device/API/simulator, real observation, residential media, real ROI/masking, live weather request, broker-backed schedule, live Twilio/SNS message, trained fruit-detection ONNX artifact, CV model, reviewed cultivar prior, operational forecast, model-quality result, actual invitation/notification, durable coordinator workflow, production location authorization, real weighed glean/donation, reviewed evaluation-set manifest, model training/promotion, production/cloud verified deletion, participant/pilot, performance, deployment, or release behavior was exercised. The webhook service was only exercised with fixture secrets and synthetic bodies; the weather adapter and scheduler only with recorded-shape synthetic JSON and an in-memory store; notifications only against injected HTTP/SNS fakes; the vision worker only with synthetic pixel grids and no trained ONNX artifact (the runtime smoke test used an untrained constant-output graph); and the dashboard only with fixture data in jsdom and a local dev server. Ripening accuracy and SMS deliverability are unmeasured. Three-second-to-fifteen-minute latency requirements, browser E2E against a live API, and mobile/large-organization performance were not measured. Registration is an in-memory fixture behind typed ports: no FastAPI endpoint, durable database, Cognito authentication, or real owner/organization onboarding ran, and no camera view was approved. The verified weights, receipt, and two-example evaluation aggregate are synthetic in-memory values, not a real held-out evaluation set or model-quality claim. No operational forecast or trained model artifact exists.

## Coordination API and verified reference checkpoint

`services/api/harvestgrid_api/` exposes the ripening scheduler and the volunteer dispatcher over
HTTP (`docs/api.md`): `POST /v1/forecasting/trigger`, `GET /v1/forecasting/{tree_id}/projection`,
`POST /v1/notifications/dispatch`, and `/healthz`.

The API is fixture-deployed but not fake-behaved. Role checks and organization scoping are real:
another organization's tree returns the same `404` as a missing tree, a volunteer role cannot
read projections, and `approved_by` is taken from the authenticated actor, never the request.
Both `POST` routes require an `Idempotency-Key`: a replay returns the stored response with
`Idempotent-Replay: true` and does not tick the weather provider or send an SMS again, while a
changed body on the same key is `409 idempotency_key_reused`. A *different* key does not bypass
notification suppression, because the ledger key `glean/state/recipient/template_version` still
blocks a second notice about the same glean. Dispatch responses contain digests, never bodies or
phone numbers, and the glean's restricted address/access note is used only as a leak marker —
a coordinator note that repeats it causes the message to be dropped rather than sent.

`WorkerBackendUnavailable` is translated to `503 worker_backend_unavailable`, so a deployment
without a broker fails in the open instead of reporting a queued job. Every response carries an
`X-Request-Id` ULID that also appears in error documents, and unexpected failures collapse to a
generic `500` with no internals.

Tests drive the application through `packages/testkit/harvestgrid_testkit/asgi.py`, a small
synchronous ASGI driver. The sandbox interpreter has no `_ssl`, so anyio's asyncio backend — and
therefore `starlette.testclient` — cannot even import. Rather than skip the slice, every
dependency and route handler is a coroutine and the blocking service calls run through
`asyncio.to_thread`; `RequestIdMiddleware` is a pure ASGI middleware rather than
`BaseHTTPMiddleware`, which would spawn an anyio task group per request. `tests/unit/test_api_scheduler.py`
and `tests/unit/test_api_notifications.py` cover 202/404/403/401/409/422/503 paths, idempotent
replays, cross-organization isolation, quiet hours, per-recipient daily caps, duplicate
suppression, provider failure, and leak-marker rejection.

### Reference checkpoint

`packages/vision/weights/manifest.json` now declares `fruit-quality-yolov8n-320-v1` as `ready`
alongside the still-`blocked` `plum-yolov8n-int8-v1`. It is a real, trained YOLOv8n
fruit-quality detector (12 144 721 bytes, SHA-256 `c76ccd5e…070b6`, 8 fresh/rotten classes,
320×320 input) fetched from a pinned upstream commit through a source archive whose own digest is
also verified. `tools/fetch_weights.py fetch|install-archive` refuses to install it without
`--accept-license AGPL-3.0-only`, because the ONNX metadata records Ultralytics' AGPL-3.0 license
even though the upstream repository claims MIT. No weight bytes are committed; the sidecar records
the artifact digest, archive digest and member, license, class names, and input size.

`tools/verify_vision.py` loads the installed artifact through `YoloOnnxDetector` and runs it on a
real photograph behind a privacy ROI polygon plus an exclusion mask built by
`harvestgrid_domain.roi.allowed_cells`. The recorded run in
`evidence/vision-artifact-verification.json` (480×360 frame grid, sample `train_batch0.jpg` from
the same pinned archive, sample not committed) reports 7 detections on the full frame, 4 after the
ROI polygon, and 3 after the exclusion mask, with no kept box centred outside the ROI. That is
inference against real weights and real masking, not a synthetic fixture.

The checkpoint is **not** an operational model and `approved_for_operational_use` stays `false`:
it has no plum class, its classes are fresh/rotten quality labels, its evaluation is the upstream
author's own split, and it was never evaluated on HarvestGrid sites, seasons, or privacy
requirements. `docs/models/fruit-quality-yolov8n-320-v1.md` records all of that. The plum detector
remains blocked for lack of a consented dataset.
