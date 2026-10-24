import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { FixtureCoordinatorApi } from "./api/fixtureClient";
import { HttpCoordinatorApi } from "./api/httpClient";
import "./styles.css";

const demoMode = import.meta.env.VITE_DEMO_MODE ?? "fixture";

const api =
  demoMode === "live"
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
    <App api={api} />
  </StrictMode>,
);
