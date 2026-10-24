/**
 * Status grammar from ui/DESIGN.md §4: icon **and** text, never colour alone.
 */

import type { ReactElement } from "react";

import type { DecisionStatus } from "../domain/types";

const STATUS_PRESENTATION: Readonly<
  Record<DecisionStatus, { symbol: string; label: string; tone: string }>
> = {
  go: { symbol: "✔", label: "Go", tone: "status-go" },
  no_go: { symbol: "✖", label: "No-go", tone: "status-no-go" },
  conditional: { symbol: "▲", label: "Conditional", tone: "status-conditional" },
  review_required: { symbol: "◆", label: "Review required", tone: "status-review" },
  stale: { symbol: "🕒", label: "Stale", tone: "status-stale" },
  blocked: { symbol: "🔒", label: "Blocked", tone: "status-blocked" },
  draft: { symbol: "▧", label: "Offline draft", tone: "status-draft" },
};

export function StatusBadge({ status }: { readonly status: DecisionStatus }): ReactElement {
  const presentation = STATUS_PRESENTATION[status];
  return (
    <span className={`status-badge ${presentation.tone}`} data-status={status}>
      <span aria-hidden="true" className="status-symbol">
        {presentation.symbol}
      </span>
      <span className="status-label">{presentation.label}</span>
    </span>
  );
}
