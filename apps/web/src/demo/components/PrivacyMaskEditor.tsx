/**
 * Scenario 1b + 2 — privacy view editor with the simulated Ring frame on top.
 *
 * Dragging a vertex changes the ROI polygon; the mask rectangles mark neighbour property and
 * public sidewalk. Both are applied *geometrically*, before any detection can become a feature
 * row, so a cluster whose box does not sit mostly inside the allowed region is dropped here.
 * The SVG is the only "map" the demo has: coarse geohash plus a frame-local drawing, never an
 * address or a real photograph.
 */

import { useRef, useState, type PointerEvent as ReactPointerEvent, type ReactElement } from "react";

import { MASK_LABELS, type MaskKind } from "../geometry";
import { RIPENESS_COLORS, RIPENESS_LABELS } from "../detections";
import type { DemoStore, DemoState } from "../demoStore";

const VIEW_WIDTH = 480;
const VIEW_HEIGHT = 360;

export function PrivacyMaskEditor({
  store,
  state,
}: {
  readonly store: DemoStore;
  readonly state: DemoState;
}): ReactElement {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [dragging, setDragging] = useState<number | null>(null);
  const { view, validation, webhook } = state;

  const toNormalized = (clientX: number, clientY: number): { x: number; y: number } => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0 || rect.height === 0) return { x: 0, y: 0 };
    return {
      x: (clientX - rect.left) / rect.width,
      y: (clientY - rect.top) / rect.height,
    };
  };

  const handlePointerDown = (
    index: number,
    event: ReactPointerEvent<SVGCircleElement>,
  ): void => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(index);
  };

  const handlePointerMove = (event: ReactPointerEvent<SVGSVGElement>): void => {
    if (dragging === null) return;
    store.moveRoiPoint(dragging, toNormalized(event.clientX, event.clientY));
  };

  const endDrag = (): void => setDragging(null);

  const maskKinds: readonly MaskKind[] = [
    "neighbor_property",
    "public_sidewalk",
    "street",
    "other_unrelated",
  ];

  return (
    <section aria-labelledby="privacy-heading" className="card" data-testid="privacy-panel">
      <h2 id="privacy-heading">2 · Privacy ROI and mask boundary</h2>
      <p className="note">
        Everything outside the polygon, and everything inside a mask, is discarded before feature
        extraction. Drag any vertex — the approvals reset, because the geometry changed.
      </p>
      <div className="map-wrap">
        <svg
          ref={svgRef}
          className="privacy-map"
          viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
          role="img"
          aria-label={`Camera frame for coarse area ${state.tree.coarseGeohash}`}
          data-testid="privacy-map"
          onPointerMove={handlePointerMove}
          onPointerUp={endDrag}
          onPointerLeave={endDrag}
        >
          <defs>
            <pattern id="frame-grid" width="40" height="40" patternUnits="userSpaceOnUse">
              <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#DCE3DA" strokeWidth="1" />
            </pattern>
            <clipPath id="roi-clip">
              <polygon
                points={view.roi
                  .map((point) => `${point.x * VIEW_WIDTH},${point.y * VIEW_HEIGHT}`)
                  .join(" ")}
              />
            </clipPath>
          </defs>
          <rect width={VIEW_WIDTH} height={VIEW_HEIGHT} fill="#F7F5EF" />
          <rect width={VIEW_WIDTH} height={VIEW_HEIGHT} fill="url(#frame-grid)" />
          <text x="12" y="22" className="map-caption">
            coarse area {state.tree.coarseGeohash} · frame 480×360 · no media stored
          </text>

          {/* Allowed region: ROI with masks cut out. */}
          <g clipPath="url(#roi-clip)">
            <rect width={VIEW_WIDTH} height={VIEW_HEIGHT} fill="#E4EFE2" opacity="0.85" />
            {view.masks.map((mask) => (
              <rect
                key={mask.id}
                x={mask.x * VIEW_WIDTH}
                y={mask.y * VIEW_HEIGHT}
                width={mask.width * VIEW_WIDTH}
                height={mask.height * VIEW_HEIGHT}
                fill="#8E3539"
                opacity="0.22"
                data-testid={`mask-rect-${mask.id}`}
              />
            ))}
            {webhook !== null
              ? webhook.frame.kept.map((cluster) => (
                  <g key={cluster.clusterId} data-testid={`cluster-kept-${cluster.clusterId}`}>
                    <ellipse
                      cx={cluster.centerX * VIEW_WIDTH}
                      cy={cluster.centerY * VIEW_HEIGHT}
                      rx={(cluster.width * VIEW_WIDTH) / 2}
                      ry={(cluster.height * VIEW_HEIGHT) / 2}
                      fill={RIPENESS_COLORS[cluster.ripeness]}
                      fillOpacity="0.55"
                      stroke={RIPENESS_COLORS[cluster.ripeness]}
                      strokeWidth="1.5"
                    />
                    <text
                      x={cluster.centerX * VIEW_WIDTH}
                      y={cluster.centerY * VIEW_HEIGHT - (cluster.height * VIEW_HEIGHT) / 2 - 4}
                      className="cluster-label"
                    >
                      {cluster.confidence.toFixed(2)}
                    </text>
                  </g>
                ))
              : null}
          </g>

          {/* Dropped clusters stay visible as hollow markers so the filter is provable. */}
          {webhook !== null
            ? webhook.frame.dropped.map((cluster) => (
                <g key={cluster.clusterId} data-testid={`cluster-dropped-${cluster.clusterId}`}>
                  <ellipse
                    cx={cluster.centerX * VIEW_WIDTH}
                    cy={cluster.centerY * VIEW_HEIGHT}
                    rx={(cluster.width * VIEW_WIDTH) / 2}
                    ry={(cluster.height * VIEW_HEIGHT) / 2}
                    fill="none"
                    stroke="#8E3539"
                    strokeDasharray="4 3"
                    strokeWidth="1.25"
                  />
                  <line
                    x1={cluster.centerX * VIEW_WIDTH - 6}
                    y1={cluster.centerY * VIEW_HEIGHT - 6}
                    x2={cluster.centerX * VIEW_WIDTH + 6}
                    y2={cluster.centerY * VIEW_HEIGHT + 6}
                    stroke="#8E3539"
                    strokeWidth="1.5"
                  />
                  <line
                    x1={cluster.centerX * VIEW_WIDTH + 6}
                    y1={cluster.centerY * VIEW_HEIGHT - 6}
                    x2={cluster.centerX * VIEW_WIDTH - 6}
                    y2={cluster.centerY * VIEW_HEIGHT + 6}
                    stroke="#8E3539"
                    strokeWidth="1.5"
                  />
                </g>
              ))
            : null}

          <polygon
            points={view.roi
              .map((point) => `${point.x * VIEW_WIDTH},${point.y * VIEW_HEIGHT}`)
              .join(" ")}
            fill="none"
            stroke="#2E5D46"
            strokeWidth="2.5"
            data-testid="roi-polygon"
          />
          {view.roi.map((point, index) => (
            <circle
              key={`vertex-${index}`}
              cx={point.x * VIEW_WIDTH}
              cy={point.y * VIEW_HEIGHT}
              r={9}
              fill="#FFFFFF"
              stroke="#1D5F8A"
              strokeWidth="2.5"
              className="roi-vertex"
              role="button"
              tabIndex={0}
              aria-label={`ROI vertex ${index + 1}`}
              data-testid={`roi-vertex-${index}`}
              onPointerDown={(event) => handlePointerDown(index, event)}
              onKeyDown={(event) => {
                const step = event.shiftKey ? 0.01 : 0.04;
                const delta =
                  event.key === "ArrowLeft"
                    ? { x: -step, y: 0 }
                    : event.key === "ArrowRight"
                      ? { x: step, y: 0 }
                      : event.key === "ArrowUp"
                        ? { x: 0, y: -step }
                        : event.key === "ArrowDown"
                          ? { x: 0, y: step }
                          : null;
                if (delta === null) return;
                event.preventDefault();
                store.moveRoiPoint(index, { x: point.x + delta.x, y: point.y + delta.y });
              }}
            />
          ))}
        </svg>
        <div className="map-side">
          <p
            className={`banner ${validation.ok ? "banner-ok" : "banner-warning"}`}
            role="status"
            data-testid="view-status"
            data-valid={validation.ok ? "true" : "false"}
          >
            {validation.ok ? "✔ " : "▲ "}
            {validation.message}
          </p>
          <dl className="facts facts-compact">
            <div>
              <dt>ROI area</dt>
              <dd data-testid="roi-area">{(validation.roiArea * 100).toFixed(1)}% of frame</dd>
            </div>
            <div>
              <dt>Masked share of ROI</dt>
              <dd data-testid="mask-coverage">{(validation.maskCoverage * 100).toFixed(1)}%</dd>
            </div>
            <div>
              <dt>Allowed region</dt>
              <dd>{(validation.allowedFraction * 100).toFixed(1)}% of frame</dd>
            </div>
            <div>
              <dt>Approvals</dt>
              <dd data-testid="view-approvals">
                owner {view.ownerApproved ? "✔" : "✖"} · coordinator{" "}
                {view.coordinatorApproved ? "✔" : "✖"}
              </dd>
            </div>
          </dl>
          <div className="row">
            <button type="button" onClick={() => store.approveView("owner")} data-testid="approve-owner">
              Owner approves view
            </button>
            <button
              type="button"
              onClick={() => store.approveView("coordinator")}
              data-testid="approve-coordinator"
            >
              Coordinator approves view
            </button>
          </div>
          <fieldset className="masks">
            <legend>Exclusion masks</legend>
            {view.masks.map((mask) => (
              <div key={mask.id} className="mask-row">
                <span>
                  {MASK_LABELS[mask.kind]} · x {mask.x.toFixed(2)} · w {mask.width.toFixed(2)}
                </span>
                <button
                  type="button"
                  className="link"
                  onClick={() => store.removeMask(mask.id)}
                  data-testid={`remove-${mask.id}`}
                >
                  Remove
                </button>
              </div>
            ))}
            <div className="row">
              {maskKinds.slice(0, 2).map((kind) => (
                <button
                  key={kind}
                  type="button"
                  onClick={() => store.addMask(kind)}
                  data-testid={`add-mask-${kind}`}
                >
                  Add {MASK_LABELS[kind].toLowerCase()} mask
                </button>
              ))}
            </div>
            <label className="field">
              <span>Neighbour mask width</span>
              <input
                type="range"
                min={5}
                max={60}
                step={1}
                value={Math.round((view.masks[0]?.width ?? 0.3) * 100)}
                aria-label="Neighbour mask width"
                data-testid="mask-width"
                onChange={(event) => {
                  const mask = view.masks[0];
                  if (!mask) return;
                  store.setMaskRectangle(mask.id, {
                    ...mask,
                    width: Number(event.target.value) / 100,
                  });
                }}
              />
            </label>
          </fieldset>
        </div>
      </div>

      <div className="webhook-row">
        <button type="button" className="primary" onClick={() => store.simulateWebhook()} data-testid="simulate-webhook">
          Simulate Ring webhook (HMAC verified)
        </button>
        {webhook === null ? (
          <p className="note">No frame received yet.</p>
        ) : (
          <p className="note" data-testid="webhook-summary">
            {webhook.eventId} · device {webhook.deviceId} · HMAC verified ·{" "}
            <strong data-testid="kept-count">{webhook.frame.kept.length} kept</strong> ·{" "}
            <strong data-testid="dropped-count">{webhook.frame.dropped.length} excluded</strong> ·{" "}
            {(webhook.ripeShare * 100).toFixed(0)}% visible clusters ripe or overripe
          </p>
        )}
      </div>
      {webhook !== null ? (
        <ul className="chips" aria-label="Kept clusters">
          {webhook.frame.kept.map((cluster) => (
            <li key={cluster.clusterId} style={{ borderColor: RIPENESS_COLORS[cluster.ripeness] }}>
              {cluster.clusterId} · {RIPENESS_LABELS[cluster.ripeness]} · {cluster.confidence.toFixed(2)}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
