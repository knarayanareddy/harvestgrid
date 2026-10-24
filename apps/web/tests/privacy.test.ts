import { describe, expect, it } from "vitest";

import { ACCESS_WINDOW_HOURS, evaluateAccessReveal } from "../src/domain/privacy";
import type { AccessRevealRequest, AssignmentSummary } from "../src/domain/types";

const CONFIRMED: AssignmentSummary = {
  assignmentId: "assignment-a",
  volunteerId: "volunteer-a",
  displayName: "Volunteer A",
  state: "confirmed",
  revealNotBefore: "2026-10-30T08:00:00Z",
  revealUntil: "2026-10-30T18:00:00Z",
};

function request(overrides: Partial<AccessRevealRequest> = {}): AccessRevealRequest {
  return {
    actorId: "volunteer-a",
    actorRoles: ["volunteer"],
    gleanState: "confirmed",
    ownerApprovalCurrent: true,
    consentActive: true,
    assignment: CONFIRMED,
    now: "2026-10-30T09:00:00Z",
    ...overrides,
  };
}

describe("access reveal policy", () => {
  it("allows a confirmed assignee inside the window and caps the grant at two hours", () => {
    const decision = evaluateAccessReveal(request());
    expect(decision.allowed).toBe(true);
    if (!decision.allowed) return;
    expect(ACCESS_WINDOW_HOURS).toBe(2);
    // RFC 3339 with millisecond precision, per the shared engineering standard.
    expect(decision.expiresAt).toBe("2026-10-30T11:00:00.000Z");
    expect(decision.minimumWindowHours).toBe(2);
  });

  it("never extends past the assignment expiry", () => {
    const decision = evaluateAccessReveal(
      request({ now: "2026-10-30T17:30:00Z" }),
    );
    expect(decision.allowed).toBe(true);
    if (!decision.allowed) return;
    expect(decision.expiresAt).toBe("2026-10-30T18:00:00.000Z");
  });

  it.each([
    [{ actorRoles: ["coordinator"] as const }, "role_not_permitted"],
    [{ actorId: "volunteer-z" }, "not_assigned"],
    [{ assignment: null }, "not_assigned"],
    [{ assignment: { ...CONFIRMED, state: "cancelled" as const } }, "assignment_cancelled"],
    [{ assignment: { ...CONFIRMED, state: "invited" as const } }, "assignment_not_confirmed"],
    [{ gleanState: "cancelled" as const }, "glean_not_active"],
    [{ consentActive: false }, "consent_inactive"],
    [{ ownerApprovalCurrent: false }, "owner_approval_missing"],
    [{ now: "2026-10-30T07:00:00Z" }, "reveal_window_not_open"],
    [{ now: "2026-10-31T09:00:00Z" }, "reveal_window_expired"],
  ])("denies %o with %s", (override, reason) => {
    const decision = evaluateAccessReveal(request(override));
    expect(decision.allowed).toBe(false);
    if (decision.allowed) return;
    expect(decision.reason).toBe(reason);
    expect(decision.message.length).toBeGreaterThan(10);
    expect(decision.message.toLowerCase()).not.toContain("street");
  });

  it("rejects unparseable timestamps instead of guessing", () => {
    const decision = evaluateAccessReveal(request({ now: "not-a-date" }));
    expect(decision).toEqual({
      allowed: false,
      reason: "invalid_timestamp",
      message: "Access window could not be verified.",
    });
  });

  it("keeps cancellation final even if the owner approval is current", () => {
    const cancelledGlean = evaluateAccessReveal(
      request({
        gleanState: "cancelled",
        assignment: { ...CONFIRMED, state: "cancelled" as const },
      }),
    );
    expect(cancelledGlean.allowed).toBe(false);
  });
});
