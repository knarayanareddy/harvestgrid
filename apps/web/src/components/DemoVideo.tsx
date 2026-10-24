/**
 * "Demo Video" tab: the recorded walkthrough with synchronized scene cards.
 *
 * The video is the one produced by `scripts/record-demo.ts` — the same fixture demo, recorded
 * frame for frame — and the cards come from the cue sheet that run writes. Playback drives the
 * cards (`timeupdate` → active scene), and a card seeks the player, so the two directions stay in
 * sync instead of being a video with unrelated prose beside it.
 *
 * Nothing here is live evidence: the recording is of fixture data, which the page states in the
 * caption and in the transcript, and no frame is presented as a device or weather capture.
 */

import { useCallback, useEffect, useRef, useState, type ReactElement } from "react";

import {
  activeSceneIndex,
  DEMO_VIDEO_URL,
  DEMO_VIDEO_MP4_URL,
  FALLBACK_SCENE_CARDS,
  formatTimestamp,
  loadCueSheet,
  type CueSheet,
  type SceneCue,
} from "../demo/demoMedia";

export interface DemoVideoProps {
  /** Overrides for tests and stories; the defaults resolve to the published media URLs. */
  readonly videoUrl?: string;
  readonly cueSheetUrl?: string;
  readonly cues?: readonly SceneCue[];
  readonly durationMs?: number;
}

type MediaState =
  | { readonly status: "unknown" }
  | { readonly status: "ready" }
  | { readonly status: "missing" };

export function DemoVideo({
  videoUrl = DEMO_VIDEO_URL,
  cueSheetUrl,
  cues,
  durationMs,
}: DemoVideoProps): ReactElement {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [sheet, setSheet] = useState<CueSheet | null>(null);
  const [media, setMedia] = useState<MediaState>({ status: "unknown" });
  const [playhead, setPlayhead] = useState(0);

  useEffect(() => {
    if (cues) return;
    let cancelled = false;
    void loadCueSheet(cueSheetUrl).then((loaded) => {
      if (!cancelled) setSheet(loaded);
    });
    return () => {
      cancelled = true;
    };
  }, [cueSheetUrl, cues]);

  const scenes = cues ?? sheet?.scenes ?? FALLBACK_SCENE_CARDS;
  const timed = scenes.some((scene) => Number.isFinite(scene.startMs));
  const totalMs = durationMs ?? sheet?.durationMs ?? Number.NaN;
  const active = activeSceneIndex(scenes, playhead);

  const seek = useCallback(
    (scene: SceneCue) => {
      const video = videoRef.current;
      if (!video || !Number.isFinite(scene.startMs)) return;
      setPlayhead(scene.startMs + 1);
      video.currentTime = (scene.startMs + 1) / 1000;
      // Seeking without starting playback would leave the card highlighted on a paused frame;
      // `play()` is rejected in some browsers before a user gesture, which is fine to ignore.
      const playback = video.play?.();
      if (playback) void playback.catch(() => undefined);
    },
    [],
  );

  return (
    <section aria-labelledby="demo-video-heading" className="card" data-testid="demo-video-panel">
      <h2 id="demo-video-heading">Recorded walkthrough</h2>
      <p className="note">
        The fixture demo, recorded end to end by <code>scripts/record-demo.ts</code>. Playback
        moves the scene cards; selecting a card seeks the video. No device, weather service,
        telephony account, or database appears in any frame.
      </p>

      <div className="video-frame">
        {media.status === "missing" ? (
          <div className="video-missing" role="status" data-testid="video-missing">
            <p>
              <strong>The recording is not in this build.</strong>
            </p>
            <p>
              Produce it with <code>pnpm dev</code> in one terminal and{" "}
              <code>node --experimental-strip-types scripts/record-demo.ts</code> in another, then
              rebuild. The scene cards below describe the same four scenarios the script drives;
              the interactive version is on the demo page.
            </p>
          </div>
        ) : (
          <video
            ref={videoRef}
            className="demo-video"
            data-testid="demo-video"
            controls
            playsInline
            preload="metadata"
            aria-describedby="demo-video-caption"
            onTimeUpdate={(event) => setPlayhead(event.currentTarget.currentTime * 1000)}
            onLoadedMetadata={() => setMedia({ status: "ready" })}
            onError={() => setMedia({ status: "missing" })}
          >
            <source src={videoUrl} type="video/webm" />
            <source src={DEMO_VIDEO_MP4_URL} type="video/mp4" />
            Your browser cannot play video. The scene cards below summarise the same
            walkthrough.
          </video>
        )}
      </div>

      <p className="video-caption" id="demo-video-caption" data-testid="demo-video-caption">
        {media.status === "ready" ? "Playing" : "Walkthrough"} · fixture data ·{" "}
        {Number.isFinite(totalMs) ? `${formatTimestamp(totalMs)} total` : "timing unknown"} ·{" "}
        {scenes.length} scenes · Ryan (Neural) voiceover narration
      </p>

      <p className="video-downloads" style={{ margin: "0.5rem 0 1rem", fontSize: "0.9rem" }}>
        <strong>Direct video files:</strong>{" "}
        <a href={videoUrl} download>Download WebM (4.7 MB)</a> ·{" "}
        <a href={DEMO_VIDEO_MP4_URL} download>Download MP4 (3.3 MB)</a>
      </p>

      <ol className="scene-cards" data-testid="scene-cards">
        {scenes.map((scene, index) => {
          const isActive = timed && index === active;
          return (
            <li key={scene.id}>
              <button
                type="button"
                className={`scene-card${isActive ? " scene-card-active" : ""}`}
                data-testid={`scene-card-${scene.id}`}
                data-active={isActive ? "yes" : "no"}
                aria-current={isActive ? "step" : undefined}
                aria-label={`${scene.cardTitle ?? scene.title} — ${scene.narration}`}
                disabled={!Number.isFinite(scene.startMs)}
                onClick={() => seek(scene)}
              >
                <span className="scene-card-head">
                  <span className="scene-card-index">{index + 1}</span>
                  <span className="scene-card-title">{scene.cardTitle ?? scene.title}</span>
                  <span className="scene-card-time">
                    {Number.isFinite(scene.startMs) ? formatTimestamp(scene.startMs) : "--:--"}
                  </span>
                </span>
                <span className="scene-card-narration">{scene.narration}</span>
                {scene.assertions && scene.assertions.length > 0 ? (
                  <span className="scene-card-assertions">
                    Checks: {scene.assertions.join(" · ")}
                  </span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ol>

      <p className="note">
        {timed
          ? "Scene boundaries come from the recorder's cue sheet, so the highlight cannot drift from the picture."
          : "Cue timings are unavailable in this build, so the cards are a transcript rather than a seek bar."}{" "}
        The video is silent: narration is written for a voice-over and is reproduced in each card.
      </p>
    </section>
  );
}
