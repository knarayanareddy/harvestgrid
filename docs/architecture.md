# HarvestGrid foundation architecture

## Implemented boundaries

```text
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
- Contract tests compare the schema copy byte-for-byte and exercise the fictional example. The walking path validates a contract only.

## Deliberately absent

No Ring client, weather client, vision pipeline, forecast model, cultivar prior, durable persistence, API/web application, owner approval workflow, invitation/address service, notification, or deployment unit exists. The onboarding store is an in-memory fixture, not a database. Empty directories reserve future boundaries only.

A future operational pipeline must keep observation/model proposals separate from owner/coordinator authority and must preserve consent and scoped-address checks outside the ranking function.

## Independence and output limits

HarvestGrid has no shared production runtime/database. The fixture remains experimental and human-review-required. It does not infer internal ripeness or food safety, collect media, produce an operational window, invite volunteers, or reveal an address. Registered trees carry coarse geohash cells only and report `awaiting_camera_view_approval`.
