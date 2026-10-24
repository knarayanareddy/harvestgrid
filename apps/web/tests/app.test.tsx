import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import { FixtureCoordinatorApi, fixtureBoard } from "../src/api/fixtureClient";
import type { CoordinatorApi } from "../src/api/CoordinatorApi";
import type { MatchingBoard } from "../src/domain/types";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

beforeAll(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(api: CoordinatorApi = new FixtureCoordinatorApi()): Promise<void> {
  await act(async () => {
    root.render(<App api={api} />);
  });
}

function text(): string {
  return container.textContent ?? "";
}

function byId<T extends HTMLElement>(id: string): T {
  const element = container.querySelector<T>(`#${id}`);
  if (!element) throw new Error(`missing element #${id}`);
  return element;
}

function setValue(element: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )?.set;
  act(() => {
    setter?.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("coordinator dashboard", () => {
  it("labels fixture data and renders the forecast with review-required status", async () => {
    await render();
    expect(text()).toContain("FIXTURE DATA");
    expect(text()).toContain("no Ring device");
    expect(text()).toContain("Review required");
    expect(text()).toContain("Experimental baseline forecast");
    expect(text()).toContain("seasonal-baseline-v1");
    expect(text()).toContain("cannot establish internal ripeness");
    expect(text()).toContain("u173z");
    expect(text()).not.toContain("52.0067");
  });

  it("renders eligible and blocked volunteers with text status and blockers", async () => {
    await render();
    expect(text()).toContain("Matching board");
    expect(text()).toContain("Volunteer training is incomplete");
    expect(text()).toMatch(/✔ Eligible/);
    expect(text()).toMatch(/🔒 Not eligible/);

    const rows = container.querySelectorAll("tbody tr");
    expect(rows.length).toBe(fixtureBoard().candidates.length);
    const firstRow = rows[0];
    expect(firstRow?.getAttribute("data-eligible")).toBe("yes");
    const lastRow = rows[rows.length - 1];
    expect(lastRow?.getAttribute("data-eligible")).toBe("no");
  });

  it("blocks approval until an eligible volunteer is selected", async () => {
    await render();
    const approve = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Approve selected assignments"),
    );
    expect(approve).toBeDefined();
    expect(approve?.disabled).toBe(true);
    expect(text()).toContain("Select at least one eligible volunteer.");

    const checkbox = byId<HTMLInputElement>("select-volunteer-b");
    await act(async () => {
      checkbox.click();
    });
    expect(text()).toContain("1 volunteer(s) selected");
    expect(approve?.disabled).toBe(false);

    await act(async () => {
      approve?.click();
      await Promise.resolve();
    });
    expect(text()).toContain("1 assignment(s) approved");
    expect(text()).toContain("Exact access details remain hidden");
  });

  it("disables selection for ineligible volunteers", async () => {
    await render();
    expect(byId<HTMLInputElement>("select-volunteer-c").disabled).toBe(true);
    expect(byId<HTMLInputElement>("select-volunteer-a").disabled).toBe(false);
  });

  it("shows field errors for unbalanced weights and issues no receipt", async () => {
    await render();
    setValue(byId<HTMLInputElement>("field-grossKg"), "10.0");
    setValue(byId<HTMLInputElement>("field-netKg"), "8.5");
    setValue(byId<HTMLInputElement>("field-acceptedKg"), "7.0");
    setValue(byId<HTMLInputElement>("field-rejectedKg"), "0.5");

    const form = container.querySelector("form");
    await act(async () => {
      form?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await Promise.resolve();
    });
    expect(text()).toContain("Accepted plus rejected weight must equal net weight.");
    expect(text()).not.toContain("Receipt ");
    expect(byId<HTMLInputElement>("field-acceptedKg").getAttribute("aria-invalid")).toBe(
      "true",
    );
  });

  it("records a valid receipt and shows only non-sensitive fields", async () => {
    await render();
    setValue(byId<HTMLInputElement>("field-grossKg"), "10.0");
    setValue(byId<HTMLInputElement>("field-netKg"), "8.5");
    setValue(byId<HTMLInputElement>("field-acceptedKg"), "8.0");
    setValue(byId<HTMLInputElement>("field-rejectedKg"), "0.5");

    const reason = Array.from(
      container.querySelectorAll<HTMLInputElement>("input[type=checkbox]"),
    ).find(
      (input) => input.closest("label")?.textContent?.includes("spoiled"),
    );
    await act(async () => {
      reason?.click();
    });
    expect(reason?.checked).toBe(true);

    const form = container.querySelector("form");
    await act(async () => {
      form?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await Promise.resolve();
    });
    const receiptText = container.querySelector(".receipt")?.textContent ?? "";
    expect(receiptText).toContain("Receipt HG-");
    expect(receiptText).toContain("8.000 kg");
    expect(receiptText).toContain("european plum");
    expect(receiptText).toContain("Fixture receipt");
    // Non-sensitive receipt: no scale reference, rejection reason, or volunteer identity.
    expect(receiptText).not.toContain("scale-01");
    expect(receiptText).not.toContain("spoiled");
    expect(receiptText).not.toContain("volunteer-");
    expect(receiptText.toLowerCase()).not.toContain("address");
  });

  it("surfaces a load failure with a retry action", async () => {
    const failing: CoordinatorApi = {
      session: {
        actorId: "coordinator-fixture",
        roles: ["coordinator"],
        organizationId: "org-fixture",
      },
      loadBoard: vi.fn().mockRejectedValue(new Error("network down")),
      approveAssignments: vi.fn(),
      recordReceipt: vi.fn(),
    };
    await render(failing);
    expect(text()).toContain("The board could not be loaded");
    expect(text()).toContain("network down");
    expect(text()).toContain("Retry");
  });

  it("hides approval from a read-only evaluator role", async () => {
    const board: MatchingBoard = { ...fixtureBoard() };
    const api = new FixtureCoordinatorApi({ board });
    const evaluator: CoordinatorApi = {
      session: {
        actorId: "evaluator-fixture",
        roles: ["evaluator"],
        organizationId: "org-fixture",
      },
      loadBoard: () => api.loadBoard(),
      approveAssignments: () => api.approveAssignments({
        gleanId: board.gleanId,
        volunteerIds: [],
        coordinatorId: "evaluator-fixture",
        revealNotBefore: "2026-10-30T08:00:00Z",
        revealUntil: "2026-10-30T18:00:00Z",
        idempotencyKey: "unused",
      }),
      recordReceipt: (input) => api.recordReceipt(input),
    };
    await render(evaluator);
    expect(byId<HTMLInputElement>("select-volunteer-b").disabled).toBe(true);
    expect(text()).toContain("roles evaluator");
  });
});
