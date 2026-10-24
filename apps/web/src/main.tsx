import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { FixtureCoordinatorApi } from "./api/fixtureClient";
import { HttpCoordinatorApi } from "./api/httpClient";
import { DemoWorkspace } from "./demo/DemoWorkspace";
import "./styles.css";

/**
 * Build modes.
 *
 * - `VITE_DEMO_MODE=live` — talk to the FastAPI service over relative `/v1` paths.
 * - `VITE_DEMO_MODE=fixture` (default) — the original fixture adapter, no network.
 * - `VITE_DEMO_MODE=true` — the static, self-contained judge demo published to GitHub Pages:
 *   the interactive workspace (onboarding, privacy masks, ripening curve, mock dispatch) *plus*
 *   the coordinator board, both backed by the in-browser fixture handler. Nothing calls out.
 */
const mode = import.meta.env.VITE_DEMO_MODE ?? "fixture";
const interactive = mode === "true";

const api =
  mode === "live"
    ? new HttpCoordinatorApi({
        actorId: "coordinator-live",
        roles: ["coordinator"],
        organizationId: "org-live",
      })
    : new FixtureCoordinatorApi();

const container = document.getElementById("root");
if (!container) throw new Error("Missing #root element");

createRoot(container).render(
  <StrictMode>
    {interactive ? (
      <>
        <DemoWorkspace />
        <hr className="demo-divider" />
        <App api={api} />
      </>
    ) : (
      <App api={api} />
    )}
  </StrictMode>,
);
