# HarvestGrid foundation architecture

## Implemented boundaries

```text
Ring webhook ──► HMAC + raw-body + dedupe ──► media job queue (fixtures only)
     │                                              │
     ▼                                              ▼
camera-view v1 document ──► ROI/mask geometry ──► feature worker (features_v1)
 (owner + coordinator approval)        │                │
                                       ▼                ▼
                        coarse geohash ──► Open-Meteo features (1-decimal point)

fictional harvest-window document ──► canonical schema + semantic checks
                                             │
                                             ▼
                           experimental/review/capability result

candidate constraints + ranking features ──► hard filters
                                             │
                                             ▼
                           deterministic score and stable tie order

registration request ──► typed validation (cultivar, coarse geohash, bloom, consents)
                              │
                              ▼
                 compare-and-set store port  ──► in-memory fixture adapter
                              │
                              ▼
      tree projection (consent status, collection gate, experiment flag)
```

- `packages/domain/harvestgrid_domain/forecast.py` validates interval ordering/containment, count/weight consistency, visible-only capability limits, seasonal-baseline status, and human-review prerequisites. It does not generate a forecast.
- `packages/domain/harvestgrid_domain/onboarding.py` registers organizations, owners/members, trees, cultivars, coarse locations, and explicit consents. It rejects exact or over-precise locations, missing/stale/wrong-actor consents, cohort cultivar changes, and conflicting replays through a stable error-code catalogue. Registration cannot approve a camera view, so collection stays blocked.
- `onboarding_adapters.py` is the fixture boundary: a compare-and-set in-memory store and a JSON cultivar catalog reading `content/cultivars/`. The draft cultivar content keeps forecasts experimental; the catalog invents no priors.
- `packages/matching/harvestgrid_domain_matching/engine.py` makes approvals, training, equipment, availability, capacity/alternative, safety, address policy, and waiver requirements hard filters. Ineligible candidates receive no score.
- Ranking uses only declared numeric features and a SHA-256 tie-breaker over `glean_id:volunteer_id`; protected traits and address/media data have no input field.
- `packages/domain/harvestgrid_domain/roi.py` owns tree ROI and privacy masking. A `CameraViewRequest` becomes an immutable versioned view only when the owner approves it *and* a coordinator approves it; either rejection, a stale version, or overlapping non-ROI geometry blocks collection. Masks are recorded as typed regions (neighbour property, public sidewalk, street, other unrelated) and are subtracted from the ROI **before** any pixel is sampled, so unrelated people, vehicles, and sidewalks cannot enter a feature row.
- `services/ring-adapter/harvestgrid_ring_adapter/webhooks.py` is the Ring ingress boundary: method/path and content-type checks, a 64 KB body cap, HMAC-SHA256 over `sha256(timestamp + "." + raw_body)` with a ±5-minute clock window, JSON parsing, a token bucket, event-id and payload-hash deduplication, forbidden-field rejection, and a bounded media-job queue. The acknowledgement deadline is five seconds; processing is asynchronous. `server.py` is a stdlib HTTP shell (`/healthz`, `POST`, `405`/`Allow`, `429`/`Retry-After`) with no Ring credentials in it.
- `packages/weather-client/harvestgrid_weather/open_meteo.py` requests temperature and precipitation features for geohash precision 4-5 only. Coordinates are truncated to one decimal place, the payload never contains access notes or exact coordinates, and provenance records `exact_coordinates_included: false`. Archive and forecast ranges are bounded, transient failures are retried with a bounded backoff, and provider rate limits are surfaced rather than hidden.
- `packages/vision/harvestgrid_vision/` extracts the 16-column `features_v1` contract from masked luminance/saturation grids. Registration, luminance, contrast, ROI-coverage, and quality gates reject unsuitable frames with reason codes instead of emitting a zero count; missing values stay `-1.0`. `OnnxDetector` fails closed when no artifact is loaded, so no fixture result can masquerade as model output.
- `apps/web/` is the coordinator dashboard. It talks to a `CoordinatorApi` port with no address, raw-media, or Ring method exposed; the default `FixtureCoordinatorApi` labels every screen `FIXTURE DATA`. Matching re-applies the same hard filters and weights (pinned by a contract test against `engine.py`), receipts are validated locally before the request leaves the browser and export only contract-allowed fields, and access-reveal previews are capped at two hours or the assignment expiry.
- Contract tests compare schemas byte-for-byte and exercise fictional examples. The walking path validates a contract only.

## Deliberately absent

No Ring device client (OAuth, capability discovery, media download), live weather request, trained vision model, forecast model, cultivar prior, durable persistence, backend API server for the web app, invitation/address service, notification, or deployment unit exists. The onboarding store, webhook store, job queue, and weather cache are in-memory fixtures, not databases or queues. `OnnxDetector` deliberately raises `InferenceBackendUnavailable`; the heuristic detector is labelled `heuristic_fixture` and produces no model-quality claim. The dashboard's live mode requires a backend that this repository does not yet ship, so its default is fixture mode.

A future operational pipeline must keep observation/model proposals separate from owner/coordinator authority and must preserve consent and scoped-address checks outside the ranking function.

## Independence and output limits

HarvestGrid has no shared production runtime/database. The fixtures remain experimental and human-review-required. They do not infer internal ripeness or food safety, collect real media, produce an operational window, invite volunteers, or reveal an address. Registered trees carry coarse geohash cells only and report `awaiting_camera_view_approval`; the web app's privacy preview shows a schedule, never a location. Every observation and receipt projection sets `raw_media_included`, `exact_address_included`, and the inference flags to `false`.
