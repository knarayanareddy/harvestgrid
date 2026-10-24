import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The browser never calls Ring or any internal service directly. All API traffic uses
// relative `/v1/...` paths; this dev proxy forwards them to the local FastAPI service.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 3000,
    strictPort: false,
    // Preview hosts are proxied (e.g. *.e2b.app); the dev server accepts them but still
    // refuses to bind anywhere except the explicit host above.
    allowedHosts: [".e2b.app", "localhost", "127.0.0.1"],
    proxy: {
      "/v1": {
        target: process.env.API_BASE_URL_INTERNAL ?? "http://127.0.0.1:8080",
        changeOrigin: false,
      },
    },
  },
  preview: { host: "0.0.0.0", port: 3000 },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["tests/**/*.test.{ts,tsx}"],
    css: false,
  },
});
