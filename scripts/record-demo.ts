#!/usr/bin/env node
/**
 * Record the HarvestGrid demo walkthrough with Playwright.
 *
 * The script drives the interactive demo (`VITE_DEMO_MODE=true`) through the four scenarios in
 * `apps/web/src/demo/recordingPlan.ts` — tree onboarding and consent, the privacy ROI/mask
 * boundary with a simulated Ring frame, the Open-Meteo ripening curve, and coordinator-approved
 * volunteer dispatch — and writes a `.webm` to `docs/media/harvestgrid-demo.webm`.
 *
 * Usage:
 *   pnpm --dir apps/web exec playwright install chromium   # once, needs browser CDN access
 *   node --experimental-strip-types scripts/record-demo.ts
 *
 * Options:
 *   --base-url <url>          where the demo is served (default http://127.0.0.1:5173/harvestgrid/)
 *   --output <path>           output file (default docs/media/harvestgrid-demo.webm)
 *   --executable-path <path>  use a Chromium/headless-shell binary Playwright did not install
 *   --launch-arg <flag>       extra browser flag (repeatable), e.g. --launch-arg=--no-sandbox
 *   --headed                  show the browser (useful when debugging selectors)
 *   --selftest                verify the scene plan and the served page without launching a browser
 *
 * Alongside the video, the run writes `<output>.scenes.json`: a cue sheet with each scene's
 * wall-clock start, end, title, and narration. The coordinator dashboard's Demo Video tab reads it
 * to highlight the scene card that matches the playhead, so the cards and the video cannot drift.
 *
 * `--executable-path` exists for environments where the Playwright browser CDN is unreachable: any
 * recent `chrome-headless-shell` build works, and `--launch-arg` carries the container flags it
 * needs. With the stock install, no flags are required.
 *
 * The recording is silent: Playwright captures video only. Narration lives in the cue sheet and the
 * scene plan so a voice-over can be added later without re-timing the UI.
 *
 * Honesty note: the video is a recording of the *fixture* demo. Every frame carries the
 * FIXTURE DATA banner; no device, weather service, telephony account, or database is involved.
 * Playwright resolves from `apps/web/node_modules`, because the repository has exactly one Node
 * project and one lockfile.
 */

import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { RECORDING_SCENES, type DemoAction } from "../apps/web/src/demo/recordingPlan.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");
const DEFAULT_OUTPUT = join(ROOT, "docs", "media", "harvestgrid-demo.webm");
const RAW_DIR = join(ROOT, "docs", "media", ".playwright-raw");
const VIEWPORT = { width: 1280, height: 900 };

/** One scene's position in the recorded video, in milliseconds from the first frame. */
export interface SceneCue {
  readonly id: string;
  readonly title: string;
  /** Short label the dashboard's scene card shows, from the plan's `cardTitle`. */
  readonly cardTitle: string;
  readonly narration: string;
  readonly startMs: number;
  readonly endMs: number;
  readonly assertions: readonly string[];
}

export interface CueSheet {
  readonly video: string;
  readonly generatedAt: string;
  readonly viewport: { readonly width: number; readonly height: number };
  readonly durationMs: number;
  readonly scenes: readonly SceneCue[];
}

interface PageLike {
  goto(url: string, options?: { waitUntil?: string }): Promise<unknown>;
  waitForSelector(selector: string, options?: { timeout?: number }): Promise<unknown>;
  click(selector: string): Promise<void>;
  fill(selector: string, value: string): Promise<void>;
  setChecked(selector: string, checked: boolean): Promise<void>;
  evaluate<T>(expression: string, argument?: unknown): Promise<T>;
  mouse: {
    move(x: number, y: number, options?: { steps?: number }): Promise<void>;
    down(): Promise<void>;
    up(): Promise<void>;
  };
  waitForTimeout(ms: number): Promise<void>;
}

interface BrowserContextLike {
  newPage(): Promise<PageLike>;
  close(): Promise<void>;
}

interface BrowserLike {
  newContext(options: Record<string, unknown>): Promise<BrowserContextLike>;
  close(): Promise<void>;
}

interface PlaywrightModule {
  chromium: { launch(options?: Record<string, unknown>): Promise<BrowserLike> };
}

export function selector(testId: string): string {
  return `[data-testid="${testId}"]`;
}

export interface RecordArgs {
  readonly baseUrl: string;
  readonly output: string;
  readonly headed: boolean;
  readonly selftest: boolean;
  readonly executablePath?: string;
  readonly launchArgs: readonly string[];
}

