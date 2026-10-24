# HarvestGrid dependencies

## Locked local toolchain

- Python `>=3.12,<3.13`; validated with CPython 3.12.12.
- uv 0.9.5 and `uv.lock`; Hatchling 1.27.0 builds the source distribution/wheel.
- Ruff 0.13.2, mypy 1.18.2, pytest 9.0.3, pytest-cov 6.3.0.
- jsonschema 4.25.1 with format support and pip-audit 2.9.0.
- API and vision verification: FastAPI 0.120.4, starlette 0.49.3, pydantic 2.14.1, httpx 0.28.1,
  uvicorn 0.38.0, and pillow 12.0.0 — all dev-group only. The runtime packages still declare no
  third-party dependency; `services/api/` is served by an application factory without importing
  any of them at package import time beyond FastAPI itself.
- No third-party production runtime dependency for current domain/matching packages.

The project has an independent environment and lockfile. Ring, live weather, database,
notification-provider, and AWS SDK dependencies are not present. `onnxruntime`, `onnx`, and
`numpy` are dev-group entries used to exercise the vision runtime and weight verification.

## Security audit

The first SSL-capable audit found `PYSEC-2026-1845` in pytest 8.4.2. HarvestGrid updated to pytest 9.0.3, regenerated `uv.lock`, and reran all checks. The final audit reported no known vulnerabilities on 2026-10-23.

Normal command:

```bash
make test-security
# equivalent audit portion: uv run pip-audit --skip-editable
```

The recorded source-built 3.12 interpreter lacks SSL, so pip-audit 2.9.0 ran under SSL-capable system Python 3.11.2 with `--path` targeting HarvestGrid's Python 3.12 `site-packages`. Only the editable local `harvestgrid-domain` distribution was skipped; third-party packages were audited. Tests and builds remained on 3.12.12.

Advisory results are point-in-time and do not replace license, dataset/model provenance, secrets, infrastructure, or residential-data security reviews.

## External dependencies still blocked

Documented Ring access, weather, OpenCV/ONNX/runtime selections, model/dataset licenses, AWS services, and deployment tooling require future preflight. No dependency was added merely to imitate an unavailable integration.
