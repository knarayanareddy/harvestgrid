# Interactive demo and recorded walkthrough

Two deliverables share one scene plan, so the video and the clickable page cannot drift apart:

| Deliverable | Location | What it is |
| --- | --- | --- |
| Standalone demo | `VITE_DEMO_MODE=true` build → GitHub Pages | A static page with an in-browser fixture store: no backend, no network calls, fully clickable |
| Recorded walkthrough | `docs/media/harvestgrid-demo.webm` + `.scenes.json` | Playwright video of the same four scenes, driven by `scripts/record-demo.ts`, embedded in the console's **Demo Video** tab |
| Scene plan | `apps/web/src/demo/recordingPlan.ts` | Test ids, actions, narration, and post-condition assertions for every scene |

Because the recorder imports the plan from `src/`, a renamed `data-testid` fails
`apps/web/tests/demo.test.tsx` instead of silently producing a broken video. On top of that,
`apps/web/tests/demoReplay.test.tsx` replays every scene against the rendered page with jsdom
events — clicking, ticking, filling, dragging a vertex, and then asserting the same post-conditions
the recorder asserts — so a plan that cannot be executed fails `pnpm test` on any machine, browser or
not.

## The four scenes

1. **Tree onboarding and consent** — register a tree in a coarse geohash area. Camera collection
   stays `not_consented` until the owner grants it, and the save button refuses to advance without
   consent.
2. **Privacy polygon and mask boundary** — drag a tree-ROI vertex, widen the neighbour mask, and
   watch every approval reset. Then approve as owner and as coordinator and replay a signed Ring
   webhook: detections that are mostly outside the allowed region are dropped before the overlay is
   drawn.
3. **Open-Meteo ripening curve** — move the warmth slider and the growing-degree-day curve, stage,
   and harvest window advance. The projection remains `experimental`, `fixture_unreviewed`, with
   `humanReadinessReviewRequired: true` and `internalRipenessInferred: false`.
4. **Volunteer dispatch** — confirm human readiness, assign volunteers, and dispatch. The result
   list shows one masked phone number and body digest per recipient and no message bodies; the
   opted-out volunteer yields `sms_opt_in_required`, and a repeat dispatch is suppressed as a
   duplicate.

## The Demo Video tab

The coordinator console (`apps/web/src/App.tsx`) has four tabs — **Map View**, **Yield Forecasts**,
**Gleaning Dispatch**, and **Demo Video**. The last one embeds the recording above with four
synchronized scene cards:

| Card | Scene id | What the video shows |
| --- | --- | --- |
| Onboarding | `onboarding` | Tree registration, consent-gated collection |
| Privacy ROI Mask | `privacy` | Vertex drag, mask widening, approvals reset, webhook replay |
| Open-Meteo Ripening | `ripening` | Warmth offset moving the GDD curve and harvest window |
| Volunteer Dispatch | `dispatch` | Readiness confirmation, assignments, per-recipient outcomes |

Each card carries its start time, the narration text, and the checks the recorder asserted for that
scene. If a build has no recording, the tab says so and keeps the cards as a transcript rather than
rendering a player that cannot play.

## Run the demo locally

```bash
cd apps/web
corepack pnpm install --frozen-lockfile
corepack pnpm dev                 # http://localhost:5173/harvestgrid/
```

`http://localhost:5173` also works: the dev server redirects to the base path. To reproduce exactly
what Pages serves:

```bash
cd apps/web
VITE_DEMO_MODE=true VITE_BASE_PATH=/harvestgrid/ corepack pnpm build
corepack pnpm preview             # http://localhost:5173/harvestgrid/
```

Everything you can do in the video, you can do in the page — the sliders, the polygon vertices, the
mask handles, the consents, the dispatch button. Nothing is prerendered and nothing is stubbed at
the DOM level.

## Record the video

```bash
cd apps/web && corepack pnpm exec playwright install chromium   # once; downloads ~150 MB
cd ../..                                                        # back to the repo root
node --experimental-strip-types scripts/record-demo.ts --base-url http://127.0.0.1:5173/harvestgrid/
# → docs/media/harvestgrid-demo.webm
```

