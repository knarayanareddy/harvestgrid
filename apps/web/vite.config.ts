import { readFileSync, readdirSync, statSync } from "node:fs";
import { extname, join, resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vitest/config";

// The browser never calls Ring or any internal service directly. All API traffic uses
// relative `/v1/...` paths; this dev proxy forwards them to the local FastAPI service, which is
// `http://api:8000` inside docker-compose and `http://127.0.0.1:8000` by default.
//
// `base` is `/harvestgrid/` so the same build serves from https://<owner>.github.io/harvestgrid/.
// Set `VITE_BASE_PATH=/` for a root-hosted deployment (the compose container does exactly that).
const base = process.env.VITE_BASE_PATH ?? "/harvestgrid/";

const APP_ROOT = import.meta.dirname;
const MEDIA_SOURCE = resolve(APP_ROOT, "../../docs/media");

/**
 * Media recorded by `scripts/record-demo.ts`, mapped into the build.
 *
 * The walkthrough video is produced outside Vite (Playwright writes it, the recorder writes the
 * matching cue sheet), so it is not importable from `src/`. This plugin:
 *   - serves `docs/media/**` at `<base>media/**` in `vite dev` and `vite preview`,
 *   - emits the same files as `media/<name>` in `vite build`, so a static host serves them
 *     next to `assets/` and GitHub Pages gets them without a second deploy step.
 *
 * Only `*.webm` and `*.scenes.json` are published: the directory also holds a README and, while a
 * recording is running, a `.playwright-raw/` staging directory, none of which belong in a bundle.
 * A missing `docs/media` is not fatal — a checkout that has not recorded the video yet still
 * builds, and the Demo Video tab says the recording is absent instead of rendering a dead player.
 */
const PUBLISHED_MEDIA = /\.(webm|mp4|scenes\.json)$/;

function publishedMedia(): readonly string[] {
  try {
    return readdirSync(MEDIA_SOURCE)
      .filter((name) => PUBLISHED_MEDIA.test(name))
      .filter((name) => statSync(join(MEDIA_SOURCE, name)).isFile())
      .sort();
  } catch {
    return [];
  }
}

const MEDIA_TYPES: Readonly<Record<string, string>> = {
  ".webm": "video/webm",
  ".mp4": "video/mp4",
  ".json": "application/json; charset=utf-8",
};

function demoMedia(): Plugin {
  return {
    name: "harvestgrid-demo-media",
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const url = request.url ?? "";
        const mediaIndex = url.indexOf("/media/");
        if (mediaIndex === -1) return next();
        const name = decodeURIComponent(url.slice(mediaIndex + "/media/".length).split("?")[0] ?? "");
        if (!publishedMedia().includes(name)) return next();
        const body = readFileSync(join(MEDIA_SOURCE, name));
        response.setHeader(
          "Content-Type",
          MEDIA_TYPES[extname(name)] ?? "application/octet-stream",
        );
        response.setHeader("Content-Length", String(body.byteLength));
        response.setHeader("Cache-Control", "no-store");
        // Byte-range requests keep video scrubbing working in the browser.
        const range = request.headers.range;
        if (range) {
          const match = /bytes=(\d+)-(\d*)/.exec(range);
          if (match) {
            const start = Number(match[1]);
            const end = match[2] ? Math.min(Number(match[2]), body.byteLength - 1) : body.byteLength - 1;
            response.statusCode = 206;
            response.setHeader("Content-Range", `bytes ${start}-${end}/${body.byteLength}`);
            response.setHeader("Content-Length", String(end - start + 1));
            response.end(body.subarray(start, end + 1));
            return;
          }
        }
        response.end(body);
      });
    },
    generateBundle() {
      for (const name of publishedMedia()) {
        this.emitFile({
          type: "asset",
          fileName: `media/${name}`,
          source: readFileSync(join(MEDIA_SOURCE, name)),
        });
      }
    },
  };
}

export default defineConfig({
  base,
  plugins: [react(), demoMedia()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    // Preview hosts are proxied (e.g. *.e2b.app); the dev server accepts them but still
    // refuses to bind anywhere except the explicit host above.
    allowedHosts: [".e2b.app", "localhost", "127.0.0.1"],
    // The media plugin serves docs/media from outside the app root, and the dev server
    // refuses @fs access outside its allowed roots by default.
    fs: { allow: [APP_ROOT, MEDIA_SOURCE] },
    proxy: {
      "/v1": {
        target: process.env.API_BASE_URL_INTERNAL ?? "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  preview: {
    host: "0.0.0.0",
    port: 5173,
    allowedHosts: [".e2b.app", "localhost", "127.0.0.1"],
    proxy: {
      "/v1": {
        target: process.env.API_BASE_URL_INTERNAL ?? "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["tests/**/*.test.{ts,tsx}"],
    css: false,
  },
});
