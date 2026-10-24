/**
 * Selectors the recorded demo drives.
 *
 * Both the recorder (`scripts/record-demo.ts`) and the DOM test import this, so a renamed
 * `data-testid` breaks a test instead of silently producing a broken video. Keeping the plan in
 * `src/` also means it is type-checked and covered by `pnpm test`.
 */

export type DemoAction =
  | { readonly kind: "click"; readonly testId: string }
  | { readonly kind: "check"; readonly testId: string; readonly checked: boolean }
  | { readonly kind: "fill"; readonly testId: string; readonly value: string }
  | { readonly kind: "range"; readonly testId: string; readonly value: string; readonly noMouse?: boolean }
  | { readonly kind: "dragVertex"; readonly testId: string; readonly dx: number; readonly dy: number }
  | { readonly kind: "wait"; readonly ms: number }
  | { readonly kind: "caption"; readonly text: string };

export interface DemoScene {
  readonly id: string;
  readonly title: string;
  readonly narration: string;
  /**
   * Panel the recorder scrolls into view before acting, and the card the dashboard's Demo Video tab
   * pairs with the cue. Without it a scene can drive a panel that is off-screen, and the recording
   * shows a scroll jump instead of the change it is narrating.
   */
  readonly panelTestId: string;
  /** Short label for the scene card in the dashboard's Demo Video tab. */
  readonly cardTitle: string;
  readonly actions: readonly DemoAction[];
  /** Assertions the recorder verifies before moving on. */
  readonly expect: readonly { readonly testId: string; readonly contains: string }[];
}

