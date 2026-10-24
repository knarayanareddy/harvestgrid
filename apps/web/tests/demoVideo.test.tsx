/**
 * The Demo Video tab: navigation, the embedded recording, and the scene cards that track it.
 *
 * jsdom does not decode media, so these tests assert the wiring that matters: the tab is in the
 * coordinator navigation beside the other three, the panel is shown and hidden accessibly, the
 * cards come from the recorder's cue sheet (with a plan-derived fallback), playback moves the
 * highlight, and a card seeks the player. The recording itself is verified against the built
 * bundle by `pnpm build` and the file on disk.
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import { FixtureCoordinatorApi } from "../src/api/fixtureClient";
import { DemoVideo } from "../src/components/DemoVideo";
import {
  activeSceneIndex,
  DEMO_CUE_SHEET_NAME,
  DEMO_VIDEO_URL,
  FALLBACK_SCENE_CARDS,
  formatTimestamp,
  loadCueSheet,
  mediaUrl,
  parseCueSheet,
  type CueSheet,
  type SceneCue,
} from "../src/demo/demoMedia";
import { RECORDING_SCENES } from "../src/demo/recordingPlan";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const CUES: readonly SceneCue[] = [
  {
    id: "onboarding",
    title: "1 · Tree onboarding and consent",
    cardTitle: "Onboarding",
    narration: "Consent first.",
    startMs: 1200,
    endMs: 4200,
    assertions: ["collection-status ∋ Consented"],
  },
  {
    id: "privacy",
    title: "2 · Privacy polygon and mask boundary",
    cardTitle: "Privacy ROI Mask",
    narration: "Drag a vertex.",
    startMs: 4200,
    endMs: 7000,
  },
  {
    id: "ripening",
    title: "3 · Open-Meteo ripening curve",
    cardTitle: "Open-Meteo Ripening",
    narration: "Warm the offset.",
    startMs: 7000,
    endMs: 9000,
  },
  {
    id: "dispatch",
    title: "4 · Volunteer dispatch",
    cardTitle: "Volunteer Dispatch",
    narration: "Dispatch two volunteers.",
    startMs: 9000,
    endMs: 12000,
  },
];

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
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function byTestId<T extends Element>(testId: string): T | null {
  return container.querySelector<T>(`[data-testid="${testId}"]`);
}

function renderVideo(props: Parameters<typeof DemoVideo>[0] = {}): void {
  act(() => {
    root.render(<DemoVideo cues={CUES} {...props} />);
  });
}

async function renderApp(): Promise<void> {
  await act(async () => {
    root.render(<App api={new FixtureCoordinatorApi()} />);
  });
}

describe("coordinator navigation", () => {
  it("lists Demo Video beside Map View, Yield Forecasts, and Gleaning Dispatch", async () => {
    await renderApp();
    const tabs = Array.from(container.querySelectorAll<HTMLButtonElement>("[role=tab]"));
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "Map View",
      "Yield Forecasts",
      "Gleaning Dispatch",
      "Demo Video",
    ]);
    // One selected tab, and it controls a panel that is not hidden.
    const selected = tabs.filter((tab) => tab.getAttribute("aria-selected") === "true");
    expect(selected).toHaveLength(1);
    const controlled = selected[0]?.getAttribute("aria-controls") ?? "";
    expect(container.querySelector(`#${controlled}`)?.hasAttribute("hidden")).toBe(false);
  });

  it("shows the Demo Video panel and hides the others when its tab is selected", async () => {
    await renderApp();
    const videoTab = byTestId<HTMLButtonElement>("tab-video");
    act(() => videoTab?.click());

    expect(videoTab?.getAttribute("aria-selected")).toBe("true");
    const panel = container.querySelector("#panel-video");
    expect(panel?.hasAttribute("hidden")).toBe(false);
    expect(container.querySelector("#panel-dispatch")?.hasAttribute("hidden")).toBe(true);
    expect(byTestId("demo-video-panel")).not.toBeNull();
    // The video itself is embedded, not merely described.
    const video = byTestId<HTMLVideoElement>("demo-video");
    expect(video?.querySelector("source")?.getAttribute("src")).toBe(DEMO_VIDEO_URL);
    expect(video?.getAttribute("controls")).not.toBeNull();
  });

  it("moves selection with the arrow keys and keeps one panel visible", async () => {
    await renderApp();
    const tablist = container.querySelector("[role=tablist]");
    const dispatchTab = byTestId<HTMLButtonElement>("tab-dispatch");
    expect(dispatchTab?.getAttribute("aria-selected")).toBe("true");

    act(() => {
      dispatchTab?.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }),
      );
    });
    expect(byTestId<HTMLButtonElement>("tab-video")?.getAttribute("aria-selected")).toBe("true");

    act(() => {
      tablist?.dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true }));
    });
    expect(byTestId<HTMLButtonElement>("tab-map")?.getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector("#panel-map")?.hasAttribute("hidden")).toBe(false);
    expect(container.querySelector("#panel-video")?.hasAttribute("hidden")).toBe(true);
  });

  it("keeps the dispatch board mounted so a tab change does not lose work", async () => {
    await renderApp();
    const checkbox = container.querySelector<HTMLInputElement>("#select-volunteer-b");
    act(() => checkbox?.click());
    expect(container.textContent).toContain("1 volunteer(s) selected");

    act(() => byTestId<HTMLButtonElement>("tab-video")?.click());
    act(() => byTestId<HTMLButtonElement>("tab-dispatch")?.click());

    expect(container.textContent).toContain("1 volunteer(s) selected");
  });
});

describe("demo video panel", () => {
  it("renders one card per recorded scene, in order", () => {
    renderVideo();
    const cards = Array.from(container.querySelectorAll("[data-testid^='scene-card-']"));
    expect(cards.map((card) => card.getAttribute("data-testid"))).toEqual([
      "scene-card-onboarding",
      "scene-card-privacy",
      "scene-card-ripening",
      "scene-card-dispatch",
    ]);
    const titles = Array.from(
      container.querySelectorAll("[data-testid^='scene-card-'] .scene-card-title"),
    ).map((node) => node.textContent);
    expect(titles).toEqual([
      "Onboarding",
      "Privacy ROI Mask",
      "Open-Meteo Ripening",
      "Volunteer Dispatch",
    ]);
  });

  it("falls back to the long scene title when a cue predates card labels", () => {
    const { cardTitle: _omitted, ...withoutLabel } = CUES[0] as SceneCue;
    renderVideo({ cues: [withoutLabel] });
    expect(container.querySelector(".scene-card-title")?.textContent).toBe(
      "1 · Tree onboarding and consent",
    );
  });

  it("highlights the card that covers the playhead", () => {
    renderVideo();
    const video = byTestId<HTMLVideoElement>("demo-video");
    expect(video).not.toBeNull();
    const seekTo = (seconds: number): void => {
      if (!video) throw new Error("no video");
      Object.defineProperty(video, "currentTime", { value: seconds, configurable: true });
      act(() => {
        video.dispatchEvent(new Event("timeupdate"));
      });
    };

    seekTo(1.0);
    expect(byTestId("scene-card-onboarding")?.getAttribute("data-active")).toBe("yes");

    seekTo(5.4);
    expect(byTestId("scene-card-privacy")?.getAttribute("data-active")).toBe("yes");
    expect(byTestId("scene-card-onboarding")?.getAttribute("data-active")).toBe("no");

    seekTo(10.2);
    expect(byTestId("scene-card-dispatch")?.getAttribute("data-active")).toBe("yes");
    expect(byTestId("scene-card-dispatch")?.getAttribute("aria-current")).toBe("step");
  });

  it("seeks the player when a card is chosen", () => {
    renderVideo();
    const video = byTestId<HTMLVideoElement>("demo-video");
    act(() => byTestId<HTMLButtonElement>("scene-card-privacy")?.click());
    expect(video?.currentTime).toBeCloseTo(4.201, 3);
  });

  it("states that the recording is absent instead of rendering a dead player", () => {
    renderVideo();
    const video = byTestId<HTMLVideoElement>("demo-video");
    act(() => {
      video?.dispatchEvent(new Event("error"));
    });
    expect(byTestId("video-missing")).not.toBeNull();
    expect(container.textContent).toContain("scripts/record-demo.ts");
    // The scene cards survive, because they are the transcript.
    expect(container.querySelectorAll("[data-testid^='scene-card-']")).toHaveLength(4);
  });

  it("falls back to the plan's cards when no cue timings exist", () => {
    renderVideo({ cues: FALLBACK_SCENE_CARDS });
    const cards = Array.from(container.querySelectorAll<HTMLButtonElement>("[data-testid^='scene-card-']"));
    expect(cards).toHaveLength(RECORDING_SCENES.length);
    expect(cards.every((card) => card.disabled)).toBe(true);
    expect(container.textContent).toContain("Cue timings are unavailable");
    expect(byTestId("demo-video-caption")?.textContent).toContain("timing unknown");
  });

  it("loads and applies a cue sheet fetched from the published media directory", async () => {
    const sheet: CueSheet = {
      video: "harvestgrid-demo.webm",
      generatedAt: "2026-10-24T15:32:42.274Z",
      durationMs: 18013,
      scenes: CUES,
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => sheet,
    });
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => {
      root.render(<DemoVideo />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(fetchMock).toHaveBeenCalledWith(
      mediaUrl(DEMO_CUE_SHEET_NAME),
      expect.objectContaining({ headers: { accept: "application/json" } }),
    );
    expect(byTestId("demo-video-caption")?.textContent).toContain("0:18 total");
    expect(byTestId("scene-card-privacy")?.hasAttribute("disabled")).toBe(false);
  });
});

describe("cue sheet contract", () => {
  it("accepts a recorder-shaped sheet and keeps the assertions", () => {
    const parsed = parseCueSheet({
      video: "harvestgrid-demo.webm",
      generatedAt: "2026-10-24T15:32:42.274Z",
      durationMs: 18013,
      scenes: CUES,
    });
    expect(parsed?.scenes).toHaveLength(4);
    expect(parsed?.scenes[0]?.assertions).toEqual(["collection-status ∋ Consented"]);
    expect(parsed?.durationMs).toBe(18013);
  });

  it("rejects sheets that would break the playhead", () => {
    const base = { video: "v.webm", generatedAt: "", durationMs: 1000 };
    expect(parseCueSheet(null)).toBeNull();
    expect(parseCueSheet({ ...base, scenes: [] })).toBeNull();
    expect(parseCueSheet({ ...base, durationMs: 0, scenes: CUES })).toBeNull();
    // Unknown scene id: a plan and a recording have diverged.
    expect(
      parseCueSheet({ ...base, scenes: [{ ...CUES[0], id: "hand-edited" }] }),
    ).toBeNull();
    // Out-of-order or negative timings.
    expect(
      parseCueSheet({
        ...base,
        scenes: [CUES[1], CUES[0]],
      }),
    ).toBeNull();
    expect(parseCueSheet({ ...base, scenes: [{ ...CUES[0], startMs: -5 }] })).toBeNull();
  });

  it("resolves media under the configured base path", () => {
    expect(DEMO_VIDEO_URL).toBe(mediaUrl("harvestgrid-demo.webm"));
    expect(DEMO_VIDEO_URL.startsWith(import.meta.env.BASE_URL)).toBe(true);
    expect(DEMO_VIDEO_URL).toContain("/media/harvestgrid-demo.webm");
  });

  it("returns null instead of throwing when the sheet is missing", async () => {
    await expect(
      loadCueSheet("/media/nope.json", vi.fn().mockRejectedValue(new Error("offline"))),
    ).resolves.toBeNull();
    await expect(
      loadCueSheet(
        "/media/nope.json",
        vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }),
      ),
    ).resolves.toBeNull();
    // No fetch implementation at all (old browser, or a test environment without one).
    vi.stubGlobal("fetch", undefined);
    await expect(loadCueSheet("/media/nope.json")).resolves.toBeNull();
  });

  it("computes the active scene and formats timestamps", () => {
    expect(activeSceneIndex(CUES, 0)).toBe(0);
    expect(activeSceneIndex(CUES, 1199)).toBe(0);
    expect(activeSceneIndex(CUES, 1200)).toBe(0);
    expect(activeSceneIndex(CUES, 4200)).toBe(1);
    expect(activeSceneIndex(CUES, 999_999)).toBe(3);
    expect(activeSceneIndex(FALLBACK_SCENE_CARDS, 5000)).toBe(-1);
    expect(formatTimestamp(0)).toBe("0:00");
    expect(formatTimestamp(18_013)).toBe("0:18");
    expect(formatTimestamp(Number.NaN)).toBe("--:--");
  });
});
