# Demo media

`harvestgrid-demo.webm` is produced by `scripts/record-demo.ts`; see [`../demo.md`](../demo.md).

The file is not committed yet because it has to be recorded where a Playwright Chromium build can
be downloaded (`corepack pnpm exec playwright install chromium`). This sandbox has no access to the
browser CDN, so the recording step could not run here — the script, the scene plan, and the DOM
tests it depends on are all present and passing.

To produce it:

```bash
cd apps/web && corepack pnpm exec playwright install chromium
cd ../..
cd apps/web && corepack pnpm dev &          # http://localhost:5173/harvestgrid/
cd ../..
node --experimental-strip-types scripts/record-demo.ts
```

Then commit the resulting `docs/media/harvestgrid-demo.webm`. Keep it under ~10 MB; the four scenes
run about 40 seconds at 1280×900.
