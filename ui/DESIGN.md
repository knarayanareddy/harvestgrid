# HarvestGrid — Visual and Component Design

**Direction:** field operations ledger
**Tokens:** [tokens.json](./tokens.json)
**Screens:** [WIREFRAMES.md](./WIREFRAMES.md) and [browser wireframes](./wireframe.html)

## 1. Design thesis

HarvestGrid is a tool for making a cautious operational decision in imperfect light and connectivity. A paper-like neutral canvas, forest-green controls, and amber warning accents read as a working field ledger rather than a sustainability brand site. Charts are subordinate to the go/no-go conditions and worker-readiness review.

The generated mint/teal gradient, violet shadows, and pill-heavy landing-page direction are rejected. All colors are solid, status is redundant, and privacy boundaries are visible.

## 2. Responsive layout

- Maximum content width 1280 px; 12-column grid on desktop.
- Global header includes organization/site scope, connectivity, and `as of` time. The stale status never appears only in a disconnected footer.
- Field decision uses 5/7 split: conditions and uncertainty left, readiness/actions right. Map is a separate tab/section, not a background layer behind the decision.
- Mobile order: site/date → go/no-go rationale → blockers → evidence → readiness → map/details.
- Tables become definition lists at narrow widths; a horizontal scroll is acceptable only for explicit comparison tables with sticky row labels.
- Offline draft actions remain available; server-dependent actions explain why unavailable rather than disappearing.

## 3. Typography and operational data

- Public Sans/system sans for interface and narrative.
- IBM Plex Mono or tabular system numerals for UTC time, temperature, probability, and coordinates.
- Body 16 px, compact operational labels 14 px, title 36 px desktop/30 px mobile.
- Units are attached to every value (`6.2 °C`, `24 h`, `0.42 probability`). Times include timezone on first use.
- Coarsened coordinates show precision label; raw coordinates never appear in a worker-facing view.

## 4. Status and uncertainty grammar

| State | Treatment |
| --- | --- |
| Go | Green check + `Go` + exact satisfied conditions |
| No-go | Red stop icon + `No-go` + deterministic trigger |
| Conditional | Amber triangle + `Conditional` + required action and expiry |
| Review required | Blue review icon + `Review required` + confidence/reason |
| Stale | Clock + age + last current time; never a green go solely from cached data |
| Blocked | Closed lock + blocker and responsible role |
| Draft | Dashed border + `Offline draft` and signing status |

`Conditional` and `Review required` are different states; neither may collapse into a generic yellow warning.

## 5. Component contracts

| Component | Anatomy and behavior | Accessibility/content requirement |
| --- | --- | --- |
| `HG-SiteSelector` | Organization, permitted site, date, connection state | Only authorized sites appear; scope change preserves unsaved draft warning |
| `HG-DecisionSummary` | Status, rationale, evidence time, confidence, blockers, review action | Status text and icon precede all color/decorative treatment |
| `HG-ConditionRow` | Measurement/model, value/unit, threshold, age, confidence, source | Rows explain forecast vs observation and missing data |
| `HG-ReadinessChecklist` | Role groups, required worker acknowledgments, signature status, blockers | Checkboxes never imply a worker signed for someone else |
| `HG-WorkerAcknowledgment` | Assignment, briefing version/language, read confirmation, review decision, signed time | Supervisor review cannot masquerade as worker signature |
| `HG-AlertCard` | Hazard, applicable zone/time, severity, action, official source | Official alert is distinct from model inference; does not autoplay sound |
| `HG-OrchardMap` | Coarsened zones, status patterns, selected zone, text alternative | Every zone has keyboard/list alternative; raw location excluded from worker view |
| `HG-DataExport` | Role scope, fields, redactions, format, export action | Clearly states excluded signatures/PII/raw coordinates |
| `HG-OfflineQueue` | Draft name, local time, pending upload, conflict status, retry | Pending upload is never shown as server accepted |
| `HG-ErrorState` | What failed, preserved work, retry/support action | Technical identifiers collapse behind details; no blame language |

## 6. Map and chart rules

Map markers are 44 px minimum interactive regions even when visual icons are smaller. Shapes and labels supplement status color. A `List view` is always adjacent to the map and contains the same operational information. The base map never traps keyboard focus; `Escape` leaves it.

Trend charts show units, time range, uncertainty band, and source. No decorative yield/revenue charts. Frost probability alone cannot trigger a deterministic go/no-go without the defined gate.

## 7. Motion and icons

- 120–180 ms transitions; no ambient gradients, moving weather backgrounds, or pulsing alert beacons.
- Stale and urgent states remain static so they can be read outdoors.
- Simple line icons for weather, alert, checklist, lock, and map. Each has a text label in operational views.

## 8. Required visual states

Site scope: loading, authorized, permission denied, removed. Evidence: current, stale, missing, conflicting, model unavailable. Decision: go, no-go, conditional, review required, blocked, approved. Sync: offline draft, uploading, accepted, conflict, rejected. Acknowledgment: assigned, read, reviewed, revoked.

## 9. Implementation handoff

The decision status is rendered directly from the typed DecisionBrief, not inferred from CSS classes. Role/scope controls hide unauthorized data at the model/API layer as well as visually. A single status-definition map feeds summary, condition rows, map/list alternatives, and exports.
