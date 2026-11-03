# HarvestGrid — remediation plan

Planned only; no project commands were executed. Preserve fixture labels; collect no residential media or contact participants without approval. Findings: [REVIEW.md](REVIEW.md). Implementation requires owner-approved pilot scope.

## Phase 0 — baseline and contract (HG-T1)

**Links:** HG-2, HG-5–7. **Prerequisite:** isolated synthetic environment and owner-selected pilot boundary. **Observed paths:** `README.md`, `apps/web/src/main.tsx`, `apps/web/src/api/httpClient.ts`, `services/api/harvestgrid_api/{__main__,app,container}.py`.

1. Trace fixture and HTTP browser entry paths through actual network routes and API container construction. Record which operations are unavailable and which require identity, a provider or durable storage.
2. With coordinators and domain reviewers, define intended actions, permissible forecast claims, food-safety exclusions, time-window semantics, and who may view or modify each resource.
3. In an approved isolated copy, record environment and run the README-documented `make lint typecheck`, `make test-unit test-contract test-security`, and `make test-web` as **future baseline checks**. Record missing tools, failures, skips and audit reachability; do not lower existing gates.

**Output:** interface/identity map, baseline log and accepted scope. **Verify:** reviewers can follow each browser action to a real API route or an explicitly marked gap; distinguish independent Pages history from the new baseline. **DoD:** [ ] gaps mapped; [ ] failures recorded; [ ] scope approved. **Exit:** contract gaps and owners agreed before implementation.

## Phase 1 — modes and actual coordination contract (HG-T2)

**Links:** HG-5, HG-6, HG-7. **Prerequisite:** HG-T1. **Observed paths:** `apps/web/Dockerfile`, `apps/web/src/main.tsx`, `apps/web/src/App.tsx`, `apps/web/src/api/httpClient.ts`, `services/api/harvestgrid_api/app.py`. **Proposed:** API contract and integration tests under `tests/integration/` and web tests.

1. Define the supported build-mode enum and make instructions and entrypoint agree. Reject unsupported values instead of silently selecting fixture mode; preserve the explicitly labelled standalone demo.
2. Specify the board, assignment and receipt request/response schemas and authenticated session transport; implement the missing server/client slice only within the approved scope. Derive access windows from the selected event, including timezone, expiry and invalid ordering.
3. Exercise the shipped browser adapter against the actual API entrypoint: successful authorized flow, missing/expired token, wrong organization, absent route, stale access window and changed idempotency payload.

**Output:** mode contract, implemented route/client seam and tests. **Verify:** `false`, `live`, `fixture` and invalid settings have explicit expected outcomes; deliberately break route or auth wiring and show a test fails. Verify displayed success corresponds to a recorded server action, not only a mocked response. **DoD:** [ ] mode mismatch resolved; [ ] real contract exercised; [ ] invalid dates denied; [ ] negative control observed. **Exit:** contract tests pass without hiding fixture mode.

## Phase 2 — durable authorized coordination (HG-T3)

**Links:** HG-1, HG-3; preventive security. **Prerequisite:** HG-T1 and approved HG-T2 contract. **Observed paths:** `docker-compose.yml`, `services/api/harvestgrid_api/container.py`, `services/api/harvestgrid_api/deps.py`. **Proposed:** migrations, persistent store and broker adapters, integration tests.

1. Design ownership, scoped queries and migration/rollback for registrations, assignments, consent, job and idempotency records. Specify atomic request/dispatch behavior and conflict handling before replacing in-memory components.
2. Wire persistent stores and a real worker into the active API path; isolate database and broker networks, require deployment-specific secrets and replace fixture authentication with approved identity. Keep local-only defaults confined to local development.
3. Using synthetic tenants, test unauthorized read/write, duplicate concurrent approvals, worker retry, database outage and process restart. Re-read durable state and verify that duplicate requests do not send duplicate messages.

**Output:** migration, access matrix and durable execution evidence. **Verify:** inspect injected dependencies from the real entrypoint; remove an authorization check or idempotency guard as a negative control and demonstrate a failing test. Confirm passwords do not appear in artifacts or logs. **DoD:** [ ] restart and concurrency observed; [ ] cross-tenant denial tested; [ ] active wiring proven; [ ] secrets isolated. **Exit:** durable state and authorization accepted before scale-out.

## Phase 3 — provider and privacy lifecycle (HG-T4)

**Links:** HG-2, HG-4. **Prerequisite:** HG-T1, HG-T3, owner authorization and documented provider access. **Observed paths:** `tests/unit/test_ring_webhook_server.py`, `README.md`; proposed provider adapter and privacy integration tests.

