# Coordination API

`services/api/harvestgrid_api/` is a FastAPI application that exposes the two services built
earlier — the weather-driven ripening scheduler (`INT-HG-005`, `FR-HG-007`, `FR-HG-008`) and the
volunteer notification dispatcher (`FR-HG-012`, `FR-HG-013`, `INT-HG-007`) — over HTTP.

It is a **fixture deployment**: the identity tokens, volunteer directory, weather source, SMS
transport, and job registry are synthetic. Every response carries `provenance: "fixture"` and
`demo_mode` is reported by `/healthz`, so no response can be mistaken for live data.

## Routes

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| `POST` | `/v1/forecasting/trigger` | coordinator | Run one scheduler tick for the caller's organization. `202` with a job record. |
| `GET` | `/v1/forecasting/{tree_id}/projection` | owner, coordinator, evaluator | Latest harvest-window projection for a tree. |
| `POST` | `/v1/notifications/dispatch` | coordinator | Render and send coordinator-approved pickup notices. |
| `GET` | `/healthz` | anyone | Liveness plus `provenance`, `demo_mode`, and the configured worker backend. |

The generated OpenAPI document is served at `/openapi.json` and `/docs`; a test asserts the
document keeps describing exactly these four paths, so a route rename cannot pass silently.

## Trigger: `POST /v1/forecasting/trigger`

The request body has no fields (`409`/`422` if extras are sent) — a caller cannot force a tree to
be reprocessed, because the tick already enforces poll intervals, consent gating, and failure
backoff.

The tick is submitted through a `JobRunner`:

- `InlineJobRunner` (the fixture default) runs the tick before responding and records the job.
- `BrokerJobRunner` raises `WorkerBackendUnavailable`, which becomes `503
  worker_backend_unavailable`. The API never pretends work was queued when no broker is
  configured.

Response (`202 Accepted`):

```json
{
  "job_id": "01KWGXF100P5NPE80DQ47D2FQY",
  "kind": "forecast_ripening_tick",
  "status": "completed",
  "submitted_at": "2026-07-02T08:00:00Z",
  "completed_at": "2026-07-02T08:00:00Z",
  "result_sha256": "b4e2ec19e52af002…",
  "provenance": "fixture",
  "result": {
    "trees_considered": 2,
    "trees_due": 2,
    "observations_new": 4,
    "projections_updated": 2,
    "adjustments": [],
    "failed": [],
    "skipped_gated": [],
    "review_recommended": []
  }
}
```

Only counters and tree identifiers leave the worker: no coordinates, weather rows, addresses, or
raw media.

## Projection: `GET /v1/forecasting/{tree_id}/projection`

Returns `404 tree_not_found` when the tree is not in the caller's organization **or** does not
exist — the two cases are byte-identical except for the echoed path and request id, so tree
existence never leaks across organizations. A tree with no projection yet returns `404
projection_not_available`, which is a different condition.

```json
{
  "tree_id": "tree-plum-01",
  "cultivar_id": "european_plum",
  "coarse_geohash": "u173z",
  "version": 1,
  "fingerprint": "4d2697220de0…",
  "provenance": "fixture",
  "review": {
    "required": true,
    "experimental": true,
    "confidence": "low",
    "prior_review_status": "fixture_unreviewed",
    "reason_codes": ["experimental_projection", "prior_not_approved"]
  },
  "harvest_window": {"available": true, "open_now": false, "start": "2026-08-24", "end": "2026-09-29"},
  "projection": {
    "cultivar_id": "european_plum",
    "stage": "fruit_set",
    "human_readiness_review_required": true,
    "internal_ripeness_inferred": false,
    "food_safety_inferred": false
  }
}
```

`open_now` is computed against the server clock, never the client's.

## Dispatch: `POST /v1/notifications/dispatch`

```json
{
  "glean_id": "glean-fixture-01",
  "glean_state": "confirmed",
  "owner_approval_current": true,
  "approved_at": "2026-07-02T07:00:00Z",
  "kind": "invitation",
  "crop_label": "plum",
  "coarse_area_label": "Delft south",
  "window_start": "2026-07-02T13:00:00Z",
  "window_end": "2026-07-02T16:00:00Z",
  "access_constraints": ["street parking only"],
  "safety_notes": ["ladder required"],
  "volunteer_ids": ["volunteer-a", "volunteer-b"]
}
```

