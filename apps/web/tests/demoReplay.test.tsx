/**
 * Replays the recorded-demo scene plan against the real DOM.
 *
 * `scripts/record-demo.ts` drives this page through Playwright; this test drives the same page
 * through the same `RECORDING_SCENES` with jsdom events. Both consume one plan, so a scene that the
 * video could not complete — a control that no longer exists, an approval that never becomes
 * current, a dispatch that stays gated — fails here instead of producing a broken recording.
 *
 * The Playwright run itself needs a browser binary this environment cannot download; this replay is
 * the executable evidence that the plan is coherent, and it is part of `pnpm test`.
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { DemoWorkspace } from "../src/demo/DemoWorkspace";
import { DemoStore } from "../src/demo/demoStore";
import { RECORDING_SCENES, type DemoAction } from "../src/demo/recordingPlan";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const FRAME = { left: 0, top: 0, width: 480, height: 360 };

let container: HTMLDivElement;
let root: Root;

beforeAll(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  // jsdom implements neither pointer capture nor layout; the polygon editor needs both to turn a
  // pointer position into a normalized vertex.
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function byTestId<T extends Element>(testId: string): T {
  const element = container.querySelector<T>(`[data-testid="${testId}"]`);
  if (!element) throw new Error(`missing data-testid=${testId}`);
  return element;
}

function setNativeValue(element: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

function pointerEvent(type: string, x: number, y: number): Event {
  const PointerEventCtor = (
    globalThis as unknown as { PointerEvent?: typeof MouseEvent }
  ).PointerEvent;
  const init = { bubbles: true, cancelable: true, clientX: x, clientY: y, pointerId: 1 };
  return new (PointerEventCtor ?? MouseEvent)(type, init);
}

/** Mirrors `perform()` in `scripts/record-demo.ts` with jsdom events. */
function perform(action: DemoAction): void {
  switch (action.kind) {
    case "click":
      byTestId<HTMLButtonElement>(action.testId).click();
      return;
    case "check": {
      const box = byTestId<HTMLInputElement>(action.testId);
      if (box.checked !== action.checked) box.click();
      return;
    }
    case "fill":
      setNativeValue(byTestId<HTMLInputElement>(action.testId), action.value);
      return;
    case "range":
      setNativeValue(byTestId<HTMLInputElement>(action.testId), action.value);
      return;
    case "dragVertex": {
      const vertex = byTestId<SVGCircleElement>(action.testId);
      const svg = byTestId<SVGSVGElement>("privacy-map");
      svg.getBoundingClientRect = () =>
        ({
          ...FRAME,
          right: FRAME.left + FRAME.width,
          bottom: FRAME.top + FRAME.height,
          x: FRAME.left,
          y: FRAME.top,
          toJSON: () => ({}),
        }) as DOMRect;
      // One act per event: the editor stores the dragged vertex in React state, so a batched
      // dispatch would move nothing. Playwright has the same one-round-trip-per-event shape.
      act(() => {
        vertex.dispatchEvent(pointerEvent("pointerdown", 100, 100));
      });
      act(() => {
        svg.dispatchEvent(
          pointerEvent("pointermove", FRAME.width / 2 + action.dx, FRAME.height / 2 + action.dy),
        );
      });
      act(() => {
        svg.dispatchEvent(pointerEvent("pointerup", FRAME.width / 2, FRAME.height / 2));
      });
      return;
    }
    case "wait":
    case "caption":
      return;
  }
}

/**
 * Drives one action. `dragVertex` dispatches three events that must render between each other, so it
 * owns its `act()` calls; wrapping it in another `act()` would batch the pointerdown state update and
 * leave the editor with no vertex under the cursor.
 */
function drive(action: DemoAction): void {
  if (action.kind === "dragVertex") {
    perform(action);
    return;
  }
  act(() => perform(action));
}

describe("recording plan replay", () => {
  it("runs every scene against the rendered demo and satisfies its assertions", () => {
    act(() => {
      root.render(<DemoWorkspace store={new DemoStore()} />);
    });
    byTestId("demo-workspace");

    for (const scene of RECORDING_SCENES) {
      for (const action of scene.actions) drive(action);
      for (const expectation of scene.expect) {
        const text = byTestId(expectation.testId).textContent ?? "";
        expect(text.toLowerCase(), `${scene.id}: ${expectation.testId}`).toContain(
          expectation.contains.toLowerCase(),
        );
      }
    }
  }, 20_000);

  it("keeps the dispatch gate closed until the plan's preconditions are met", () => {
    act(() => {
      root.render(<DemoWorkspace store={new DemoStore()} />);
    });
    // Same controls the recorder clicks, minus the readiness confirmation and assignments.
    drive({ kind: "click", testId: "approve-owner" });
    drive({ kind: "click", testId: "approve-coordinator" });
    expect(byTestId("dispatch-gate").textContent).not.toContain("Gate satisfied");
    expect(container.querySelector('[data-testid="dispatch-summary"]')).toBeNull();

    drive({ kind: "check", testId: "readiness-confirm", checked: true });
    expect(byTestId("dispatch-gate").textContent).toContain("at least one volunteer");
  });

  it("resets both approvals when the plan's drag moves a vertex", () => {
    act(() => {
      root.render(<DemoWorkspace store={new DemoStore()} />);
    });
    drive({ kind: "click", testId: "approve-owner" });
    drive({ kind: "click", testId: "approve-coordinator" });
    expect(byTestId("view-approvals").textContent).toContain("owner ✔");

    drive({ kind: "dragVertex", testId: "roi-vertex-0", dx: 0, dy: 0 });

    expect(byTestId("view-approvals").textContent).toContain("owner ✖");
    expect(byTestId("view-approvals").textContent).toContain("coordinator ✖");
  });
});