1. Obtain official integration and signature specifications and sandbox permission. Compare actual event schema, signature, timestamp and retry rules to the synthetic test; explicitly record unsupported capabilities rather than assuming Ring compatibility.
2. Design consent-scoped capture, minimization, revocation, deletion, replay protection and credential rotation across provider, job queue, database, object storage, logs and backups. Implement only approved surfaces.
3. Test forged, expired, replayed and duplicated events across restart; revoke synthetic consent during queued work; verify deletion at each actual storage boundary and after a backup restore, accounting for documented retention windows.

**Output:** vendor compatibility record, data inventory and deletion evidence. **Verify:** sandbox response and storage readback, not an application success message alone; disabling signature verification must fail a negative test. **DoD:** [ ] provider contract confirmed or integration remains disabled; [ ] revocation blocks processing; [ ] deletion and restore observed. **Exit:** privacy owner accepts residual retention and incident response.

## Phase 4 — automation, release and recovery (HG-T5)

**Links:** HG-1, HG-3 and preventive dependency/operations requirements. **Prerequisite:** HG-T2 and HG-T3; HG-T4 for any provider release. **Observed paths:** `.github/workflows/pages.yml`, `pyproject.toml`, `apps/web/package.json`, both Dockerfiles. **Proposed:** service CI, deployment runbook and recovery drill.

1. Add an independent CI gate for the documented Python lint, type, unit, contract and security checks plus frontend typecheck/test/build; retain frozen locks and record network audit failures distinctly from clean results.
2. Review dependency advisories and artifact provenance, pin actions/images to verified immutable revisions as appropriate, and separate runtime dependencies from development tooling. Build an immutable release artifact from the reviewed commit and record its identity.
3. Set service-level objectives, alerting, backup ownership and schema-compatible rollback; run an isolated deployment, simulate database/worker loss, restore encrypted backups and check idempotency and consent state before resuming dispatch.

**Output:** reproducible gate results, release manifest and witnessed recovery record. **Verify:** deliberately break one gate, inspect the deployed artifact identity and observe restored data and usable service; a successful health endpoint alone is insufficient. **DoD:** [ ] independent CI evidence; [ ] artifact identified; [ ] restore and rollback observed. **Exit:** operations owner accepts recovery objectives.

## Phase 5 — domain, UX and independent acceptance (HG-T6)

**Links:** HG-2, HG-6; preventive domain and accessibility requirements. **Prerequisite:** HG-T2–T5 completed, including durable authorization, replay and deletion controls, and release/recovery evidence. **Observed paths:** `apps/web/src/App.tsx`, `apps/web/tests/app.test.tsx`, `README.md`; proposed domain validation and user-research records.

1. If a model is authorized, evaluate on consented held-out data across relevant cultivars and seasons; document failure rates, uncertainty, training/evaluation separation and human-review thresholds. Do not infer food safety from images.
2. Test keyboard and screen-reader navigation, non-map alternatives, receipt errors, access-window confirmation and fixture/live status with representative coordinators and participants; document unresolved barriers.
3. Have independent reviewers repeat authorization, replay, restart and deletion cases against the release candidate; inspect mutation/negative-control evidence and all provider and recovery limitations. Defer a live pilot if any required boundary remains unverified.

**Output:** domain validation, accessibility findings and signed acceptance record. **Verify:** deliberately incorrect labels or broken authorization fail relevant checks; actual users and domain experts review the consequences, not only test counts. **DoD:** [ ] model limitations accepted; [ ] UX observations recorded; [ ] independent authorization, replay, restart and deletion evidence reviewed. **Exit:** after HG-T2–T5 and these checks, the owner explicitly accepts or rejects the bounded pilot; unverified boundaries block acceptance.

## Dependencies, approval and release policy

| Task | Depends on | Enables |
| --- | --- | --- |
| HG-T1 | Pilot owner and synthetic environment | Contract and baseline |
| HG-T2 | T1 | Real UI/API seam |
| HG-T3 | T1, T2 contract | Durable multi-tenant operation |
| HG-T4 | T1, T3, provider permission | Authorized provider processing |
| HG-T5 | T2, T3; T4 for provider release | Reproducible release and recovery |
| HG-T6 | T2–T5 completed | Independent acceptance |

Production acceptance: [ ] approved identity and access matrix; [ ] durable retries and consent; [ ] authorized vendor and data lifecycle; [ ] measured domain limits; [ ] accessibility review; [ ] actual restore and release provenance; [ ] owner sign-off. Credentials, real data collection, provider calls, infrastructure, migrations and publication require owner approval. Release immutable artifacts only after review; halt dispatch on uncertain consent or state integrity. Roll back application versions only where schema-compatible; otherwise follow the approved forward-fix/restore plan and reconcile queued actions before resuming.
