/**
 * "Map View" tab: the coarse-area view a coordinator is allowed to see.
 *
 * There is deliberately no map imagery here. The coordinator console receives a geohash cell, not
 * coordinates, and never receives Ring frames, so drawing a street map would invent location
 * precision the product does not have. What this tab can honestly show is the cell, the tree, the
 * forecast window, and the boundary of what is hidden — which is also where a coordinator checks
 * that a candidate is close enough before approving an assignment.
 */

import type { ReactElement } from "react";

import type { MatchingBoard } from "../domain/types";

function formatDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toISOString().slice(0, 10);
}

export function CoarseAreaView({ board }: { readonly board: MatchingBoard }): ReactElement {
  const { forecast } = board;

  return (
    <section aria-labelledby="map-heading" className="card" data-testid="map-view">
      <h2 id="map-heading">Coarse area · {forecast.coarseArea}</h2>
      <p className="note">
        A geohash cell, not an address. The console never receives exact coordinates, owner
        contact details, or camera frames; the ROI polygon and mask editor live in the owner-facing
        demo, not in the coordinator view.
      </p>

      <div className="area-grid">
        <figure className="area-cell">
          <svg
            viewBox="0 0 240 180"
            role="img"
            aria-label={`Coarse area ${forecast.coarseArea}`}
            data-testid="coarse-area-cell"
          >
            <defs>
              <pattern id="cell-grid" width="20" height="20" patternUnits="userSpaceOnUse">
                <path d="M 20 0 L 0 0 0 20" fill="none" stroke="var(--color-border)" strokeWidth="1" />
              </pattern>
            </defs>
            <rect width="240" height="180" fill="var(--color-surface-alt)" />
            <rect width="240" height="180" fill="url(#cell-grid)" />
            <rect
              x="70"
              y="50"
              width="100"
              height="80"
              fill="var(--color-primary)"
              fillOpacity="0.12"
              stroke="var(--color-primary)"
              strokeWidth="2.5"
              strokeDasharray="6 4"
            />
            <text x="120" y="96" textAnchor="middle" className="area-cell-label">
              {forecast.coarseArea}
            </text>
            <text x="120" y="146" textAnchor="middle" className="area-cell-note">
              one geohash cell · exact coordinates withheld
            </text>
          </svg>
          <figcaption>
            Cell <strong>{forecast.coarseArea}</strong> for tree{" "}
            <strong>{forecast.treeId}</strong>. Neighbouring cells are not shown because the
            console never receives them.
          </figcaption>
        </figure>

        <dl className="facts">
          <div>
            <dt>Tree</dt>
            <dd>{forecast.treeId}</dd>
          </div>
          <div>
            <dt>Ready window</dt>
            <dd>
              <time dateTime={forecast.readyStart}>{formatDate(forecast.readyStart)}</time> to{" "}
              <time dateTime={forecast.readyEnd}>{formatDate(forecast.readyEnd)}</time>
            </dd>
          </div>
          <div>
            <dt>Visible clusters</dt>
            <dd>
              {forecast.visibleCount} (range {forecast.countLower}–{forecast.countUpper}) — visible
              only, never a weight or ripeness claim
            </dd>
          </div>
          <div>
            <dt>Owner approval</dt>
            <dd>
              {board.ownerApprovalCurrent ? "Current" : "Not current — matching is blocked"}
            </dd>
          </div>
          <div>
            <dt>Candidates</dt>
            <dd>
              {board.candidates.length} ranked for this glean — eligibility is decided in Gleaning
              Dispatch, where the blockers are listed
            </dd>
          </div>
        </dl>
      </div>

      <p className="note" data-testid="map-boundary-note">
        Travel fit is computed from the volunteer's coarse area to this cell, with no street address
        on either side. Exact access details appear only to a confirmed, assigned volunteer inside
        the reveal window.
      </p>
    </section>
  );
}
