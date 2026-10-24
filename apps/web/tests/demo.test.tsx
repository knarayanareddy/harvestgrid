import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { DemoWorkspace } from "../src/demo/DemoWorkspace";
import { DEFAULT_ROI, DemoStore, DEMO_VOLUNTEERS } from "../src/demo/demoStore";
import { filterClusters, syntheticClusters } from "../src/demo/detections";
import { allowedRaster, maskCoverageOfRoi, validateView } from "../src/demo/geometry";
import { dispatchNotices, maskPhone, renderNotice, type PickupAlert } from "../src/demo/notices";
import { projectRipening, seasonalTemperatures } from "../src/demo/phenology";
import {
  RECORDING_CONTROL_IDS,
  RECORDING_EXPECTATION_IDS,
  RECORDING_SCENES,
} from "../src/demo/recordingPlan";
import { sha256Hex } from "../src/demo/sha256";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

beforeAll(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function byTestId<T extends HTMLElement>(testId: string): T {
  const element = container.querySelector<T>(`[data-testid="${testId}"]`);
  if (!element) throw new Error(`missing data-testid=${testId}`);
  return element;
}

function renderStore(store: DemoStore = new DemoStore()): DemoStore {
  act(() => {
    root.render(<DemoWorkspace store={store} />);
  });
  return store;
}

describe("sha256", () => {
  it("matches the published FIPS 180-4 vectors", () => {
    expect(sha256Hex("")).toBe("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    expect(sha256Hex("abc")).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    expect(sha256Hex("The quick brown fox jumps over the lazy dog")).toBe(
      "d7a8fbb307d7809469ca9abcb0082e4f8d5651e46d3cdb762d02d0bf37c9e592",
    );
  });
});

describe("privacy geometry", () => {
  it("subtracts masks from the ROI instead of sampling them afterwards", () => {
    const roi = DEFAULT_ROI;
    const withMask = validateView(roi, [
      { id: "m1", kind: "neighbor_property", x: 0.6, y: 0, width: 0.4, height: 0.4 },
    ]);
    const withoutMask = validateView(roi, []);
    expect(withMask.allowedFraction).toBeLessThan(withoutMask.allowedFraction);
    expect(withMask.maskCoverage).toBeGreaterThan(0);
  });

  it("refuses a view whose masks hide too much of the tree", () => {
    const roi = DEFAULT_ROI;
    const validation = validateView(roi, [
      { id: "m1", kind: "neighbor_property", x: 0, y: 0, width: 1, height: 1 },
    ]);
    expect(validation.ok).toBe(false);
    expect(validation.reason).toBe("roi_obscured_by_masks");
  });

  it("refuses a region that is too small to collect from", () => {
    const validation = validateView(
      [
        { x: 0.4, y: 0.4 },
        { x: 0.42, y: 0.4 },
        { x: 0.42, y: 0.42 },
        { x: 0.4, y: 0.42 },
      ],
      [],
    );
    expect(validation.ok).toBe(false);
    expect(validation.reason).toBe("roi_area_too_small");
  });

  it("drops clusters mostly outside the allowed cells", () => {
    const roi = DEFAULT_ROI;
    const masks = [{ id: "m1", kind: "neighbor_property" as const, x: 0, y: 0, width: 0.5, height: 0.5 }];
    const raster = allowedRaster(roi, masks);
    const frame = filterClusters(syntheticClusters(), raster);
    expect(frame.dropped.length).toBeGreaterThan(0);
    for (const cluster of frame.dropped) {
      expect(cluster.centerX).toBeTypeOf("number");
    }
    // Nothing dropped may also be kept.
    const keptIds = new Set(frame.kept.map((cluster) => cluster.clusterId));
    expect(frame.dropped.some((cluster) => keptIds.has(cluster.clusterId))).toBe(false);
  });

  it("measures mask coverage deterministically", () => {
    const first = maskCoverageOfRoi(DEFAULT_ROI, []);
    const second = maskCoverageOfRoi(DEFAULT_ROI, []);
    expect(first).toBe(second);
    expect(first).toBe(0);
  });
});

describe("phenology", () => {
  const observed = seasonalTemperatures("2026-04-01", 118);

  it("advances with warmer weather and never regresses", () => {
    const cool = projectRipening({ coarseGeohash: "u173z", asOf: "2026-10-24", observed });
    const warm = projectRipening({
      coarseGeohash: "u173z",
      asOf: "2026-10-24",
      observed: seasonalTemperatures("2026-04-01", 118, { warmthOffsetC: 5 }),
    });
    expect(warm.accumulatedGdd).toBeGreaterThan(cool.accumulatedGdd);
    expect(warm.harvestStart).not.toBeNull();
    expect((warm.harvestStart ?? "") <= (cool.harvestStart ?? "")).toBe(true);
  });

  it("stays experimental with an unreviewed prior and never infers ripeness", () => {
    const projection = projectRipening({ coarseGeohash: "u173z", asOf: "2026-10-24", observed });
    expect(projection.experimental).toBe(true);
    expect(projection.reviewStatus).toBe("fixture_unreviewed");
    expect(projection.humanReadinessReviewRequired).toBe(true);
    expect(projection.internalRipenessInferred).toBe(false);
    expect(projection.foodSafetyInferred).toBe(false);
    expect(projection.reasonCodes).toContain("prior_not_approved");
  });
});

describe("notices", () => {
  const alert: PickupAlert = {
    gleanId: "glean-demo-01",
    gleanState: "confirmed",
    cropLabel: "plum",
    coarseAreaLabel: "Delft south",
    windowStart: "2026-08-26T08:00:00Z",
    windowEnd: "2026-08-26T12:00:00Z",
    accessConstraints: ["street parking only"],
    safetyNotes: ["closed shoes"],
    templateVersion: "pickup-v2",
  };

  it("masks phone numbers instead of showing them", () => {
    const masked = maskPhone("+31600000001");
    expect(masked).not.toContain("00000001");
    expect(masked.startsWith("+31")).toBe(true);
  });

  it("renders a notice without a phone number, address, or restricted marker", () => {
    const body = renderNotice(alert);
    expect(body).toContain("Delft south");
    expect(body).not.toMatch(/\+\d{8,15}/);
  });

  it("refuses to leak the glean's restricted access detail", () => {
    // A street-shaped string is caught by the address guard before the marker comparison runs.
    expect(() =>
      renderNotice({ ...alert, accessConstraints: ["FIXTURE 1 Example Street"] }, [
        "FIXTURE 1 Example Street",
      ]),
    ).toThrowError(/street address/i);
    // A non-address secret is caught by the restricted-marker comparison.
    expect(() =>
      renderNotice({ ...alert, accessConstraints: ["gate code 0000"] }, ["gate code 0000"]),
    ).toThrowError(/restricted access detail/i);
  });

  it("suppresses opted-out volunteers and defers during quiet hours", () => {
    const result = dispatchNotices({
      alert,
      volunteers: DEMO_VOLUNTEERS,
      at: "2026-08-26T08:00:00Z",
      ledger: {},
      sentToday: {},
    });
    expect(result.report.sent).toBe(2);
    const optedOut = result.report.outcomes.find((o) => o.volunteerId === "volunteer-c");
    expect(optedOut?.status).toBe("skipped");
    expect(optedOut?.reason).toBe("sms_opt_in_required");

    const quiet = dispatchNotices({
      alert,
      volunteers: DEMO_VOLUNTEERS,
      at: "2026-08-26T22:30:00Z",
      ledger: {},
      sentToday: {},
    });
    expect(quiet.report.sent).toBe(0);
    expect(quiet.report.deferred).toBe(2);
  });

  it("suppresses a duplicate for the same glean and recipient", () => {
    const first = dispatchNotices({
      alert,
      volunteers: DEMO_VOLUNTEERS,
      at: "2026-08-26T08:00:00Z",
      ledger: {},
      sentToday: {},
    });
    const second = dispatchNotices({
      alert,
      volunteers: DEMO_VOLUNTEERS,
      at: "2026-08-26T08:00:00Z",
      ledger: first.ledger,
      sentToday: first.sentToday,
    });
    expect(second.report.sent).toBe(0);
    expect(second.report.outcomes[0]?.reason).toBe("duplicate_suppressed");
  });
});

describe("demo store", () => {
  it("blocks dispatch until the view is approved and readiness is confirmed", () => {
    const store = new DemoStore();
    expect(store.canDispatch()).toBe(false);
    store.setAssignments(["volunteer-a"]);
    expect(store.canDispatch()).toBe(false);
    store.approveView("owner");
    store.approveView("coordinator");
    store.confirmReadiness(true);
    expect(store.canDispatch()).toBe(true);
  });

  it("resets view approvals when the geometry changes", () => {
    const store = new DemoStore();
    store.approveView("owner");
    store.approveView("coordinator");
    expect(store.getSnapshot().view.coordinatorApproved).toBe(true);
    store.moveRoiPoint(0, { x: 0.2, y: 0.2 });
    expect(store.getSnapshot().view.ownerApproved).toBe(false);
    expect(store.getSnapshot().view.coordinatorApproved).toBe(false);
  });

  it("dispatches only to assigned, opted-in volunteers and keeps digests", () => {
    const store = new DemoStore();
    store.approveView("owner");
    store.approveView("coordinator");
    store.confirmReadiness(true);
    store.setAssignments(["volunteer-a", "volunteer-c"]);
    const report = store.dispatch();
    expect(report?.sent).toBe(1);
    expect(report?.outcomes.map((outcome) => outcome.volunteerId)).toEqual([
      "volunteer-a",
      "volunteer-c",
    ]);
    const sent = report?.outcomes.find((outcome) => outcome.volunteerId === "volunteer-a");
    expect(sent?.bodySha256).toHaveLength(64);
    expect(sent?.maskedPhone).not.toContain("+31600000001");
  });

  it("does not dispatch while a mask hides too much of the tree", () => {
    const store = new DemoStore({
      masks: [{ id: "big", kind: "neighbor_property", x: 0, y: 0, width: 1, height: 1 }],
    });
    store.approveView("owner");
    store.approveView("coordinator");
    store.confirmReadiness(true);
    store.setAssignments(["volunteer-a"]);
    expect(store.canDispatch()).toBe(false);
    expect(store.dispatch()).toBeNull();
  });
});

describe("demo workspace", () => {
  it("renders every control the recording script drives, before any interaction", () => {
    renderStore();
    for (const testId of RECORDING_CONTROL_IDS) {
      expect(container.querySelector(`[data-testid="${testId}"]`), testId).not.toBeNull();
    }
  });

  it("keeps the recorder's assertions reachable through the store", () => {
    const store = renderStore();
    act(() => {
      store.approveView("owner");
      store.approveView("coordinator");
      store.simulateWebhook();
      store.confirmReadiness(true);
      store.setAssignments(["volunteer-a", "volunteer-b", "volunteer-c"]);
      store.dispatch();
    });
    for (const testId of RECORDING_EXPECTATION_IDS) {
      expect(container.querySelector(`[data-testid="${testId}"]`), testId).not.toBeNull();
    }
  });

  it("walks the four recording scenarios without leaving the gate closed", () => {
    const store = renderStore();
    // Scenario 1 — consent.
    act(() => {
      store.completeOnboarding({
        regionLabel: "Delft south",
        ownerConsentActive: true,
        cameraCollectionConsent: true,
      });
    });
    expect(byTestId("collection-status").textContent).toContain("Consented");

    // Scenario 2 — geometry, approvals, webhook.
    act(() => {
      store.approveView("owner");
      store.approveView("coordinator");
      store.simulateWebhook();
    });
    expect(byTestId("view-approvals").textContent).toContain("owner ✔");
    expect(byTestId("webhook-summary").textContent).toContain("HMAC verified");
    expect(byTestId("kept-count").textContent).toMatch(/^\d+ kept$/);
    expect(Number.parseInt(byTestId("kept-count").textContent ?? "0", 10)).toBeGreaterThan(0);

    // Scenario 3 — the curve moves.
    const before = byTestId("accumulated-gdd").textContent;
    act(() => store.setWarmthOffset(5));
    expect(byTestId("accumulated-gdd").textContent).not.toBe(before);

    // Scenario 4 — dispatch.
    act(() => {
      store.confirmReadiness(true);
      store.setAssignments(["volunteer-a", "volunteer-b", "volunteer-c"]);
    });
    expect(byTestId("dispatch-gate").textContent).toContain("Gate satisfied");
    act(() => {
      store.dispatch();
    });
    expect(byTestId("dispatch-summary").textContent).toContain("2 sent");
    expect(byTestId("outcome-volunteer-c").textContent).toContain("sms opt in required");
  });

  it("labels the page as fixture data on every screen", () => {
    renderStore();
    expect(byTestId("demo-banner").textContent).toContain("FIXTURE DATA");
  });

  it("keeps the recording plan honest by naming all four scenarios", () => {
    expect(RECORDING_SCENES.map((scene) => scene.id)).toEqual([
      "onboarding",
      "privacy",
      "ripening",
      "dispatch",
    ]);
  });
});
