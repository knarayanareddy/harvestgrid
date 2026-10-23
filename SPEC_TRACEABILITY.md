# HarvestGrid — Specification Traceability

**Status:** local registration, operations, coordinator-verified outcome, synthetic evaluation, and privacy-lifecycle slices validated; most requirements remain incomplete
**Coverage:** 60 unique requirement and acceptance IDs from [`SPEC.md`](./SPEC.md); 1 row now has implementation and test paths for `FR-HG-001`
**Evidence warning:** linked evidence is local source/fixture evidence only; no live integration, device, user-study, or release evidence is implied.

This is the working register required by the implementation specification. Requirement summaries aid navigation only; `SPEC.md` remains normative.

## Status rules

- `not_started`: no implementation exists.
- `in_progress`: active implementation exists but is not yet verified.
- `implemented`: code exists; required tests or release evidence remain incomplete.
- `verified`: all required tests and evidence for this row are linked and current.
- `blocked`: an explicit external dependency or decision prevents completion; identify it in `docs/` and evidence.
- `deviation_pending`: a requested change is awaiting explicit approval in [`SPEC_DEVIATIONS.md`](./SPEC_DEVIATIONS.md); a MUST requirement remains incomplete.

Use paths relative to this project root for implementation, tests, and evidence. Do not enter a path until it exists. Do not use fixture/simulated evidence to satisfy a live-integration gate.

## Register

