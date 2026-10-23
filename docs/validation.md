# HarvestGrid validation record

Recorded 2026-10-23. Earlier slices were validated under source-built CPython 3.12.12 with zlib, SQLite, and OpenSSL support, including the dependency audit. The `FR-HG-001` onboarding revalidation ran on 2026-10-23 under source-built CPython 3.12.12 with zlib 1.3.1 and SQLite 3.38.2; that sandbox interpreter has no OpenSSL module, so the network audit step was not rerun for this slice. Python 3.11 was not substituted in either run.

## Passing checks

| Check | Result |
| --- | --- |
| Ruff lint + format check | Passed |
| Strict mypy | Passed; 11 source files |
| Unit tests | 68/68 passed |
| Branch-aware domain + matching coverage | 93.83%; required gate 90% retained; onboarding 99%, onboarding adapters 100% |
| Schema/contract tests | 26/26 passed; forecast schema copy byte-identical; tree-registration projection validated against `packages/contracts/tree-registration.schema.json` |
| Operational/security boundary test | 1/1 passed |
| Third-party dependency audit | Not rerun this slice (sandbox interpreter lacks OpenSSL). Last recorded result: no known vulnerabilities; editable local package skipped |
| Source distribution + wheel | Both built successfully |
| Fixture walking path | Coarse-location owner/organization/tree/consent registration, approval/assignment/revocation, minimized export, short-lived access-token revocation, five-stage fixture deletion, plus a separate coordinator-verified outcome, non-sensitive receipt, forecast evaluation, closure, and a two-example synthetic evaluation aggregate with an ineligible model data gate |
| **Total tests** | **95/95 passed** (68 unit + 26 contract + 1 security) |

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

## Reproduce

```bash
make lint typecheck test-unit test-contract test-security build dev
```

Use a Python 3.12 runtime with zlib, SQLite, and SSL/TLS so the normal coverage and audit commands are self-contained. Do not skip the audit or run project tests under 3.11.

## Not evidenced

No Ring device/API/simulator, real observation, residential media, ROI/masking, weather, CV model, cultivar prior, operational forecast, model-quality result, actual invitation/notification, durable coordinator workflow, production location authorization, real weighed glean/donation, reviewed evaluation-set manifest, model training/promotion, production/cloud verified deletion, participant/pilot, performance, deployment, or release behavior was exercised. Registration is an in-memory fixture behind typed ports: no FastAPI endpoint, durable database, Cognito authentication, or real owner/organization onboarding ran, and no camera view was approved. The verified weights, receipt, and two-example evaluation aggregate are synthetic in-memory values, not a real held-out evaluation set or model-quality claim. No operational forecast or trained model artifact exists.
