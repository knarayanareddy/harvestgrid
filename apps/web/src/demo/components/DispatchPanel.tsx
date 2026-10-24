/**
 * Scenario 4 — coordinator approval and volunteer dispatch.
 *
 * A judge ticks volunteers, confirms the readiness checklist, and dispatches. The panel then
 * shows what actually left the system: per-recipient status, a masked phone number, the message
 * segment count, and the SHA-256 of the body. Neither the body nor the phone number is displayed
 * in full, because the API this mirrors never returns them either.
 */

import { useState, type ReactElement } from "react";

import type { DemoStore, DemoState } from "../demoStore";

export function DispatchPanel({
  store,
  state,
}: {
  readonly store: DemoStore;
  readonly state: DemoState;
}): ReactElement {
  const [selected, setSelected] = useState<readonly string[]>([]);
  const { glean, volunteers } = state;
  const canDispatch = store.canDispatch();

  const toggle = (volunteerId: string): void => {
    const next = selected.includes(volunteerId)
      ? selected.filter((id) => id !== volunteerId)
      : [...selected, volunteerId];
    setSelected(next);
    store.setAssignments(next);
  };

  return (
    <section aria-labelledby="dispatch-heading" className="card" data-testid="dispatch-panel">
      <h2 id="dispatch-heading">4 · Coordinate and dispatch</h2>
      <p className="note">
        No notice is sent automatically. Coordinator readiness, a current owner approval, an
        approved privacy view, and explicit assignment approval are all required first.
      </p>

      <fieldset className="masks">
        <legend>Assign volunteers</legend>
        {volunteers.map((volunteer) => (
          <label key={volunteer.volunteerId} className="check">
            <input
              type="checkbox"
              checked={selected.includes(volunteer.volunteerId)}
              onChange={() => toggle(volunteer.volunteerId)}
              data-testid={`assign-${volunteer.volunteerId}`}
            />
            <span>
              {volunteer.displayName} · {volunteer.travelNote} · {store.maskedPhone(volunteer.volunteerId)}
              {volunteer.smsOptIn ? "" : " · SMS opt-out"}
            </span>
          </label>
        ))}
      </fieldset>

      <label className="check">
        <input
          type="checkbox"
          checked={glean.coordinatorReadinessConfirmed}
          onChange={(event) => store.confirmReadiness(event.target.checked)}
          data-testid="readiness-confirm"
        />
        <span>I confirm the human readiness checklist (visible estimate only)</span>
      </label>

      <p className="note" data-testid="dispatch-gate">
        {canDispatch
          ? "Gate satisfied — dispatch is allowed."
          : "Gate closed: approve the view, confirm readiness, and assign at least one volunteer."}
      </p>
      <button
        type="button"
        className="primary"
        disabled={!canDispatch}
        onClick={() => store.dispatch()}
        data-testid="dispatch-button"
      >
        Dispatch volunteer notices
      </button>

      {glean.dispatch === null ? (
        <p className="note" data-testid="dispatch-empty">
          No dispatch yet.
        </p>
      ) : (
        <div data-testid="dispatch-results">
          <p className="lede" data-testid="dispatch-summary">
            {glean.dispatch.sent} sent · {glean.dispatch.skipped} skipped · {glean.dispatch.deferred}{" "}
            deferred · template {glean.dispatch.templateVersion}
          </p>
          <table className="board">
            <caption>Per-recipient outcome. Bodies and phone numbers are never shown.</caption>
            <thead>
              <tr>
                <th scope="col">Volunteer</th>
                <th scope="col">Phone</th>
                <th scope="col">Status</th>
                <th scope="col">Reason</th>
                <th scope="col">Segments</th>
                <th scope="col">Body SHA-256</th>
              </tr>
            </thead>
            <tbody>
              {glean.dispatch.outcomes.map((outcome) => (
                <tr key={outcome.volunteerId} data-testid={`outcome-${outcome.volunteerId}`}>
                  <td>{outcome.displayName}</td>
                  <td>{outcome.maskedPhone}</td>
                  <td>{outcome.status}</td>
                  <td>{outcome.reason.replaceAll("_", " ")}</td>
                  <td>{outcome.segments}</td>
                  <td className="digest">{outcome.bodySha256?.slice(0, 16) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="note">
            Report digest <code data-testid="dispatch-digest">{glean.dispatch.resultSha256.slice(0, 24)}…</code>
          </p>
        </div>
      )}
    </section>
  );
}
