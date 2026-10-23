# HarvestGrid preflight

Recorded 2026-10-23 for the local vertical slices. Project code, tests, builds, fixtures, and audits target Python 3.12; no operational media or model toolchain is implied.

## Available and exercised

| Capability | Recorded version/state |
| --- | --- |
| Project interpreter | Source-built CPython 3.12.12 with zlib 1.3.1, SQLite 3.45.3, and OpenSSL 3.0.19 |
| Package manager | uv 0.9.5; `uv.lock` is frozen for normal bootstrap |
| Quality tools | Ruff 0.13.2; mypy 1.18.2; pytest 9.0.3; pytest-cov 6.3.0 |
| Runtime dependencies | None for current domain/matching packages |
| Audit runner | The same CPython 3.12.12 environment; `pip-audit --skip-editable` completed over TLS |

The restored source-built 3.12 interpreter includes the native modules needed by coverage, package builds, and TLS audits. `make test-security` completed in the project environment without an external audit interpreter. Use a complete Python 3.12 installation with zlib, SQLite, and SSL/TLS for the same self-contained workflow.

## Blocked or not implemented

- Documented Ring API/SDK/simulator/device access, OAuth, webhooks, and capability discovery.
- Owner/coordinator consent, residential media, ROI/masking, camera registration, and CV inference.
- Weather adapter, cultivar priors, licensed datasets, trained model artifacts, and forecast evaluation.
- Application/API, production persistence, deployed workflow/notification adapters, verified deletion, and real scoped-location authorization.
- Participants, operational pilot, food-safety decisions, deployment, and release evidence.

`datasets/`, `models/`, and `training/` contain boundaries/placeholders, not data or trained models.

## First local checks

```bash
python3.12 --version
uv --version
make bootstrap
make lint typecheck test-unit test-contract test-security build
make dev
```

No fixture may satisfy a Ring, real-observation, model-quality, or operational-release gate. Audit failures must remain visible and be remediated or recorded.
