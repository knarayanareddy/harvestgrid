/**
 * Locates the recorded walkthrough for the Demo Video tab.
 *
 * `docs/media/` is published by the `harvestgrid-demo-media` plugin in `vite.config.ts` at
 * `<base>media/<name>` in dev, preview, and build alike. Nothing here imports the binary: the
 * video is 2 MB of VP8 that Vite must not attempt to parse, so it is referenced by URL and the
 * cue sheet is fetched as data.
 *
 * Both artefacts are optional. A checkout that has not run `scripts/record-demo.ts` still builds
 * and still shows the four scene cards (from `recordingPlan.ts`, which is always present); it just
 * says the recording is missing instead of rendering a player that cannot play.
 */

import { RECORDING_SCENES } from "./recordingPlan";

export const DEMO_VIDEO_NAME = "harvestgrid-demo.webm";
export const DEMO_VIDEO_MP4_NAME = "harvestgrid-demo.mp4";
export const DEMO_CUE_SHEET_NAME = "harvestgrid-demo.scenes.json";

/** One scene's position in the video, as written by the recorder. */
export interface SceneCue {
  readonly id: string;
  readonly title: string;
  /** Short card label. Older cue sheets omitted it, so it is derived from the plan by id. */
  readonly cardTitle?: string;
  readonly narration: string;
  readonly startMs: number;
  readonly endMs: number;
  readonly assertions?: readonly string[];
}

export interface CueSheet {
  readonly video: string;
  readonly generatedAt: string;
  readonly viewport?: { readonly width: number; readonly height: number };
  readonly durationMs: number;
  readonly scenes: readonly SceneCue[];
}

/** Base-path aware URL for a published media file (GitHub Pages serves under `/harvestgrid/`). */
export function mediaUrl(name: string): string {
  const base = import.meta.env.BASE_URL ?? "/";
  return `${base.endsWith("/") ? base : `${base}/`}media/${name}`;
}

export const DEMO_VIDEO_URL = mediaUrl(DEMO_VIDEO_NAME);
export const DEMO_VIDEO_MP4_URL = mediaUrl(DEMO_VIDEO_MP4_NAME);
export const DEMO_CUE_SHEET_URL = mediaUrl(DEMO_CUE_SHEET_NAME);

/**
 * Cards to render when no cue sheet is available. The labels come from the same plan the recorder
 * drives, so the fallback list cannot describe a scene the video does not contain.
 */
export const FALLBACK_SCENE_CARDS: readonly SceneCue[] = RECORDING_SCENES.map((scene) => ({
  id: scene.id,
  title: scene.title,
  cardTitle: scene.cardTitle,
  narration: scene.narration,
  startMs: Number.NaN,
  endMs: Number.NaN,
}));

const PLAN_BY_ID = new Map(RECORDING_SCENES.map((scene) => [scene.id, scene]));

/**
 * Validates a cue sheet before it drives the UI.
 *
 * The file is generated, but it is also an input from disk: a truncated or hand-edited sheet must
 * degrade to the fallback cards rather than produce a player that seeks to `NaN`. Every scene must
 * have a known id and a non-negative, ordered timeline.
 */
export function parseCueSheet(value: unknown): CueSheet | null {
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  const scenes = record.scenes;
  if (!Array.isArray(scenes) || scenes.length === 0) return null;
  const durationMs = record.durationMs;
  if (typeof durationMs !== "number" || !Number.isFinite(durationMs) || durationMs <= 0) {
    return null;
  }

  const parsed: SceneCue[] = [];
  let previousEnd = 0;
  for (const entry of scenes) {
    if (typeof entry !== "object" || entry === null) return null;
    const scene = entry as Record<string, unknown>;
    const { id, title, narration, startMs, endMs } = scene;
    if (typeof id !== "string") return null;
    const planned = PLAN_BY_ID.get(id);
    if (!planned) return null;
    if (typeof title !== "string" || title.length === 0) return null;
    if (typeof narration !== "string") return null;
    if (typeof startMs !== "number" || typeof endMs !== "number") return null;
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return null;
    if (startMs < 0 || endMs < startMs || startMs < previousEnd) return null;
    previousEnd = endMs;
    parsed.push({
      id,
      title,
      // The plan is authoritative for the short label: a cue sheet is generated data, and a stale
      // one must not rename a card the tests and docs already refer to.
      cardTitle: planned.cardTitle,
      narration,
      startMs,
      endMs,
      assertions: Array.isArray(scene.assertions)
        ? scene.assertions.filter((item): item is string => typeof item === "string")
        : [],
    });
  }

  return {
    video: typeof record.video === "string" ? record.video : DEMO_VIDEO_NAME,
    generatedAt: typeof record.generatedAt === "string" ? record.generatedAt : "",
    durationMs,
    scenes: parsed,
  };
}

/** Loads the cue sheet, or `null` when it is absent or unusable. Never throws. */
export async function loadCueSheet(
  url: string = DEMO_CUE_SHEET_URL,
  fetchImpl: typeof fetch | undefined = globalThis.fetch,
): Promise<CueSheet | null> {
  if (!fetchImpl) return null;
  try {
    const response = await fetchImpl(url, { headers: { accept: "application/json" } });
    if (!response.ok) return null;
    return parseCueSheet(await response.json());
  } catch {
    return null;
  }
}

/** Index of the scene covering `timeMs`, or the nearest completed scene. */
export function activeSceneIndex(
  scenes: readonly SceneCue[],
  timeMs: number,
): number {
  if (scenes.length === 0) return -1;
  const timed = scenes.filter((scene) => Number.isFinite(scene.startMs));
  if (timed.length === 0) return -1;
  let candidate = 0;
  for (let index = 0; index < scenes.length; index += 1) {
    const scene = scenes[index] as SceneCue;
    if (!Number.isFinite(scene.startMs)) continue;
    if (timeMs >= scene.startMs) candidate = index;
    else break;
  }
  return candidate;
}

export function formatTimestamp(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "--:--";
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}