export function parseArgs(argv: readonly string[]): RecordArgs {
  const result: {
    baseUrl: string;
    output: string;
    headed: boolean;
    selftest: boolean;
    executablePath?: string;
    launchArgs: string[];
  } = {
    baseUrl: process.env.DEMO_URL ?? "http://127.0.0.1:5173/harvestgrid/",
    output: DEFAULT_OUTPUT,
    headed: false,
    selftest: false,
    launchArgs: [],
  };
  const envExecutable = process.env.PLAYWRIGHT_EXECUTABLE_PATH;
  if (envExecutable) result.executablePath = resolve(envExecutable);
  for (const extra of (process.env.PLAYWRIGHT_LAUNCH_ARGS ?? "").split(/[,\s]+/)) {
    if (extra) result.launchArgs.push(extra);
  }
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    const next = argv[index + 1];
    if (flag === "--base-url" && next) {
      result.baseUrl = next;
      index += 1;
    } else if (flag === "--output" && next) {
      result.output = resolve(process.cwd(), next);
      index += 1;
    } else if (flag === "--executable-path" && next) {
      result.executablePath = resolve(next);
      index += 1;
    } else if (flag?.startsWith("--launch-arg=")) {
      result.launchArgs.push(flag.slice("--launch-arg=".length));
    } else if (flag === "--launch-arg" && next) {
      result.launchArgs.push(next);
      index += 1;
    } else if (flag === "--headed") {
      result.headed = true;
    } else if (flag === "--selftest") {
      result.selftest = true;
    }
  }
  if (!result.baseUrl.endsWith("/")) result.baseUrl = `${result.baseUrl}/`;
  return result;
}

/** Cue-sheet path for a given video: `foo.webm` → `foo.scenes.json`. */
export function cueSheetPath(output: string): string {
  return output.replace(/\.webm$/, "") + ".scenes.json";
}

async function loadPlaywright(): Promise<PlaywrightModule> {
  const requireFromWeb = createRequire(join(ROOT, "apps", "web", "package.json"));
  let resolved: string;
  try {
    resolved = requireFromWeb.resolve("playwright");
  } catch {
    throw new Error(
      "playwright is not installed. Run: pnpm --dir apps/web install && " +
        "pnpm --dir apps/web exec playwright install chromium",
    );
  }
  // Playwright ships CommonJS; named-export detection is not guaranteed for it, so accept either
  // the namespace or its `default`.
  const loaded = (await import(pathToFileURL(resolved).href)) as
    | PlaywrightModule
    | { default: PlaywrightModule };
  const module = "chromium" in loaded ? loaded : loaded.default;
  if (module?.chromium === undefined) {
    throw new Error(`playwright at ${resolved} did not expose chromium`);
  }
  return module;
}

async function centre(page: PageLike, testId: string): Promise<{ x: number; y: number }> {
  return page.evaluate<{ x: number; y: number }>(
    `(() => {
      const element = document.querySelector(${JSON.stringify(selector(testId))});
      if (!element) throw new Error("missing " + ${JSON.stringify(testId)});
      const rect = element.getBoundingClientRect();
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    })()`,
  );
}

async function perform(page: PageLike, action: DemoAction): Promise<void> {
  switch (action.kind) {
    case "click":
      await page.click(selector(action.testId));
      return;
    case "check":
      await page.setChecked(selector(action.testId), action.checked);
      return;
    case "fill":
      await page.fill(selector(action.testId), action.value);
      return;
    case "range":
      await page.evaluate(
        `(() => {
          const input = document.querySelector(${JSON.stringify(selector(action.testId))});
          if (!(input instanceof HTMLInputElement)) throw new Error("missing range");
          const setter = Object.getOwnPropertyDescriptor(
            HTMLInputElement.prototype, "value").set;
          setter.call(input, ${JSON.stringify(action.value)});
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.dispatchEvent(new Event("change", { bubbles: true }));
        })()`,
      );
      return;
    case "dragVertex": {
      const start = await centre(page, action.testId);
      await page.mouse.move(start.x, start.y);
      await page.mouse.down();
      await page.mouse.move(start.x + action.dx, start.y + action.dy, { steps: 12 });
      await page.mouse.up();
      return;
    }
    case "wait":
      await page.waitForTimeout(action.ms);
      return;
    case "caption":
      return;
  }
}

