import type { ReactElement } from "react";

import type { ForecastSummary } from "../domain/types";
import { StatusBadge } from "./StatusBadge";

function formatDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toISOString().slice(0, 10);
}

export function DecisionSummary({
  forecast,
  ownerApprovalCurrent,
}: {
  readonly forecast: ForecastSummary;
  readonly ownerApprovalCurrent: boolean;
}): ReactElement {
  return (
    <section aria-labelledby="decision-heading" className="card">
      <h2 id="decision-heading">Forecast and readiness</h2>
      <p className="lede">
        <StatusBadge status={forecast.status} />{" "}
        {forecast.experimental
          ? "Experimental baseline forecast — human readiness review is mandatory."
          : "Reviewed forecast — human readiness review is still mandatory."}
      </p>
      <dl className="facts">
        <div>
          <dt>Tree</dt>
          <dd>
            {forecast.treeId} · coarse area {forecast.coarseArea}
          </dd>
        </div>
        <div>
          <dt>Ready window</dt>
          <dd>
            <time dateTime={forecast.readyStart}>{formatDate(forecast.readyStart)}</time> to{" "}
            <time dateTime={forecast.readyEnd}>{formatDate(forecast.readyEnd)}</time>
          </dd>
        </div>
        <div>
          <dt>Visible fruit estimate</dt>
          <dd>
            {forecast.visibleCount} clusters (range {forecast.countLower}–{forecast.countUpper})
            — visible only, not a weight or ripeness claim
          </dd>
        </div>
        <div>
          <dt>Model and data versions</dt>
          <dd>
            {forecast.modelVersion} · data {forecast.dataVersion}
          </dd>
        </div>
        <div>
          <dt>Owner approval</dt>
          <dd>{ownerApprovalCurrent ? "Current" : "Not current — matching is blocked"}</dd>
        </div>
      </dl>
      <h3>Confidence reasons</h3>
      <ul className="reasons">
        {forecast.confidenceReasons.map((reason) => (
          <li key={reason}>{reason.replaceAll("_", " ")}</li>
        ))}
      </ul>
      <p className="note">
        Visual estimates cannot establish internal ripeness, disease, or food safety. The
        coordinator checklist governs operational decisions.
      </p>
    </section>
  );
}
