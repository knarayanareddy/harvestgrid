# HarvestGrid validation record

Recorded 2026-10-23. Project tests, builds, fixture execution, and dependency audit ran under source-built CPython 3.12.12 with zlib, SQLite, and OpenSSL support; Python 3.11 was not substituted.

## Passing checks

| Check | Result |
| --- | --- |
| Ruff lint + format check | Passed |
| Strict mypy | Passed; 9 source files |
| Unit tests | 44/44 passed |
| Branch-aware domain + matching coverage | 91.36%; required gate 90% retained |
| Canonical schema/contract tests | 4/4 passed; schema copy byte-identical |
| Operational/security boundary test | 1/1 passed |
| Third-party dependency audit | No known vulnerabilities; editable local package skipped |
| Source distribution + wheel | Both built successfully |
| Fixture walking path | Approval/assignment/revocation, minimized export, short-lived access-token revocation, five-stage fixture deletion, plus a separate coordinator-verified outcome, non-sensitive receipt, forecast evaluation, closure, and a two-example synthetic evaluation aggregate with an ineligible model data gate |
| **Total tests** | **49/49 passed** |

The forecast remains `experimental`, `seasonal_baseline`, human-readiness-review-required, and visible-only. Internal-ripeness and food-safety inference remain false.

The in-memory operations slice requires owner and coordinator readiness approvals before deterministic proposals, requires coordinator assignment approval, supports idempotent multi-volunteer confirmation, and permits restricted-location return only to a confirmed assignee inside the explicit reveal window. Owner revocation cancels assignments and denies subsequent access. No notification is sent and no real address is used.

The metadata-only privacy service binds location grants to the existing confirmed-assignment workflow and caps them at the earlier of two hours or the assignment reveal expiry. Cancellation/revocation and token expiry invalidate access; owner export requires recent authentication and omits exact addresses, access notes, raw media, and tokens. Consent revocation, camera unlink, safeguarded tree deletion, and organization offboarding produce scoped local deletion jobs. Five stage receipts verify removal of raw-media, derived, forecast, and access/analytics artifact metadata; tests confirm camera-scoped deletion leaves another camera's artifacts intact and full deletion removes all target artifacts. These are fixture-store checks, not restricted-cloud or Ring deletion evidence.

A separate confirmed-workflow branch records 10.0 kg gross, 8.5 kg net, 8.0 kg accepted, and 0.5 kg rejected with a coarse spoilage reason and fixture scale/evidence metadata. Coordinator verification produces one deterministic receipt and a single-outcome evaluation; duplicate entry/verification/closure does not create another version or receipt. The receipt export excludes volunteer, address/access, rejection, scale, and evidence-hash fields. The canonical forecast has no weight estimate, so the evaluation reports `forecast_weight_unavailable` and a visible-count absolute error of 2 rather than inventing a weight error. Automatic model promotion is false. Tests also cover non-null weight errors, undefined zero-weight percentage error, invalid weight arithmetic/evidence, authorization, stale versions, and immutable post-outcome state. They exposed and now regress the forecast validator's estimate/lower/upper unpacking order.

The reviewed evaluation-set fixture requires an attestation and at least two coordinator-verified examples, detects replay conflicts, rejects mixed model versions, computes start/end MAE, interval coverage/width, visible-count MAE, and weight MAE/MAPE where defined, and exports aggregates without tree/site/recipient/location identifiers. The separate data gate requires 100 tree-season sequences, 20 sites, and held-out site/season splits; the walking fixture remains ineligible and automatic promotion is always false.

## Reproduce

```bash
make lint typecheck test-unit test-contract test-security build dev
```

Use a Python 3.12 runtime with zlib, SQLite, and SSL/TLS so the normal coverage and audit commands are self-contained. Do not skip the audit or run project tests under 3.11.

## Not evidenced

No Ring device/API/simulator, real observation, residential media, ROI/masking, weather, CV model, cultivar prior, operational forecast, model-quality result, actual invitation/notification, durable coordinator workflow, production location authorization, real weighed glean/donation, reviewed evaluation-set manifest, model training/promotion, production/cloud verified deletion, participant/pilot, performance, deployment, or release behavior was exercised. The verified weights, receipt, and two-example evaluation aggregate are synthetic in-memory values, not a real held-out evaluation set or model-quality claim. No operational forecast or trained model artifact exists.
