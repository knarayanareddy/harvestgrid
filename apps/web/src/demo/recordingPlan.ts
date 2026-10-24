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
      "A tree is registered with a coarse geohash only. Camera collection needs its own consent — the demo refuses to continue without it.",
    panelTestId: "onboarding-panel",
    cardTitle: "Onboarding",
    actions: [
      { kind: "fill", testId: "region-input", value: "Delft south — fixture" },
      { kind: "check", testId: "camera-consent", checked: false },
      { kind: "wait", ms: 700 },
      { kind: "check", testId: "camera-consent", checked: true },
      { kind: "click", testId: "save-tree" },
      { kind: "wait", ms: 700 },
    ],
    expect: [{ testId: "collection-status", contains: "Consented" }],
  },
  {
    id: "privacy",
    title: "2 · Privacy polygon and mask boundary",
    narration:
      "Drag a vertex of the tree ROI, then widen the neighbour mask. Every change resets both approvals, and detections that fall mostly outside the allowed region are dropped.",
    panelTestId: "privacy-panel",
    cardTitle: "Privacy ROI Mask",
    actions: [
      { kind: "dragVertex", testId: "roi-vertex-0", dx: 34, dy: 26 },
      { kind: "wait", ms: 500 },
      { kind: "range", testId: "mask-width", value: "52" },
      { kind: "wait", ms: 500 },
      { kind: "click", testId: "approve-owner" },
      { kind: "click", testId: "approve-coordinator" },
      { kind: "click", testId: "simulate-webhook" },
      { kind: "wait", ms: 900 },
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
      "Warm the weather offset and the growing-degree-day curve advances, moving the stage and the harvest window. The projection stays experimental, with human review required.",
    panelTestId: "curve-panel",
    cardTitle: "Open-Meteo Ripening",
    actions: [
      { kind: "range", testId: "warmth-slider", value: "4" },
      { kind: "wait", ms: 800 },
      { kind: "range", testId: "warmth-slider", value: "-3" },
      { kind: "wait", ms: 800 },
      { kind: "range", testId: "warmth-slider", value: "0" },
      { kind: "wait", ms: 500 },
    ],
    expect: [{ testId: "dispatch-gate", contains: "Gate" }],
  },
  {
    id: "dispatch",
    title: "4 · Volunteer dispatch",
    narration:
      "Confirm readiness, assign two volunteers, and dispatch. The result shows per-recipient outcomes, masked phone numbers, and body digests — never message bodies.",
    panelTestId: "dispatch-panel",
    cardTitle: "Volunteer Dispatch",
    actions: [
      { kind: "check", testId: "assign-volunteer-a", checked: true },
      { kind: "check", testId: "assign-volunteer-b", checked: true },
      { kind: "check", testId: "assign-volunteer-c", checked: true },
      { kind: "check", testId: "readiness-confirm", checked: true },
      { kind: "wait", ms: 600 },
      { kind: "click", testId: "dispatch-button" },
      { kind: "wait", ms: 900 },
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