export const RECORDING_SCENES: readonly DemoScene[] = [
  {
    id: "onboarding",
    title: "1 · Tree onboarding and consent",
    narration:
      "Welcome to HarvestGrid, a decentralized neighborhood fruit rescue network built for the Ring smart home ecosystem. Across urban communities, millions of pounds of backyard fruit rot every season while local pantries face fresh produce shortages. HarvestGrid begins with privacy-preserving tree onboarding: trees are registered with coarse geohashes only, keeping exact street addresses anonymous. Furthermore, camera data collection strictly requires explicit homeowner consent; without it, ingestion is blocked. Once consented, the tree is securely saved to the edge registry.",
    panelTestId: "onboarding-panel",
    cardTitle: "Onboarding",
    actions: [
      { kind: "wait", ms: 14000 },
      { kind: "fill", testId: "region-input", value: "Delft south — fixture" },
      { kind: "wait", ms: 4000 },
      { kind: "check", testId: "camera-consent", checked: false },
      { kind: "wait", ms: 5000 },
      { kind: "check", testId: "camera-consent", checked: true },
      { kind: "wait", ms: 2000 },
      { kind: "click", testId: "save-tree" },
      { kind: "wait", ms: 8000 },
    ],
    expect: [{ testId: "collection-status", contains: "Consented" }],
  },
  {
    id: "privacy",
    title: "2 · Privacy polygon and mask boundary",
    narration:
      "Scene two: privacy boundaries. To protect neighbor privacy, homeowners draw an adjustable polygon mask directly over their Ring camera view. Any area outside the tree canopy—including neighboring windows, sidewalks, or backyards—is permanently redacted. Notice that dragging any vertex or adjusting the mask slider immediately invalidates existing approvals, preventing accidental surveillance drift. The homeowner and coordinator re-approve the boundary, and a signed Ring HMAC webhook is replayed and verified on-device.",
    panelTestId: "privacy-panel",
    cardTitle: "Privacy ROI Mask",
    actions: [
      { kind: "wait", ms: 5000 },
      { kind: "dragVertex", testId: "roi-vertex-0", dx: 34, dy: 26 },
      { kind: "wait", ms: 7000 },
      { kind: "range", testId: "mask-width", value: "52" },
      { kind: "wait", ms: 7000 },
      { kind: "click", testId: "approve-owner" },
      { kind: "wait", ms: 2000 },
      { kind: "click", testId: "approve-coordinator" },
      { kind: "wait", ms: 4000 },
      { kind: "click", testId: "simulate-webhook" },
      { kind: "wait", ms: 8000 },
    ],
    expect: [
      { testId: "view-approvals", contains: "owner ✔" },
      { testId: "webhook-summary", contains: "HMAC verified" },
    ],
  },
  {
    id: "ripening",
    title: "3 · Open-Meteo ripening curve",
    narration:
      "Scene three: autonomous ripening intelligence. HarvestGrid couples on-device ONNX vision models with Open-Meteo microclimate weather forecasts. By tracking cumulative growing-degree-days against biological base temperatures, the system models fruit maturation. As temperature offsets fluctuate from warm anomalies to cooler cold snaps, the ripening curve dynamically recalculates the optimal harvest window. Crucially, the gleaning dispatch gate remains locked with human review required, preventing premature harvesting.",
    panelTestId: "curve-panel",
    cardTitle: "Open-Meteo Ripening",
    actions: [
      { kind: "wait", ms: 7000 },
      { kind: "range", testId: "warmth-slider", value: "4" },
      { kind: "wait", ms: 6000 },
      { kind: "range", testId: "warmth-slider", value: "-3" },
      { kind: "wait", ms: 5000 },
      { kind: "range", testId: "warmth-slider", value: "0" },
      { kind: "wait", ms: 14000 },
    ],
    expect: [{ testId: "dispatch-gate", contains: "Gate" }],
  },
  {
    id: "dispatch",
    title: "4 · Volunteer dispatch",
    narration:
      "Scene four: volunteer gleaning dispatch. Once harvest conditions peak and the coordinator confirms field readiness, nearby registered volunteers are assigned to the harvest site. Dispatch messages enforce strict privacy boundaries: volunteer telephone numbers are masked, message bodies are hashed to cryptographic digests, and SMS opt-in status is verified. Upon dispatch, donation receipts are cryptographically committed to a local ledger for neighborhood food distribution, closing the loop from backyard tree to neighborhood food pantry.",
    panelTestId: "dispatch-panel",
    cardTitle: "Volunteer Dispatch",
    actions: [
      { kind: "wait", ms: 5000 },
      { kind: "check", testId: "assign-volunteer-a", checked: true },
      { kind: "wait", ms: 2000 },
      { kind: "check", testId: "assign-volunteer-b", checked: true },
      { kind: "wait", ms: 2000 },
      { kind: "check", testId: "assign-volunteer-c", checked: true },
      { kind: "wait", ms: 5000 },
      { kind: "check", testId: "readiness-confirm", checked: true },
      { kind: "wait", ms: 3000 },
      { kind: "click", testId: "dispatch-button" },
      { kind: "wait", ms: 14000 },
    ],
    expect: [
      { testId: "dispatch-summary", contains: "sent" },
      { testId: "outcome-volunteer-c", contains: "sms opt in required" },
    ],
  },
];

function actionTestId(action: DemoAction): string | null {
  switch (action.kind) {
    case "click":
    case "check":
    case "fill":
    case "range":
    case "dragVertex":
      return action.testId;
    case "wait":
    case "caption":
      return null;
  }
}

/**
 * Controls the recorder clicks, ticks, fills, or drags. They must exist as soon as the page
 * loads, so a DOM test can assert every one of them before the video is recorded.
 */
export const RECORDING_CONTROL_IDS: readonly string[] = Array.from(
  new Set([
    "demo-workspace",
    "demo-banner",
    ...RECORDING_SCENES.flatMap((scene) =>
      scene.actions
        .map(actionTestId)
        .filter((testId): testId is string => testId !== null),
    ),
  ]),
);

/** Assertions the recorder makes after acting; they only exist once a scenario has run. */
export const RECORDING_EXPECTATION_IDS: readonly string[] = Array.from(
  new Set(RECORDING_SCENES.flatMap((scene) => scene.expect.map((item) => item.testId))),
);
