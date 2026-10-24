/**
 * Address/access reveal policy for the coordinator board.
 *
 * The browser deliberately receives no address until the volunteer is confirmed, the
 * glean is running, owner approval is current, and consent is active. Even then access is
 * capped at the earlier of two hours from now or the assignment's reveal expiry. The UI
 * shows blockers rather than a partially-revealed address.
 */

import type { AccessDecision, AccessRevealRequest } from "./types";

export const ACCESS_WINDOW_HOURS = 2;

export function evaluateAccessReveal(request: AccessRevealRequest): AccessDecision {
  if (!request.actorRoles.includes("volunteer")) {
    return {
      allowed: false,
      reason: "role_not_permitted",
      message: "Only an assigned volunteer can request access details.",
    };
  }
  if (!request.consentActive) {
    return {
      allowed: false,
      reason: "consent_inactive",
      message: "Owner consent is not active, so access details are unavailable.",
    };
  }
  if (!request.ownerApprovalCurrent) {
    return {
      allowed: false,
      reason: "owner_approval_missing",
      message: "Owner approval is not current, so access details are unavailable.",
    };
  }
  if (request.assignment === null || request.assignment.volunteerId !== request.actorId) {
    return {
      allowed: false,
      reason: "not_assigned",
      message: "Access details are only available to the assigned volunteer.",
    };
  }
  if (request.assignment.state === "cancelled") {
    return {
      allowed: false,
      reason: "assignment_cancelled",
      message: "This assignment was cancelled; access details and tokens are revoked.",
    };
  }
  if (request.assignment.state !== "confirmed") {
    return {
      allowed: false,
      reason: "assignment_not_confirmed",
      message: "Accept the assignment before requesting access details.",
    };
  }
  if (request.gleanState !== "confirmed" && request.gleanState !== "in_progress") {
    return {
      allowed: false,
      reason: "glean_not_active",
      message: "Access details are unavailable outside a confirmed glean.",
    };
  }
  const now = Date.parse(request.now);
  const notBefore = Date.parse(request.assignment.revealNotBefore);
  const revealUntil = Date.parse(request.assignment.revealUntil);
  if (!Number.isFinite(now) || !Number.isFinite(notBefore) || !Number.isFinite(revealUntil)) {
    return {
      allowed: false,
      reason: "invalid_timestamp",
      message: "Access window could not be verified.",
    };
  }
  if (now < notBefore) {
    return {
      allowed: false,
      reason: "reveal_window_not_open",
      message: "Access details become available shortly before the event.",
    };
  }
  const minimumExpiry = now + ACCESS_WINDOW_HOURS * 60 * 60 * 1000;
  const expiresAt = Math.min(minimumExpiry, revealUntil);
  if (now >= expiresAt) {
    return {
      allowed: false,
      reason: "reveal_window_expired",
      message: "The access window has expired for this assignment.",
    };
  }
  return { allowed: true, expiresAt: new Date(expiresAt).toISOString(), minimumWindowHours: 2 };
}