/** Scrolls a panel to the middle of the viewport and returns once the scroll has settled. */
async function focusPanel(page: PageLike, testId: string): Promise<void> {
  await page.evaluate(
    `(() => {
      const element = document.querySelector(${JSON.stringify(selector(testId))});
      if (!element) throw new Error("missing " + ${JSON.stringify(testId)});
      element.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
    })()`,
  );
  await page.waitForTimeout(650);
}

async function assertExpectations(page: PageLike, sceneId: string): Promise<void> {
  const scene = RECORDING_SCENES.find((item) => item.id === sceneId);
  if (!scene) return;
  for (const expectation of scene.expect) {
    await page.waitForSelector(selector(expectation.testId), { timeout: 10_000 });
    const text = await page.evaluate<string>(
      `document.querySelector(${JSON.stringify(selector(expectation.testId))}).textContent ?? ""`,
    );
    if (!text.toLowerCase().includes(expectation.contains.toLowerCase())) {
      throw new Error(
        `scene ${sceneId}: ${expectation.testId} did not contain "${expectation.contains}" (got "${text}")`,
      );
    }
  }
}

export interface RecordOptions {
  readonly baseUrl: string;
  readonly output: string;
  readonly headed: boolean;
  readonly executablePath?: string;
  readonly launchArgs?: readonly string[];
  readonly playwright?: PlaywrightModule;
  /** Injectable clock so cue timings are deterministic in tests. */
  readonly now?: () => number;
}

export async function record(options: RecordOptions): Promise<{
  video: string;
  cueSheet: string;
  cues: CueSheet;
}> {
  const playwright = options.playwright ?? (await loadPlaywright());
  const now = options.now ?? (() => Date.now());
  rmSync(RAW_DIR, { recursive: true, force: true });
  mkdirSync(RAW_DIR, { recursive: true });

  const launchOptions: Record<string, unknown> = { headless: !options.headed };
  if (options.executablePath) launchOptions.executablePath = options.executablePath;
  if (options.launchArgs && options.launchArgs.length > 0) {
    launchOptions.args = [...options.launchArgs];
  }

  // The video clock starts with the first frame, not with the process, so scene boundaries are
  // measured from page load and shifted by the recorder's own lead-in.
  const LEAD_IN_MS = 1200;
  const sceneCues: SceneCue[] = [];

  const browser = await playwright.chromium.launch(launchOptions);
  try {
    const context = await browser.newContext({
      viewport: VIEWPORT,
      recordVideo: { dir: RAW_DIR, size: VIEWPORT },
      colorScheme: "light",
      reducedMotion: "no-preference",
    });
    const page = await context.newPage();
    await page.goto(options.baseUrl, { waitUntil: "domcontentloaded" });
    await page.waitForSelector(selector("demo-workspace"), { timeout: 30_000 });
    await page.waitForTimeout(LEAD_IN_MS);

    const startedAt = now();
    for (const scene of RECORDING_SCENES) {
      // Frame the panel this scene acts on, so the video shows the change rather than a scroll
      // jump. The panel id comes from the plan, so a renamed section cannot desynchronise them.
      await focusPanel(page, scene.panelTestId);
      await page.waitForTimeout(600);
      const sceneStart = now() - startedAt + LEAD_IN_MS;
      console.log(`▶ ${scene.title}`);
      for (const action of scene.actions) await perform(page, action);
      await assertExpectations(page, scene.id);
      await page.waitForTimeout(4500);
      sceneCues.push({
        id: scene.id,
        title: scene.title,
        cardTitle: scene.cardTitle,
        narration: scene.narration,
        startMs: Math.round(sceneStart),
        endMs: Math.round(now() - startedAt + LEAD_IN_MS),
        assertions: scene.expect.map((item) => `${item.testId} ∋ ${item.contains}`),
      });
    }
    // A closing beat so the last frame is not the dispatch table mid-scroll.
    await page.evaluate("window.scrollTo({ top: 0, behavior: 'smooth' })");
    await page.waitForTimeout(1500);
    const durationMs = Math.round(now() - startedAt + LEAD_IN_MS);
    await context.close();

    const videos = readdirSync(RAW_DIR).filter((name) => name.endsWith(".webm"));
    if (videos.length !== 1) {
      throw new Error(`expected exactly one recording in ${RAW_DIR}, found ${videos.length}`);
    }
    mkdirSync(dirname(options.output), { recursive: true });
    const rawVideo = join(RAW_DIR, videos[0] as string);
    const narrationPath = join(ROOT, "docs", "media", "harvestgrid-narration.mp3");
    if (existsSync(narrationPath)) {
      execFileSync("ffmpeg", [
        "-y",
        "-i",
        rawVideo,
        "-i",
        narrationPath,
        "-c:v",
        "copy",
        "-c:a",
        "libopus",
        "-b:a",
        "128k",
        "-shortest",
        options.output,
      ]);
      execFileSync("ffmpeg", [
        "-y",
        "-i",
        options.output,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        options.output.replace(/\.webm$/, ".mp4"),
      ]);
    } else {
      renameSync(rawVideo, options.output);
    }
    rmSync(RAW_DIR, { recursive: true, force: true });

    const cues: CueSheet = {
      video: options.output.split(/[\\/]/).pop() ?? "harvestgrid-demo.webm",
      generatedAt: new Date(startedAt).toISOString(),
      viewport: VIEWPORT,
      durationMs,
      scenes: sceneCues,
    };
    const cuePath = cueSheetPath(options.output);
    writeFileSync(cuePath, `${JSON.stringify(cues, null, 2)}\n`, "utf8");
    return { video: options.output, cueSheet: cuePath, cues };
  } finally {
    await browser.close();
  }
}