Guarantees enforced by the route, not the caller:

- **No address, access note, or phone number is accepted in the request.** `phone_e164` and
  `access_note` are rejected as unknown fields; phones are resolved from the server-side
  directory by volunteer id.
- `approved_by` is the authenticated coordinator's own id, so a caller cannot claim someone else
  approved the dispatch.
- Dispatch requires current owner approval and a dispatchable glean state (`invited`/`confirmed`
  for invitations and reminders, `cancelled` only for cancellations); otherwise `403
  dispatch_not_authorized` and nothing is sent.
- Opt-out, suppression, daily caps (3 per volunteer), per-tick caps (50), and quiet hours
  (21:00–07:00 local) are applied per recipient and reported as `skipped`/`deferred` with a
  reason code.
- The glean's restricted address/access note is used **only** as a leak marker: if the rendered
  body contains it, the message is dropped with `message_contains_restricted_content`.
- The response carries digests, never bodies: `notices[].body_sha256`, plus `request_sha256` and
  `result_sha256`.

## Idempotency

Both `POST` routes require an `Idempotency-Key` header (8–128 characters from `A-Za-z0-9._:/+-`; `400
idempotency_key_required` / `idempotency_key_invalid`). Keys are scoped per organization, actor,
and route, and are retained for 24 hours:

- Repeating a request with the same key and the same body returns the stored response with
  `Idempotent-Replay: true` and does not poll the weather provider or send an SMS again.
- Reusing a key with a different body returns `409 idempotency_key_reused`.
- A *different* key does not bypass notification suppression: the ledger key
  `glean/state/recipient/template_version` still prevents texting a volunteer twice about one
  glean.

## Errors

All errors are RFC 9457 `application/problem+json` with a stable `code`, `instance`, and
`request_id` (a ULID that is also returned in the `X-Request-Id` response header); unexpected
failures collapse to a generic `500 internal_error` with no internals.

| Code | Status | Meaning |
| --- | --- | --- |
| `unauthenticated` | 401 | Missing or malformed bearer token. |
| `role_forbidden` | 403 | Authenticated actor lacks the required role. |
| `dispatch_not_authorized` | 403 | Approval evidence or glean state does not permit dispatch. |
| `tree_not_found` / `projection_not_available` | 404 | Unknown/foreign tree, or no projection yet. |
| `idempotency_key_reused` | 409 | Same key, different request. |
| `validation_failed` | 422 | Request schema violation; `fields[]` lists `path` and `code`. |
| `volunteer_not_found` | 422 | Volunteer ids outside the actor's directory, by index. |
| `notification_rejected` / `notice_rejected` | 422 | Dispatch or rendering refused. |
| `worker_backend_unavailable` | 503 | No broker-backed worker is configured. |

## Authentication (fixture)

Bearer tokens map to synthetic actors in `services/api/harvestgrid_api/container.py`:

| Token | Roles | Organization |
| --- | --- | --- |
| `fixture-coordinator-a` | coordinator | `org-fixture-a` |
| `fixture-owner-a` | owner | `org-fixture-a` |
| `fixture-evaluator-a` | evaluator | `org-fixture-a` |
| `fixture-volunteer-a` | volunteer | `org-fixture-a` |
| `fixture-coordinator-b` | coordinator | `org-fixture-b` |

The authorization logic is real (role checks, organization scoping); only the identity provider is
synthetic. `fixture-coordinator-b` exists so tests can prove cross-organization isolation.

## Running and testing

```bash
# application factory (used by ASGI servers and by tests)
uv run python -c "from harvestgrid_api import create_app; app = create_app()"

uv run pytest tests/unit/test_api_scheduler.py tests/unit/test_api_notifications.py -q
```

Tests drive the app through `packages/testkit/harvestgrid_testkit/asgi.py`, a small synchronous
ASGI driver. It exercises the real stack — routing, middleware, dependencies, exception handlers —
without `starlette.testclient`, whose anyio backend imports `ssl` and therefore fails on the
sandbox interpreter that has no `_ssl`. Every dependency and handler is a coroutine and blocking
service calls go through `asyncio.to_thread`, so the API needs no worker threads from the ASGI
framework and behaves identically under a real server.
