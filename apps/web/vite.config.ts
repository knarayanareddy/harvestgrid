import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The browser never calls Ring or any internal service directly. All API traffic uses
// relative `/v1/...` paths; this dev proxy forwards them to the local FastAPI service, which is
// `http://api:8000` inside docker-compose and `http://127.0.0.1:8000` by default.
//
// `base` is `/harvestgrid/` so the same build serves from https://<owner>.github.io/harvestgrid/.
// Set `VITE_BASE_PATH=/` for a root-hosted deployment (the compose container does exactly that).
const base = process.env.VITE_BASE_PATH ?? "/harvestgrid/";

export default defineConfig({
  base,
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    // Preview hosts are proxied (e.g. *.e2b.app); the dev server accepts them but still
    // refuses to bind anywhere except the explicit host above.
    allowedHosts: [".e2b.app", "localhost", "127.0.0.1"],
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
