import { useMemo, useState, type ReactElement } from "react";

import { BLOCKER_LABELS, boardRows, canApproveAssignments } from "../domain/matching";
import type { Candidate, MatchingBoard as Board, MembershipRole } from "../domain/types";

function scoreText(score: number | null): string {
  return score === null ? "—" : score.toFixed(2);
}

export function MatchingBoard({
  board,
  roles,
  onApprove,
  busy,
  message,
}: {
  readonly board: Board;
  readonly roles: readonly MembershipRole[];
  readonly onApprove: (volunteerIds: readonly string[]) => void;
  readonly busy: boolean;
  readonly message: string | null;
}): ReactElement {
  const [selection, setSelection] = useState<readonly string[]>([]);
  const rows = useMemo(
    () => boardRows(board.candidates, board.serverTieKeys),
    [board.candidates, board.serverTieKeys],
  );
  const approval = canApproveAssignments(board, selection, board.candidates);
  const coordinator = roles.includes("coordinator");

  const toggle = (candidate: Candidate, eligible: boolean): void => {
    if (!eligible) return;
    setSelection((current) =>
      current.includes(candidate.volunteerId)
        ? current.filter((id) => id !== candidate.volunteerId)
        : [...current, candidate.volunteerId],
    );
  };

  return (
    <section aria-labelledby="board-heading" className="card">
      <h2 id="board-heading">Matching board</h2>
      <p className="note">
        Deterministic hard filters and ranking. Selecting a row never reveals an address:
        exact details unlock only for a confirmed assigned volunteer inside the reveal window.
      </p>
      <table className="board">
        <caption>
          Eligible volunteers are ranked by the documented weights; ineligible volunteers
          show their blockers and cannot be selected.
        </caption>
        <thead>
          <tr>
            <th scope="col">Select</th>
            <th scope="col">Volunteer</th>
            <th scope="col">Training</th>
            <th scope="col">Eligibility</th>
            <th scope="col">Score</th>
            <th scope="col">Explanation</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const candidate = board.candidates.find(
              (item) => item.volunteerId === row.volunteerId,
            );
            if (!candidate) return null;
            const checked = selection.includes(row.volunteerId);
            return (
              <tr key={row.volunteerId} data-eligible={row.eligible ? "yes" : "no"}>
                <td>
                  <input
                    type="checkbox"
                    id={`select-${row.volunteerId}`}
                    checked={checked}
                    disabled={!row.eligible || !coordinator}
                    onChange={() => toggle(candidate, row.eligible)}
                  />
                  <label className="visually-hidden" htmlFor={`select-${row.volunteerId}`}>
                    Select {row.displayName}
                  </label>
                </td>
                <th scope="row">
                  {row.displayName}
                  <span className="muted"> ({row.volunteerId})</span>
                </th>
                <td>{candidate.trainingLevel.replaceAll("_", " ")}</td>
                <td>
                  {row.eligible ? (
                    <span className="pill pill-ok">✔ Eligible</span>
                  ) : (
                    <span className="pill pill-blocked">🔒 Not eligible</span>
                  )}
                </td>
                <td className="numeric">{scoreText(row.score)}</td>
                <td>
                  {row.eligible && row.components ? (
                    <ul className="components">
                      <li>travel {row.components.travelFit.toFixed(2)}</li>
                      <li>schedule {row.components.scheduleFit.toFixed(2)}</li>
                      <li>recipient {row.components.recipientLogisticsFit.toFixed(2)}</li>
                      <li>equipment {row.components.equipmentFit.toFixed(2)}</li>
                      <li>fairness {row.components.fairnessRotation.toFixed(2)}</li>
                    </ul>
                  ) : (
                    <ul className="blockers">
                      {row.blockers.map((blocker) => (
                        <li key={blocker}>{BLOCKER_LABELS[blocker]}</li>
                      ))}
                    </ul>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="actions">
        <button
          type="button"
          className="primary"
          disabled={!approval.allowed || busy}
          onClick={() => onApprove(selection)}
        >
          Approve selected assignments
        </button>
        <p className="note" role="status" aria-live="polite">
          {approval.allowed
            ? `${selection.length} volunteer(s) selected for coordinator approval.`
            : approval.reason}
        </p>
      </div>
      {message ? (
        <p className="note" role="status" aria-live="polite">
          {message}
        </p>
      ) : null}
    </section>
  );
}
