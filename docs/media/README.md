# Demo media

Two generated files, produced together by `scripts/record-demo.ts`; see [`../demo.md`](../demo.md).

| File | What it is |
| --- | --- |
| `harvestgrid-demo.webm` | VP8 recording of the fixture demo, 1280×900, ~18 s, silent |
| `harvestgrid-demo.scenes.json` | Cue sheet: each scene's `startMs`/`endMs`, title, card label, narration, and the assertions the recorder checked before moving on |

Both are committed, because the coordinator console's **Demo Video** tab embeds them and GitHub
Pages serves them at `/harvestgrid/media/`. `apps/web/vite.config.ts` maps `docs/media/` into the
build (dev server, preview, and `dist/media/`), and `apps/web/tests/demoArtifacts.test.ts` fails if
the files are missing, truncated, or out of step with `apps/web/src/demo/recordingPlan.ts`.

## Re-recording

Re-record whenever a demo panel, a test id, or a scene's narration changes: the cue sheet is
regenerated with the video, and `demoArtifacts.test.ts` compares it with the plan.

```bash
cd apps/web && corepack pnpm dev            # terminal 1: http://localhost:5173/harvestgrid/
node --experimental-strip-types scripts/record-demo.ts   # terminal 2, from the repo root
```

Requires a Playwright browser: `pnpm --dir apps/web exec playwright install chromium`.

### Where the browser CDN is unreachable

The video in this repository was recorded with a `chrome-headless-shell` build fetched from the npm
registry, which needs no browser-CDN access. Playwright accepts any recent binary:

```bash
export LD_LIBRARY_PATH=/path/to/nss-libs       # headless-shell needs libnss3/libnspr4
node --experimental-strip-types scripts/record-demo.ts \
  --executable-path /path/to/chrome-headless-shell \
  --launch-arg=--no-sandbox --launch-arg=--no-zygote --launch-arg=--disable-dev-shm-usage
```

`--executable-path` and `--launch-arg` also read from `PLAYWRIGHT_EXECUTABLE_PATH` and
`PLAYWRIGHT_LAUNCH_ARGS`. Playwright's own video encoder (`ffmpeg-linux` in the Playwright cache) is
still required; without it the run fails at context creation rather than writing a partial file.
