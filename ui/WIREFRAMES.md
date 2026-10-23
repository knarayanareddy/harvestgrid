# HarvestGrid — Low-Fidelity Wireframes

Open [wireframe.html](./wireframe.html). The four responsive artboards model field-decision, readiness, location-privacy, and offline states. Values are fictional fixtures, not current weather, official alerts, or real farm data.

## Screen inventory

| Anchor | Screen | Purpose | Primary path |
| --- | --- | --- | --- |
| `#decision` | Field decision | Understand go/no-go status, evidence, uncertainty, and blockers | Resolve conditions and request review |
| `#readiness` | Worker readiness | Track assignment-specific briefing and signed review | Record permitted acknowledgment |
| `#locations` | Orchard map and list | Inspect coarsened operational zones with equivalent non-map access | Select zone or use list |
| `#offline` | Offline draft queue | Preserve field work and distinguish local from server-accepted state | Retry sync or resolve conflict |

## 1. Field decision

**Composition:** site/date and freshness header, large conditional status, deterministic blockers, observed/model conditions, readiness summary, review action.

**Fictional content:** `Conditional` because wind is within threshold but an official alert and one readiness review still block approval.

**Annotations**

- Model confidence does not turn into a go decision.
- Each condition names its source and age; missing evidence stays unknown.
- Approval action remains unavailable while named blockers exist.

**State variants:** go; no-go; conditional; review required; blocked; stale; missing model; permission denied; approved.

## 2. Worker readiness

**Composition:** assignment and briefing version, role-grouped checklist, individual acknowledgment rows, supervisor-review status, blockers.

**Fictional content:** two picking teams; one worker has read the briefing but supervisor review is outstanding.

**Annotations**

- `Read` and `Reviewed` are separate events.
- No supervisor can sign on behalf of a worker through this UI.
- Language/briefing version is visible next to the acknowledgment.

**State variants:** assigned; read; reviewed; revoked; reassigned; offline signed draft; conflict; expired briefing version.

## 3. Orchard map and list

**Composition:** coarsened zone grid, adjacent list view, privacy note, selected-zone detail and operational status.

**Fictional content:** zones A1–B3 with no raw coordinates; worker-facing export excludes signatures and precise location.

**Annotations**

- Map and list expose equivalent status and actions.
- Zone status uses label/pattern as well as color.
- Precision disclosure remains in the screen, not only in a policy document.

**State variants:** map unavailable; list fallback; permitted zone scope; location hidden; stale zone; no assigned zones; export preview.

## 4. Offline draft queue

**Composition:** offline banner, locally saved drafts, pending-upload state, conflict explanation, retry and preserve-copy actions.

**Fictional content:** a readiness-review draft saved at 06:42 local time, not yet accepted by the server.

**Annotations**

- `Saved on device` is never labeled `Submitted`.
- Retrying does not discard the local draft.
- Conflict resolution shows both versions and requires an authorized choice.

**State variants:** offline draft; uploading; accepted; rejected; conflict; eviction warning; storage full; reconnecting.

## Flow contract

`Authorized site/date → Evidence freshness → Deterministic decision → Worker readiness → Human approval → Assignment`

Offline work follows `Local draft → Reconnect → Server acceptance OR conflict resolution`. Browser artboards do not fetch weather, track location, collect signatures, or submit reviews.
