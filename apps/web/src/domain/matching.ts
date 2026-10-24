/**
 * Deterministic matching for the coordinator board.
 *
 * The weights mirror SPEC §9.2 exactly:
 *   0.30 travel + 0.25 schedule + 0.20 recipient logistics + 0.15 equipment + 0.10 fairness
 *
 * Hard filters are evaluated first. An ineligible volunteer receives no score, and no
 * amount of scoring can waive a filter. Inputs are numeric features plus explicit
 * boolean readiness facts — there is no field for age, gender, ethnicity, health, or any
 * other protected trait, so discrimination by score is not representable here.
 */

import type {
  BlockerCode,
  Candidate,
  EligibilityDecision,
  ScoreComponents,
} from "./types";

export type { BlockerCode };

export const RANKING_WEIGHTS = {
  travelFit: 0.3,
  scheduleFit: 0.25,
  recipientLogisticsFit: 0.2,
  equipmentFit: 0.15,
  fairnessRotation: 0.1,
} as const;

export const BLOCKER_LABELS: Readonly<Record<BlockerCode, string>> = {
  owner_approval_missing: "Owner approval is not current",
  coordinator_approval_missing: "Coordinator approval is not current",
  trained_leader_missing: "No trained event leader available",
  volunteer_training_missing: "Volunteer training is incomplete",
  equipment_missing: "Required equipment is not confirmed",
  availability_mismatch: "Availability does not intersect the window",
  recipient_capacity_insufficient: "Recipient capacity is insufficient",
  safety_blocker_unresolved: "Safety blocker is unresolved",
  address_reveal_policy_unsatisfied: "Address-reveal policy is not satisfied",
  insurance_or_waiver_missing: "Insurance or waiver requirement is unmet",
};

export function evaluateCandidate(candidate: Candidate): EligibilityDecision {
  const constraints = candidate.constraints;
  const blockers: BlockerCode[] = [];
  if (!constraints.ownerApprovalCurrent) blockers.push("owner_approval_missing");
  if (!constraints.coordinatorApprovalCurrent) blockers.push("coordinator_approval_missing");
  if (!constraints.trainedLeaderAvailable) blockers.push("trained_leader_missing");
  if (!constraints.volunteerTrainingValid) blockers.push("volunteer_training_missing");
  if (!constraints.equipmentSatisfied) blockers.push("equipment_missing");
  if (!constraints.availabilityIntersects) blockers.push("availability_mismatch");
  if (
    !constraints.recipientCapacitySufficient &&
    !constraints.coordinatorNoRecipientPlanApproved
  ) {
    blockers.push("recipient_capacity_insufficient");
  }
  if (constraints.unresolvedSafetyBlockers > 0) blockers.push("safety_blocker_unresolved");
  if (!constraints.addressRevealPolicySatisfied) {
    blockers.push("address_reveal_policy_unsatisfied");
  }
  if (!constraints.insuranceWaiverSatisfied) blockers.push("insurance_or_waiver_missing");

  if (blockers.length > 0) {
    return {
      volunteerId: candidate.volunteerId,
      displayName: candidate.displayName,
      eligible: false,
      blockers,
      score: null,
      components: null,
    };
  }
  const components = scoreComponents(candidate);
  return {
    volunteerId: candidate.volunteerId,
    displayName: candidate.displayName,
    eligible: true,
    blockers: [],
    score: round4(
      components.travelFit * RANKING_WEIGHTS.travelFit +
        components.scheduleFit * RANKING_WEIGHTS.scheduleFit +
        components.recipientLogisticsFit * RANKING_WEIGHTS.recipientLogisticsFit +
        components.equipmentFit * RANKING_WEIGHTS.equipmentFit +
        components.fairnessRotation * RANKING_WEIGHTS.fairnessRotation,
    ),
    components,
  };
}

function scoreComponents(candidate: Candidate): ScoreComponents {
  return {
    travelFit: clamp01(candidate.ranking.travelFit),
    scheduleFit: clamp01(candidate.ranking.scheduleFit),
    recipientLogisticsFit: clamp01(candidate.ranking.recipientLogisticsFit),
    equipmentFit: clamp01(candidate.ranking.equipmentFit),
    fairnessRotation: clamp01(candidate.ranking.fairnessRotation),
  };
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

function round4(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}

/**
 * Stable order: eligible candidates by descending score, then by the server-provided tie
 * key (a hash of `gleanId:volunteerId`), then by volunteer ID so the order never depends
 * on input position or on `Array.prototype.sort` stability.
 */
export function rankEligible(
  candidates: readonly Candidate[],
  serverTieKeys: Readonly<Record<string, string>> = {},
): readonly EligibilityDecision[] {
  const decisions = candidates
    .map((candidate) => evaluateCandidate(candidate))
    .filter((decision) => decision.eligible);
  return [...decisions].sort((left, right) => {
    const scoreDelta = (right.score ?? 0) - (left.score ?? 0);
    if (scoreDelta !== 0) return scoreDelta;
    const leftKey = serverTieKeys[left.volunteerId] ?? left.volunteerId;
    const rightKey = serverTieKeys[right.volunteerId] ?? right.volunteerId;
    return leftKey < rightKey ? -1 : leftKey > rightKey ? 1 : 0;
  });
}

/** Board rows in display order: eligible ranked first, then ineligible by name. */
export function boardRows(
  candidates: readonly Candidate[],
  serverTieKeys: Readonly<Record<string, string>> = {},
): readonly EligibilityDecision[] {
  const ranked = rankEligible(candidates, serverTieKeys);
  const rankedIds = new Set(ranked.map((decision) => decision.volunteerId));
  const ineligible = candidates
    .filter((candidate) => !rankedIds.has(candidate.volunteerId))
    .map((candidate) => evaluateCandidate(candidate))
    .sort((left, right) => left.displayName.localeCompare(right.displayName));
  return [...ranked, ...ineligible];
}

/** Assignment approval is impossible without current owner approval and eligible people. */
export function canApproveAssignments(
  board: {
    readonly ownerApprovalCurrent: boolean;
    readonly coordinatorReadinessConfirmed: boolean;
    readonly gleanState: string;
  },
  selection: readonly string[],
  candidates: readonly Candidate[],
): { readonly allowed: boolean; readonly reason: string | null } {
  if (!board.ownerApprovalCurrent) {
    return { allowed: false, reason: "Owner approval is required before matching." };
  }
  if (!board.coordinatorReadinessConfirmed) {
    return { allowed: false, reason: "Coordinator readiness confirmation is required." };
  }
  if (board.gleanState !== "matching" && board.gleanState !== "coordinator_assignment_approval") {
    return { allowed: false, reason: "This glean is not accepting assignments." };
  }
  if (selection.length === 0) {
    return { allowed: false, reason: "Select at least one eligible volunteer." };
  }
  const eligible = new Set(rankEligible(candidates).map((decision) => decision.volunteerId));
  if (selection.some((volunteerId) => !eligible.has(volunteerId))) {
    return { allowed: false, reason: "Every selected volunteer must pass the hard filters." };
  }
  return { allowed: true, reason: null };
}