Flags: `--base-url <url>`, `--output <path>`, `--headed`, `--selftest`.

* The recorder waits for `[data-testid="demo-workspace"]`, replays each scene's actions, and only
  then asserts the scene's `expect` entries (`webhook-summary` contains `HMAC verified`,
  `outcome-volunteer-c` contains `sms opt in required`, and so on). A missing or unexpected value
  fails the run — the video is either correct or absent, never halfway.
* `--selftest` checks the scene plan (every scene drives at least one control and asserts at least
  one outcome), fetches the served page, and reports whether a browser is installed. It needs no
  browser, so it runs anywhere; `make demo-selftest` is the wrapper.
* Playwright records silent VP8 WebM. Narration lives in the scene plan as `narration` strings so a
  voice-over can be added later without re-timing the UI; the shipped video has no audio track.

### Where the recording lives

`docs/media/harvestgrid-demo.webm` (2.4 MB, VP8, 1280×900, ~18 s, silent) and its cue sheet are
committed. `apps/web/vite.config.ts` maps `docs/media/` into the app at `<base>media/` for the dev
server, `vite preview`, and `vite build`, so the same files serve the console's Demo Video tab and
the GitHub Pages deployment; nothing is copied by hand.

The cue sheet — `harvestgrid-demo.scenes.json` — is what keeps the scene cards honest. The recorder
writes each scene's `startMs`/`endMs`, its card label from the plan, its narration, and the
assertions it verified. Playback drives the card highlight from those timings, and selecting a card
seeks the video, so the highlight cannot drift from the picture. `apps/web/tests/demoArtifacts.test.ts`
reads both files from disk and fails if they are absent, truncated (not a real EBML container), or
describe scenes that no longer match `recordingPlan.ts` — the one failure mode a unit test would
otherwise miss, since a stale video still renders perfectly.

The recording in this repository was captured with a `chrome-headless-shell` build from the npm
registry (`--executable-path`, `--launch-arg`) because the Playwright browser CDN was unreachable in
the authoring environment. Playwright's video encoder is still required; see
[`media/README.md`](./media/README.md) for the exact command.

Frame-checked after the last recording (1280×900, VP8, 18.1 s, 25 fps, no audio): the onboarding
scene shows consent gating `Blocked — both consents are required` flipping to `Consented`, the
privacy scene shows a dragged vertex, both approvals resetting to `✖`, and the webhook summary
reporting `10 kept · 6 excluded`, the ripening scene shows the GDD total moving with the warmth
slider, and the dispatch scene ends with `2 sent · 1 skipped` and `sms opt in required` for the
opted-out volunteer.

## Publish to GitHub Pages

`.github/workflows/pages.yml` builds the demo bundle and deploys `apps/web/dist` on every push to
`main` that touches `apps/web/`.

```text
https://knarayanareddy.github.io/harvestgrid/
```

Before the first deploy, set **Settings → Pages → Build and deployment → Source** to **GitHub
Actions**. The workflow:

1. installs with the committed `apps/web/pnpm-lock.yaml` (`--frozen-lockfile`),
2. runs `pnpm typecheck` and `pnpm test`,
3. builds with `VITE_DEMO_MODE=true VITE_BASE_PATH=/harvestgrid/`,
4. fails if `dist/index.html` lacks `/harvestgrid/assets/`, contains a root-relative asset URL, or
   the fixture banner is missing from the bundle,
5. uploads the artifact with `actions/upload-pages-artifact` and deploys with
   `actions/deploy-pages` under the `github-pages` environment.

`base: '/harvestgrid/'` is the default in `apps/web/vite.config.ts`, so a local `pnpm build` matches
the deployed page; compose overrides it with `VITE_BASE_PATH=/` for root hosting.

## What the demo deliberately does not do

* No map tiles, no geocoding, no device frames from a real camera — the ROI editor draws on a
  synthetic frame so no residential imagery exists in the repository.
* No live Open-Meteo request, no Twilio/SNS call, no persistence: reloading the page resets state.
* No address reveal, no SMS body, no unmasked phone number, and no claim that a visual estimate
  proves internal ripeness or food safety.
