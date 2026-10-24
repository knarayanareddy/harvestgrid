import { useCallback, useEffect, useState, type ReactElement } from "react";

import type { CoordinatorApi } from "./api/CoordinatorApi";
import { DecisionSummary } from "./components/DecisionSummary";
import { MatchingBoard } from "./components/MatchingBoard";
import { ReceiptForm } from "./components/ReceiptForm";
import type {
  DonationReceipt,
  FieldError,
  MatchingBoard as Board,
  ReceiptInput,
} from "./domain/types";

type LoadState =
  | { readonly status: "loading" }
  | { readonly status: "error"; readonly message: string }
  | { readonly status: "ready"; readonly board: Board };

export function App({ api }: { readonly api: CoordinatorApi }): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: "loading" });
  const [approvalMessage, setApprovalMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<DonationReceipt | null>(null);
  const [errors, setErrors] = useState<readonly FieldError[]>([]);

  const refresh = useCallback(async () => {
    setLoad({ status: "loading" });
    try {
      const board = await api.loadBoard();
      setLoad({ status: "ready", board });
    } catch (error) {
      setLoad({
        status: "error",
        message: error instanceof Error ? error.message : "The board could not be loaded.",
      });
    }
  }, [api]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const approve = async (volunteerIds: readonly string[]): Promise<void> => {
    if (load.status !== "ready") return;
    setBusy(true);
    setApprovalMessage(null);
    try {
      const assignments = await api.approveAssignments({
        gleanId: load.board.gleanId,
        volunteerIds,
        coordinatorId: api.session.actorId,
        revealNotBefore: "2026-10-30T08:00:00Z",
        revealUntil: "2026-10-30T18:00:00Z",
        idempotencyKey: `assignments/${load.board.gleanId}/${volunteerIds.join(",")}`,
      });
      setApprovalMessage(
        `${assignments.length} assignment(s) approved and recorded. Exact access details remain hidden until each volunteer confirms.`,
      );
    } catch (error) {
      setApprovalMessage(
        error instanceof Error ? error.message : "Assignments could not be approved.",
      );
    } finally {
      setBusy(false);
    }
  };

  const submitReceipt = async (input: ReceiptInput): Promise<void> => {
    setBusy(true);
    setErrors([]);
    setReceipt(null);
    try {
      const result = await api.recordReceipt(input);
      if (result.ok) {
        setReceipt(result.value);
      } else {
        setErrors(result.errors);
      }
    } catch (error) {
      setErrors([
        {
          path: "receipt",
          code: "request_failed",
          message: error instanceof Error ? error.message : "The receipt could not be recorded.",
        },
      ]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to coordinator work area
      </a>
      <header className="masthead">
        <p className="eyebrow">HarvestGrid coordinator console</p>
        <h1>Match volunteers, then log the weighed outcome</h1>
        <p className="session">
          Signed in as <strong>{api.session.actorId}</strong> · roles{" "}
          {api.session.roles.join(", ")} · organization {api.session.organizationId}
        </p>
        {load.status === "ready" && load.board.provenance === "fixture" ? (
          <p className="banner banner-fixture" role="status">
            FIXTURE DATA — no Ring device, weather service, media pipeline, or database is
            connected. Nothing here is live evidence.
          </p>
        ) : null}
      </header>
      <main id="main">
        {load.status === "loading" ? (
          <p role="status">Loading the matching board…</p>
        ) : load.status === "error" ? (
          <section className="card" role="alert">
            <h2>The board could not be loaded</h2>
            <p>{load.message}</p>
            <button type="button" className="primary" onClick={() => void refresh()}>
              Retry
            </button>
          </section>
        ) : (
          <>
            <DecisionSummary
              forecast={load.board.forecast}
              ownerApprovalCurrent={load.board.ownerApprovalCurrent}
            />
            <MatchingBoard
              board={load.board}
              roles={api.session.roles}
              onApprove={(volunteerIds) => void approve(volunteerIds)}
              busy={busy}
              message={approvalMessage}
            />
            <ReceiptForm
              roles={api.session.roles}
              onSubmit={(input) => void submitReceipt(input)}
              receipt={receipt}
              errors={errors}
              busy={busy}
            />
          </>
        )}
      </main>
      <footer className="colophon">
        <p>
          Visual estimates never assert internal ripeness or food safety, and volunteers are
          never dispatched automatically. Exact addresses appear only for a confirmed,
          assigned volunteer inside the reveal window.
        </p>
      </footer>
    </div>
  );
}
