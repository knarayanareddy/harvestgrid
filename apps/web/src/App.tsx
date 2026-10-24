import { useCallback, useEffect, useRef, useState, type ReactElement } from "react";

import type { CoordinatorApi } from "./api/CoordinatorApi";
import { CoarseAreaView } from "./components/CoarseAreaView";
import { DecisionSummary } from "./components/DecisionSummary";
import { DemoVideo } from "./components/DemoVideo";
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

type TabId = "map" | "yields" | "dispatch" | "video";

const TABS: readonly { readonly id: TabId; readonly label: string }[] = [
  { id: "map", label: "Map View" },
  { id: "yields", label: "Yield Forecasts" },
  { id: "dispatch", label: "Gleaning Dispatch" },
  { id: "video", label: "Demo Video" },
];

const TAB_IDS = TABS.map((tab) => tab.id);

/**
 * The coordinator console's navigation.
 *
 * "Gleaning Dispatch" is the default because it is the work a coordinator comes to do; the other
 * tabs are context. Every panel stays mounted and is hidden with the `hidden` attribute rather
 * than unmounted, so a half-typed receipt survives a tab change and the board is not refetched
 * when a coordinator checks the forecast mid-approval.
 */
function tabIdOf(tab: TabId): string {
  return `tab-${tab}`;
}

function panelIdOf(tab: TabId): string {
  return `panel-${tab}`;
}

function getInitialTab(): TabId {
  if (typeof window !== "undefined") {
    const hash = window.location.hash.toLowerCase();
    if (hash === "#video" || hash === "#demo-video" || hash === "#tab-video") return "video";
    const params = new URLSearchParams(window.location.search);
    if (params.get("tab") === "video") return "video";
  }
  return "dispatch";
}

export function App({ api }: { readonly api: CoordinatorApi }): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: "loading" });
  const [approvalMessage, setApprovalMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<DonationReceipt | null>(null);
  const [errors, setErrors] = useState<readonly FieldError[]>([]);
  const [tab, setTab] = useState<TabId>(getInitialTab);
  const tabRefs = useRef<Partial<Record<TabId, HTMLButtonElement | null>>>({});

  const selectTab = (next: TabId): void => {
    setTab(next);
    // Roving focus: moving selection with the keyboard must take focus with it, or the next
    // arrow key acts on the control that no longer represents the visible panel.
    tabRefs.current[next]?.focus();
  };

  const handleTabKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    const current = TAB_IDS.indexOf(tab);
    if (current === -1) return;
    let next = current;
    if (event.key === "ArrowRight") next = (current + 1) % TAB_IDS.length;
    else if (event.key === "ArrowLeft") next = (current - 1 + TAB_IDS.length) % TAB_IDS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TAB_IDS.length - 1;
    else return;
    event.preventDefault();
    const target = TAB_IDS[next] as TabId;
    selectTab(target);
  };

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

  useEffect(() => {
    if (typeof window === "undefined") return;
    const onHashChange = (): void => {
      const hash = window.location.hash.toLowerCase();
      if (hash === "#video" || hash === "#demo-video" || hash === "#tab-video") {
        setTab("video");
      }
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

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
        <div style={{ margin: "0.75rem 0", display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <button
            type="button"
            className="primary"
            onClick={() => setTab("video")}
            style={{
              padding: "0.4rem 0.85rem",
              fontSize: "0.9rem",
              display: "inline-flex",
              alignItems: "center",
              gap: "0.4rem",
              cursor: "pointer",
            }}
          >
            🎥 Watch Demo Video (38s Walkthrough with Ryan Neural Voice)
          </button>
        </div>
      </header>
      <nav className="tabs-nav" aria-label="Coordinator views">
        <div
          className="tabs"
          role="tablist"
          aria-label="Coordinator views"
          onKeyDown={handleTabKeyDown}
        >
          {TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              id={tabIdOf(item.id)}
              className="tab"
              aria-selected={tab === item.id}
              aria-controls={panelIdOf(item.id)}
              tabIndex={tab === item.id ? 0 : -1}
              data-testid={`tab-${item.id}`}
              ref={(element) => {
                tabRefs.current[item.id] = element;
              }}
              onClick={() => setTab(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </nav>
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
            <div
              role="tabpanel"
              id={panelIdOf("map")}
              aria-labelledby={tabIdOf("map")}
              hidden={tab !== "map"}
              tabIndex={0}
            >
              <CoarseAreaView board={load.board} />
            </div>
            <div
              role="tabpanel"
              id={panelIdOf("yields")}
              aria-labelledby={tabIdOf("yields")}
              hidden={tab !== "yields"}
              tabIndex={0}
            >
              <DecisionSummary
                forecast={load.board.forecast}
                ownerApprovalCurrent={load.board.ownerApprovalCurrent}
              />
            </div>
            <div
              role="tabpanel"
              id={panelIdOf("dispatch")}
              aria-labelledby={tabIdOf("dispatch")}
              hidden={tab !== "dispatch"}
              tabIndex={0}
            >
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
            </div>
            <div
              role="tabpanel"
              id={panelIdOf("video")}
              aria-labelledby={tabIdOf("video")}
              hidden={tab !== "video"}
              tabIndex={0}
            >
              <DemoVideo />
            </div>
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