| ID | Requirement or scenario summary | Status | Implementation paths | Test paths | Demo/device evidence |
| --- | --- | --- | --- | --- | --- |
| `FR-HG-001` | Register an owner, organization, tree, cultivar, coarse location, and explicit operational consents. | `implemented` | `packages/domain/harvestgrid_domain/onboarding.py`<br>`packages/domain/harvestgrid_domain/onboarding_adapters.py`<br>`packages/contracts/tree-registration.schema.json` | `tests/unit/test_onboarding.py`<br>`tests/unit/test_onboarding_adapters.py`<br>`tests/contract/test_onboarding_contract.py`<br>`docs/validation.md` | — |
| `FR-HG-002` | Link a supported Ring device through the documented backend OAuth/API flow and discover actual capabilities. | `not_started` | — | — | — |
| `FR-HG-003` | Configure tree ROI, exclusion/privacy masks, camera view version, and owner-approved collection schedule. | `not_started` | — | — | — |
| `FR-HG-004` | Collect supported snapshots/live-view frames/event clips through the real Ring adapter; deduplicate and record provenance. | `not_started` | — | — | — |
| `FR-HG-005` | Register frames, detect camera movement, crop to the approved tree ROI, and reject unsuitable views. | `not_started` | — | — | — |
| `FR-HG-006` | Produce visible-fruit detections, count/area/color features, occlusion and quality metrics with model/data versions. | `not_started` | — | — | — |
| `FR-HG-007` | Generate a versioned probabilistic harvest window and visible-yield estimate using the canonical schema. | `in_progress` | `packages/domain/harvestgrid_domain/forecast.py` | `tests/contract`<br>`tests/unit/test_forecast.py` | — |
| `FR-HG-008` | Mark forecasts experimental when data/model gates are unmet; always require human readiness confirmation. | `in_progress` | `packages/domain/harvestgrid_domain/forecast.py`<br>`packages/domain/harvestgrid_domain/evaluation.py` | `tests/unit/test_forecast.py`<br>`tests/unit/test_evaluation.py` | — |
| `FR-HG-009` | Obtain owner approval before a forecast becomes a glean request. | `implemented` | `packages/domain/harvestgrid_domain/operations.py` | `tests/unit/test_operations.py` | — |
| `FR-HG-010` | Capture volunteer training/availability/equipment and recipient acceptance/capacity as hard constraints. | `implemented` | `packages/matching/harvestgrid_domain_matching/engine.py`<br>`packages/domain/harvestgrid_domain/operations.py` | `tests/unit/test_matching.py`<br>`tests/unit/test_operations.py` | — |
| `FR-HG-011` | Produce deterministic coordinator-reviewed assignments with no protected-trait scoring. | `implemented` | `packages/matching/harvestgrid_domain_matching/engine.py`<br>`packages/domain/harvestgrid_domain/operations.py` | `tests/unit/test_matching.py`<br>`tests/unit/test_operations.py` | — |
| `FR-HG-012` | Orchestrate invitations, reminders, cancellations, access instructions, and escalation with durable state. | `in_progress` | `packages/domain/harvestgrid_domain/operations.py` | `tests/unit/test_operations.py` | — |
| `FR-HG-013` | Reveal exact address/access notes only to assigned eligible volunteers for the minimum necessary period. | `in_progress` | `packages/domain/harvestgrid_domain/operations.py` | `tests/unit/test_operations.py` | — |
| `FR-HG-014` | Record coordinator completion, actual harvested/accepted weights, rejection/spoilage, and donation receipt data. | `implemented` | `packages/domain/harvestgrid_domain/operations.py`<br>`packages/domain/harvestgrid_domain/outcomes.py` | `tests/unit/test_outcomes.py` | — |
| `FR-HG-015` | Compare forecast to outcome in an evaluation set; no automatic production retraining from one outcome. | `in_progress` | `packages/domain/harvestgrid_domain/outcomes.py`<br>`packages/domain/harvestgrid_domain/evaluation.py` | `tests/unit/test_outcomes.py`<br>`tests/unit/test_evaluation.py`<br>`docs/validation.md` | — |
| `FR-HG-016` | Support consent revocation, camera unlink, data export/deletion, and organization offboarding. | `implemented` | `packages/domain/harvestgrid_domain/operations.py`<br>`packages/domain/harvestgrid_domain/privacy.py` | `tests/unit/test_operations.py`<br>`tests/unit/test_privacy.py`<br>`docs/validation.md` | — |
| `INT-HG-001` | A documented Ring API/SDK/simulator/device integration MUST execute in the main observation path and appear in demo evidence. | `not_started` | — | — | — |
| `INT-HG-002` | Ring calls MUST be server-to-server; browser code never calls Ring OAuth/device endpoints directly. | `not_started` | — | — | — |
| `INT-HG-003` | Webhooks use official HMAC verification, raw-body handling, five-second acknowledgement, idempotency, and rate-limit behavior described in the companion hackathon review. | `not_started` | — | — | — |
| `INT-HG-004` | Ring capability discovery selects snapshot, live-view, or event-clip strategy; unsupported capabilities fail visibly rather than being mocked. | `not_started` | — | — | — |
| `INT-HG-005` | Open-Meteo or a configured documented weather adapter supplies coarse-location historical/forecast weather; exact residential coordinates are not sent. | `not_started` | — | — | — |
| `INT-HG-006` | Custom CV inference runs in a controlled Python/ONNX service; Bedrock may summarize evidence/messages but cannot generate counts, dates, readiness, or assignments. | `not_started` | — | — | — |
| `INT-HG-007` | SES/SNS sends only coordinator/owner-approved notifications through durable workflows. | `not_started` | — | — | — |
| `NFR-HG-001` | Valid Ring event acknowledgement within five seconds; processing continues asynchronously. | `not_started` | — | — | — |
| `NFR-HG-002` | New media begins feature extraction within 15 minutes; scheduled forecasts complete within five minutes per tree on CPU reference infrastructure. | `not_started` | — | — | — |
| `NFR-HG-003` | p95 dashboard read latency below 700 ms for a 1,000-tree organization. | `not_started` | — | — | — |
| `NFR-HG-004` | Duplicate Ring/weather/notification events produce no duplicate observations, gleans, or messages. | `in_progress` | `packages/domain/harvestgrid_domain/operations.py` | `tests/unit/test_operations.py` | — |
| `NFR-HG-005` | Raw residential media is inaccessible to public users, volunteers without assignment, model trainers without separate consent, and analytics. | `in_progress` | `packages/domain/harvestgrid_domain/privacy.py` | `tests/unit/test_privacy.py` | — |
| `NFR-HG-006` | Forecast output always includes model/data version, valid interval, uncertainty, and quality/confidence reasons. | `not_started` | — | — | — |
| `NFR-HG-007` | Camera or model outage never auto-cancels a confirmed glean or marks produce unsafe/unripe; it creates a coordinator review state. | `not_started` | — | — | — |
| `SEC-HG-001` | Exact address/coordinates/access notes are isolated and revealed only under assignment policy. | `in_progress` | `packages/domain/harvestgrid_domain/operations.py`<br>`packages/domain/harvestgrid_domain/privacy.py` | `tests/unit/test_operations.py`<br>`tests/unit/test_privacy.py` | — |
| `SEC-HG-002` | Raw Ring media defaults to deletion seven days after feature extraction; crop-only media requires separate owner consent for training. | `not_started` | — | — | — |
| `SEC-HG-003` | No public residential map, face/plate recognition, person tracking, or volunteer behavioral surveillance. | `not_started` | — | — | — |
| `SEC-HG-004` | Camera consent and organizational operating consent are separately revocable; revocation stops collection and address reveal. | `in_progress` | `packages/domain/harvestgrid_domain/operations.py`<br>`packages/domain/harvestgrid_domain/privacy.py` | `tests/unit/test_operations.py`<br>`tests/unit/test_privacy.py` | — |
| `SEC-HG-005` | Visual outputs never assert internal ripeness or food safety; coordinator checklist governs operational decisions. | `in_progress` | `packages/domain/harvestgrid_domain/forecast.py` | `tests/security` | — |
| `SEC-HG-006` | Volunteers are never auto-dispatched; trained leader and coordinator approval are mandatory. | `implemented` | `packages/domain/harvestgrid_domain/operations.py`<br>`packages/matching/harvestgrid_domain_matching/engine.py` | `tests/unit/test_operations.py`<br>`tests/unit/test_matching.py` | — |
| `SEC-HG-007` | Owner address, raw media, access notes, and recipient information are excluded from logs/model prompts/public receipts. | `in_progress` | `packages/domain/harvestgrid_domain/operations.py`<br>`packages/domain/harvestgrid_domain/outcomes.py`<br>`packages/domain/harvestgrid_domain/privacy.py` | `tests/unit/test_operations.py`<br>`tests/unit/test_outcomes.py`<br>`tests/unit/test_privacy.py` | — |
| `SEC-HG-008` | Dataset exports use crop-only media, consent manifest, coarse/no location, and re-identification review. | `not_started` | — | — | — |
| `AC-HG-001` | Given no owner/coordinator view approval, when collection is requested, then no media is collected. | `not_started` | — | — | — |
| `AC-HG-002` | Given a real supported Ring device, when onboarding, then capabilities and one real observation are recorded truthfully. | `not_started` | — | — | — |
| `AC-HG-003` | Given an invalid webhook signature, when received, then it is rejected without media processing. | `not_started` | — | — | — |
| `AC-HG-004` | Given duplicate Ring events, when processed, then one observation/forecast input results. | `not_started` | — | — | — |
| `AC-HG-005` | Given an unsupported device capability, when collecting, then a visible capability error occurs and no fixture substitutes silently. | `not_started` | — | — | — |
| `AC-HG-006` | Given moved camera/poor registration, when processing, then view pauses and forecast enters quality review. | `not_started` | — | — | — |
| `AC-HG-007` | Given people/vehicles outside tree ROI, when minimizing, then masks/crop prevent their use in features or training exports. | `not_started` | — | — | — |
| `AC-HG-008` | Given low-quality/occluded images, when forecasting, then uncertainty/reason codes increase and no confident ready claim appears. | `not_started` | — | — | — |
| `AC-HG-009` | Given insufficient training data, when forecasting, then baseline output is experimental and requires review. | `verified` | `packages/domain/harvestgrid_domain/forecast.py` | `tests/unit/test_forecast.py`<br>`docs/validation.md` | — |
| `AC-HG-010` | Given any valid forecast, when serialized, then it passes schema and has internal ripeness/food-safety flags false. | `verified` | `packages/domain/harvestgrid_domain/forecast.py` | `tests/contract`<br>`docs/validation.md` | — |
| `AC-HG-011` | Given invalid interval ordering/model output, when validating, then forecast is rejected rather than repaired arbitrarily. | `verified` | `packages/domain/harvestgrid_domain/forecast.py` | `tests/unit/test_forecast.py`<br>`docs/validation.md` | — |
| `AC-HG-012` | Given no owner approval, when matching starts, then no invitations or address reveals occur. | `implemented` | `packages/domain/harvestgrid_domain/operations.py`<br>`packages/matching/harvestgrid_domain_matching/engine.py` | `tests/unit/test_operations.py`<br>`tests/unit/test_matching.py`<br>`docs/validation.md` | — |
| `AC-HG-013` | Given no trained leader, when matching, then the glean is ineligible regardless of score. | `verified` | `packages/matching/harvestgrid_domain_matching/engine.py` | `tests/unit/test_matching.py`<br>`docs/validation.md` | — |
| `AC-HG-014` | Given insufficient recipient capacity, when matching, then hard filter blocks or requires coordinator alternative. | `verified` | `packages/matching/harvestgrid_domain_matching/engine.py` | `tests/unit/test_matching.py`<br>`docs/validation.md` | — |
| `AC-HG-015` | Given two identical ranking inputs, when scored twice, then explanations and ordering are identical. | `verified` | `packages/matching/harvestgrid_domain_matching/engine.py` | `tests/unit/test_matching.py`<br>`docs/validation.md` | — |
| `AC-HG-016` | Given a material new forecast after confirmation, when received, then existing event remains and coordinator review is required. | `not_started` | — | — | — |
| `AC-HG-017` | Given cancellation, when volunteer requests access, then exact details are unavailable and tokens are revoked. | `implemented` | `packages/domain/harvestgrid_domain/operations.py`<br>`packages/domain/harvestgrid_domain/privacy.py` | `tests/unit/test_operations.py`<br>`tests/unit/test_privacy.py`<br>`docs/validation.md` | — |
| `AC-HG-018` | Given duplicate invitation/completion events, when processed, then one message/event/receipt results. | `in_progress` | `packages/domain/harvestgrid_domain/operations.py`<br>`packages/domain/harvestgrid_domain/outcomes.py`<br>`packages/domain/harvestgrid_domain/evaluation.py` | `tests/unit/test_operations.py`<br>`tests/unit/test_outcomes.py`<br>`tests/unit/test_evaluation.py` | — |
| `AC-HG-019` | Given actual weights, when verified, then evaluation uses accepted/rejected amounts correctly and no automatic model promotion occurs. | `implemented` | `packages/domain/harvestgrid_domain/operations.py`<br>`packages/domain/harvestgrid_domain/outcomes.py`<br>`packages/domain/harvestgrid_domain/evaluation.py` | `tests/unit/test_outcomes.py`<br>`tests/unit/test_evaluation.py`<br>`docs/validation.md` | — |
| `AC-HG-020` | Given owner revocation, when next collection/match occurs, then both stop and scoped deletion begins. | `in_progress` | `packages/domain/harvestgrid_domain/operations.py`<br>`packages/domain/harvestgrid_domain/privacy.py` | `tests/unit/test_operations.py`<br>`tests/unit/test_privacy.py`<br>`docs/validation.md` | — |
| `AC-HG-021` | Given another organization's tree IDs, when accessed, then authorization denies data and media. | `not_started` | — | — | — |
| `AC-HG-022` | Given a real Ring device/simulator, when the demo runs, then actual documented Ring data drives features, forecast review, and glean workflow. | `not_started` | — | — | — |
