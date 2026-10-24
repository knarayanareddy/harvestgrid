# HarvestGrid data inventory

Inventory date: 2026-10-24. No residential, operational, or training data is present.

| Data | Current location | Classification/provenance | Retention and use |
| --- | --- | --- | --- |
| Fictional harvest window | `examples/harvest-window.example.json` | Synthetic seasonal-baseline contract | Source-controlled schema/semantic tests only |
| Candidate constraints/features | `tests/unit/test_matching.py`, `tests/unit/test_operations.py` | Synthetic volunteer/coordinator inputs | In-memory deterministic tests only |
| Glean workflow/location inputs | `packages/domain/harvestgrid_domain/operations.py`, tests, walking path | Synthetic approvals, assignments, audit events, and explicitly fictional restricted-location strings | In-memory only; location inputs are not stored in snapshots or printed; no real invitation, address, or deletion receipt |
| Outcome weights/receipts/evaluation | `packages/domain/harvestgrid_domain/outcomes.py`, tests, walking path | Fictional gross/net/accepted/rejected Decimal weights, coarse rejection codes, scale/evidence references, recipient/glean IDs, and forecast errors | In-memory only; receipt dictionary omits location, volunteer, rejection, scale, and evidence details; no real donation, export URI, evaluation set, or model update |
| Registration records | `packages/domain/harvestgrid_domain/onboarding.py`, `onboarding_adapters.py`, tests | Fictional organization/owner/member IDs, display names, coarse geohash cells, optional bloom dates, consent decisions, audit events | In-memory fixture store only; no exact address, coordinates, contact details, or real participant identity; provenance labeled `fixture` |
| Cultivar catalog | `content/cultivars/european-plum-v1.json` | Reviewed draft content document; no seasonal priors | Read-only versioned reference; keeps forecasts experimental |
| Dataset/model boundaries | `datasets/`, `models/`, `training/` | Documentation/placeholders only | No dataset, artifact, or training run |
| Camera view ROI/masks | `packages/domain/harvestgrid_domain/roi.py`, `packages/contracts/camera-view.schema.json`, tests | Normalized synthetic polygons for ROI and privacy masks; fictional view/tree/device IDs | In-memory only; no real frame, device, address, or coordinate; schema forbids exact address, coordinates, raw-media URI, and person/vehicle detection |
| Ring webhook envelopes | `services/ring-adapter/`, `tests/unit/test_ring_webhooks.py` | Synthetic signed event bodies with fictional `event_id`, `device_id`, and `connection_id`; fixture secret only in tests | In-memory store/queue with bounded retention; bodies capped at 64 KB; person, face, plate, address, and coordinate fields are rejected; no real Ring secret or device |
| Weather features | `packages/weather-client/`, `tests/unit/test_weather_open_meteo.py` | Synthetic Open-Meteo JSON for coarse geohash cells at one decimal place; recorded GDD/precipitation features | In-memory six-hour cache; transport is injected, so no test makes a live request; provenance records `exact_coordinates_included: false` |
| Vision features and provenance | `packages/vision/`, `tests/unit/test_vision_worker.py`, `tests/contract/test_adapter_contracts.py` | Synthetic luminance/saturation grids and a 16-column `features_v1` row with version and masking provenance | In-memory only; no raw frame, model artifact, or training set; rejected observations emit no row; inference flags are hard-coded false |
| Coordinator dashboard state | `apps/web/`, tests | Synthetic board, volunteer/recipient IDs, weights, and receipt numbers; all screens labelled `FIXTURE DATA` | Browser-memory fixtures only; no analytics, cookie, prompt log, or network call in fixture mode; receipt export is limited to the donation-receipt schema |
| Schema and projection contracts | `packages/contracts/` | Non-sensitive forecast schema copy, tree-registration and camera-view projections, and the non-sensitive donation receipt | Byte-drift and JSON Schema contract tests |
| Configuration examples | `.env.example` | Safe placeholders | Real environment files are Git-ignored |

## Not present or collected

No real Ring media/event, exact address/coordinates, access note, owner identity, real tree ROI, face/plate/person data, real volunteer identity/availability, real recipient information, real harvest weight, real donation receipt, or real coordinator decision is present. Fixtures use fictional identifiers, normalized polygons, synthetic pixel grids, and coarse geohash cells only; no real owner, organization, device, or participant is present, and no cultivar content beyond the checked-in draft is available. Restricted-location strings in source/tests are explicitly fictional and are not persisted in workflow snapshots. No database, analytics, prompt log, media store, model artifact, or model training set exists; no ONNX file is checked in (`*.onnx` is Git-ignored).

Future raw residential media requires separate revocable consent, ROI/exclusion masking, restricted server-to-server access, seven-day default deletion after extraction, and no analytics/training use without separate consent. Exact location and access notes require assignment-scoped, time-limited reveal.

`datasets/private/`, model artifacts, and common model-binary extensions are Git-ignored. Do not convert real residential media or coordinator records into test fixtures.
