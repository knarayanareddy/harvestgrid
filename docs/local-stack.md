# Local stack: docker compose

One command brings up Postgres 16 + PostGIS, Redis 7, the FastAPI coordination API, and the React
19 dashboard:

```bash
cp .env.example .env         # optional; every value has a fixture-safe default
docker compose up --build
```

| Service | Image / build | Host port | Purpose |
| --- | --- | --- | --- |
| `db` | `postgis/postgis:16-3.4` | 5432 | Postgres 16 with PostGIS, named volume `db-data` |
| `redis` | `redis:7-alpine` | 6379 | Cache only: no persistence, `allkeys-lru`, 256 MB cap |
| `api` | `services/api/Dockerfile` | 8000 | FastAPI coordination API, one worker |
| `web` | `apps/web/Dockerfile` (`target: dev`) | 5173 | Vite dev server for the dashboard |
| `web-prod` | `apps/web/Dockerfile` (`target: runtime`) | 8080 | Static bundle behind nginx, profile `static` |

```bash
open http://localhost:5173        # dashboard
open http://localhost:8000/docs   # OpenAPI UI
open http://localhost:8080        # only with: docker compose --profile static up --build web-prod
```

## What this stack actually does

The compose file is honest about the boundary, and the containers enforce it.

* **PostGIS and Redis are provisioned but unused.** `db` and `redis` start, pass their healthchecks,
  and the API receives `DATABASE_URL` and `REDIS_URL` on the compose network. The coordination API
  still keeps its state in process — there is no SQLAlchemy session, no migration, no Redis client.
  They are here so the topology, the env wiring, and `psql`/`redis-cli` inspection are real today,
  and so the persistence slice has somewhere to land. Nothing in this repository claims that
  harvests survive a restart.
* **Everything else is fixture.** The Ring adapter replays recorded webhook bodies over a fixture
  HMAC secret, the weather client returns recorded Open-Meteo shapes, and the SMS transports record
  instead of sending. The API labels responses with provenance, and the dashboard's banner reads
  `FIXTURE DATA`.
* **One API worker, on purpose.** Dispatch decisions live in memory; a second worker would fork
  coordinator state. `docker compose up --scale api=2` is not supported and would produce duplicate
  notices.
* **`DEMO_MODE` is checked at boot.** `python -m harvestgrid_api` refuses to start unless
  `DEMO_MODE=fixture` (`services/api/harvestgrid_api/__main__.py`), so a container that was pointed
  at a live provider by mistake fails loudly instead of sending synthetic SMS as if real.
* **The browser never names a container.** The dashboard calls relative `/v1/...` paths; Vite's dev
  proxy forwards them to `API_BASE_URL_INTERNAL` (`http://api:8000`). That keeps the same bundle
  working on `localhost`, behind the sandbox preview proxy, and under GitHub Pages.
* **Demo mode by default.** `VITE_DEMO_MODE=true` renders the in-browser fixture store, so
  `http://localhost:5173` is fully interactive even when the API container is stopped. Set
  `VITE_DEMO_MODE=false` to exercise the `/v1` proxy path against the API.

## Verifying without sending anything

```bash
docker compose config -q                 # schema and interpolation check
docker compose up -d db redis            # database + cache only
psql "postgresql://harvestgrid:harvestgrid_local_only@127.0.0.1:5432/harvestgrid" \
  -c 'select postgis_version();'
redis-cli -h 127.0.0.1 ping              # PONG
curl -fsS http://127.0.0.1:8000/healthz | jq
curl -fsS http://127.0.0.1:8000/openapi.json | jq '.info'
```

The API's health and OpenAPI routes are the same ones asserted by `tests/unit/test_api_app.py`;
the container adds a `HEALTHCHECK` on `/healthz` for both `api` and `nginx`.

## Why the image installs the dev dependency group

`pyproject.toml` keeps its runtime dependencies (`fastapi`, `uvicorn`, `httpx`, `pillow`) in
`[dependency-groups] dev`, because this repository ships importable libraries rather than a
published application. `services/api/Dockerfile` therefore runs `uv sync --frozen` instead of
`--no-dev` and adds nothing outside `uv.lock`. Moving those four entries into
`[project].dependencies` is the correct fix before the service is published; until then, image and
CI resolve the same lock file and cannot drift.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `api` restarts with exit code 2 | `DEMO_MODE` set to anything but `fixture` | unset it or set `DEMO_MODE=fixture` |
| `web` fails to start with `strictPort` | something else holds 5173 | `WEB_PORT=5174 docker compose up web` |
| Dashboard shows `FIXTURE DATA` but no data | expected in demo mode; the store is in the browser | interact with the panels, or rebuild with `VITE_DEMO_MODE=false` |
| nginx 404 on a deep link | `web-prod` without the SPA fallback | the shipped `nginx.conf` has `try_files ... /index.html`; check the mounted file |
| `port is already allocated` on 5432 | a local Postgres | `POSTGRES_PORT=55432 docker compose up` |

## Scope boundary

There is no CDK application, no cloud deploy, no migration, and no multi-tenant identity. `make
test-infra` now validates this compose file when a container runtime exists and otherwise explains
why it cannot; `make deploy` still refuses. See [`docs/validation.md`](./validation.md) for the
evidence boundary.