async function selftest(baseUrl: string): Promise<void> {
  // 1. The plan itself: unique ids, every scene reachable, expectations named.
  const seen = new Set<string>();
  for (const scene of RECORDING_SCENES) {
    for (const action of scene.actions) {
      if (action.kind === "wait" || action.kind === "caption") continue;
      if (seen.has(action.testId)) {
        console.log(`note: ${action.testId} is driven in more than one scene`);
      }
      seen.add(action.testId);
    }
    if (scene.expect.length === 0) throw new Error(`scene ${scene.id} asserts nothing`);
  }
  console.log(`✔ plan: ${RECORDING_SCENES.length} scenes, ${seen.size} controls, all asserting`);

  // 2. The served page: reachable, and the demo bundle is referenced.
  const response = await fetch(baseUrl);
  if (!response.ok) throw new Error(`${baseUrl} returned ${response.status}`);
  const html = await response.text();
  if (!html.includes("id=\"root\"")) throw new Error("page has no #root element");
  console.log(`✔ served page: ${baseUrl} (${response.status}, ${html.length} bytes)`);

  // 3. The browser: a Playwright-installed build, or an explicit binary.
  const explicit = process.env.PLAYWRIGHT_EXECUTABLE_PATH;
  const cacheHint = join(ROOT, "apps", "web", "node_modules", "playwright-core", ".local-browsers");
  console.log(
    explicit && existsSync(explicit)
      ? `✔ explicit browser: ${explicit}; run without --selftest to record`
      : existsSync(cacheHint)
        ? "✔ chromium cache present; run without --selftest to record"
        : "note: chromium is not installed, so the video cannot be recorded in this environment " +
            "(run `pnpm --dir apps/web exec playwright install chromium`, or pass " +
            "--executable-path)",
  );
  console.log("Selectors the recorder will use:");
  for (const scene of RECORDING_SCENES) {
    console.log(`  ${scene.title}`);
    console.log(`    ${scene.actions.filter((a) => a.kind !== "wait" && a.kind !== "caption").length} actions, ${scene.expect.length} assertions`);
  }
}

function main(): number {
  const args = parseArgs(process.argv.slice(2));
  if (args.selftest) {
    selftest(args.baseUrl)
      .then(() => console.log("✔ selftest passed"))
      .catch((error: unknown) => {
        console.error(`✖ ${error instanceof Error ? error.message : String(error)}`);
        process.exitCode = 1;
      });
    return 0;
  }
  record(args)
    .then(({ video, cueSheet, cues }) => {
      const bytes = readFileSync(video).length;
      console.log(`✔ wrote ${video} (${(bytes / 1024).toFixed(0)} kB, ${cues.durationMs} ms)`);
      console.log(`✔ wrote ${cueSheet} (${cues.scenes.length} scenes)`);
      const output = video;
      // Playwright only records VP8 WebM; note the codec so docs do not over-claim.
      try {
        execFileSync("ffprobe", ["-v", "error", "-show_entries", "stream=codec_name", output], {
          stdio: "inherit",
        });
      } catch {
        console.log("(ffprobe not installed; skipping codec probe)");
      }
    })
    .catch((error: unknown) => {
      console.error(`✖ ${error instanceof Error ? error.message : String(error)}`);
      if (error instanceof Error && error.message.includes("Executable doesn't exist")) {
        console.error(
          "Run: pnpm --dir apps/web exec playwright install chromium, " +
            "or point --executable-path at an existing headless-shell binary.",
        );
      }
      process.exitCode = 1;
    });
  return 0;
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}
