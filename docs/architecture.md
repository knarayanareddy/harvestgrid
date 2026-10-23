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
```

- `packages/domain/harvestgrid_domain/forecast.py` validates interval ordering/containment, count/weight consistency, visible-only capability limits, seasonal-baseline status, and human-review prerequisites. It does not generate a forecast.
- `packages/matching/harvestgrid_domain_matching/engine.py` makes approvals, training, equipment, availability, capacity/alternative, safety, address policy, and waiver requirements hard filters. Ineligible candidates receive no score.
- Ranking uses only declared numeric features and a SHA-256 tie-breaker over `glean_id:volunteer_id`; protected traits and address/media data have no input field.
- Contract tests compare the schema copy byte-for-byte and exercise the fictional example. The walking path validates a contract only.

## Deliberately absent

No Ring client, weather client, vision pipeline, forecast model, cultivar prior, persistence, owner approval workflow, invitation/address service, notification, application, or deployment unit exists. Empty directories reserve future boundaries only.

A future operational pipeline must keep observation/model proposals separate from owner/coordinator authority and must preserve consent and scoped-address checks outside the ranking function.

## Independence and output limits

HarvestGrid has no shared production runtime/database. The fixture remains experimental and human-review-required. It does not infer internal ripeness or food safety, collect media, produce an operational window, invite volunteers, or reveal an address.
