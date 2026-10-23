# HarvestGrid data inventory

Inventory date: 2026-10-23. No residential, operational, or training data is present.

| Data | Current location | Classification/provenance | Retention and use |
| --- | --- | --- | --- |
| Fictional harvest window | `examples/harvest-window.example.json` | Synthetic seasonal-baseline contract | Source-controlled schema/semantic tests only |
| Candidate constraints/features | `tests/unit/test_matching.py`, `tests/unit/test_operations.py` | Synthetic volunteer/coordinator inputs | In-memory deterministic tests only |
| Glean workflow/location inputs | `packages/domain/harvestgrid_domain/operations.py`, tests, walking path | Synthetic approvals, assignments, audit events, and explicitly fictional restricted-location strings | In-memory only; location inputs are not stored in snapshots or printed; no real invitation, address, or deletion receipt |
| Outcome weights/receipts/evaluation | `packages/domain/harvestgrid_domain/outcomes.py`, tests, walking path | Fictional gross/net/accepted/rejected Decimal weights, coarse rejection codes, scale/evidence references, recipient/glean IDs, and forecast errors | In-memory only; receipt dictionary omits location, volunteer, rejection, scale, and evidence details; no real donation, export URI, evaluation set, or model update |
| Cultivar boundary | `content/cultivars/` | Placeholder documentation; no priors | No operational inference |
| Dataset/model boundaries | `datasets/`, `models/`, `training/` | Documentation/placeholders only | No dataset, artifact, or training run |
| Schema copy | `packages/contracts/` | Non-sensitive forecast contract | Byte-drift tests |
| Configuration examples | `.env.example` | Safe placeholders | Real environment files are Git-ignored |

## Not present or collected

No real Ring media/event, exact address/coordinates, access note, owner identity, tree ROI, face/plate/person data, volunteer identity/availability, recipient information, harvest weight, donation receipt, or coordinator decision is present. Restricted-location strings in source/tests are explicitly fictional and are not persisted in workflow snapshots. No database, analytics, prompt log, media store, or model training set exists.

Future raw residential media requires separate revocable consent, ROI/exclusion masking, restricted server-to-server access, seven-day default deletion after extraction, and no analytics/training use without separate consent. Exact location and access notes require assignment-scoped, time-limited reveal.

`datasets/private/`, model artifacts, and common model-binary extensions are Git-ignored. Do not convert real residential media or coordinator records into test fixtures.
