import { describe, expect, it } from "vitest";

import {
  BLOCKER_LABELS,
  RANKING_WEIGHTS,
  boardRows,
  canApproveAssignments,
  evaluateCandidate,
  rankEligible,
} from "../src/domain/matching";
import type { Candidate, HardConstraints } from "../src/domain/types";

const OK: HardConstraints = {
  ownerApprovalCurrent: true,
  coordinatorApprovalCurrent: true,
  trainedLeaderAvailable: true,
  volunteerTrainingValid: true,
  equipmentSatisfied: true,
  availabilityIntersects: true,
  recipientCapacitySufficient: true,
  coordinatorNoRecipientPlanApproved: false,
  unresolvedSafetyBlockers: 0,
  addressRevealPolicySatisfied: true,
  insuranceWaiverSatisfied: true,
};

function candidate(overrides: Partial<Candidate> = {}): Candidate {
  return {
    volunteerId: "volunteer-a",
    displayName: "Volunteer A",
    trainingLevel: "trained_leader",
    equipment: ["ladder"],
    constraints: OK,
    ranking: {
      travelFit: 0.5,
      scheduleFit: 0.5,
      recipientLogisticsFit: 0.5,
      equipmentFit: 0.5,
      fairnessRotation: 0.5,
    },
    recentAssignments: 0,
    ...overrides,
  };
}

describe("ranking weights", () => {
  it("match the specification and sum to one", () => {
    expect(RANKING_WEIGHTS).toEqual({
      travelFit: 0.3,
      scheduleFit: 0.25,
      recipientLogisticsFit: 0.2,
      equipmentFit: 0.15,
      fairnessRotation: 0.1,
    });
    const total = Object.values(RANKING_WEIGHTS).reduce((sum, value) => sum + value, 0);
    expect(total).toBeCloseTo(1, 10);
  });

  it("scores a uniform candidate with the weighted mean", () => {
    const decision = evaluateCandidate(candidate());
    expect(decision.eligible).toBe(true);
    expect(decision.score).toBeCloseTo(0.5, 6);
    expect(decision.components).toEqual({
      travelFit: 0.5,
      scheduleFit: 0.5,
      recipientLogisticsFit: 0.5,
      equipmentFit: 0.5,
      fairnessRotation: 0.5,
    });
  });

  it("clamps out-of-range features instead of producing an invalid score", () => {
    const decision = evaluateCandidate(
      candidate({
        ranking: {
          travelFit: 4,
          scheduleFit: -2,
          recipientLogisticsFit: Number.NaN,
          equipmentFit: 0.5,
          fairnessRotation: 1,
        },
      }),
    );
    expect(decision.components).toEqual({
      travelFit: 1,
      scheduleFit: 0,
      recipientLogisticsFit: 0,
      equipmentFit: 0.5,
      fairnessRotation: 1,
    });
    expect(decision.score).toBeGreaterThan(0);
    expect(decision.score).toBeLessThanOrEqual(1);
  });
});

describe("hard filters", () => {
  it.each([
    [{ ownerApprovalCurrent: false }, "owner_approval_missing"],
    [{ coordinatorApprovalCurrent: false }, "coordinator_approval_missing"],
    [{ trainedLeaderAvailable: false }, "trained_leader_missing"],
    [{ volunteerTrainingValid: false }, "volunteer_training_missing"],
    [{ equipmentSatisfied: false }, "equipment_missing"],
    [{ availabilityIntersects: false }, "availability_mismatch"],
    [{ recipientCapacitySufficient: false }, "recipient_capacity_insufficient"],
    [{ unresolvedSafetyBlockers: 2 }, "safety_blocker_unresolved"],
    [{ addressRevealPolicySatisfied: false }, "address_reveal_policy_unsatisfied"],
    [{ insuranceWaiverSatisfied: false }, "insurance_or_waiver_missing"],
  ])("blocks on %o", (override, blocker) => {
    const decision = evaluateCandidate(
      candidate({ constraints: { ...OK, ...override } }),
    );
    expect(decision.eligible).toBe(false);
    expect(decision.blockers).toContain(blocker);
    expect(decision.score).toBeNull();
    expect(decision.components).toBeNull();
    expect(BLOCKER_LABELS[blocker as keyof typeof BLOCKER_LABELS]).toBeTruthy();
  });

  it("never lets a perfect score waive a safety blocker", () => {
    const decision = evaluateCandidate(
      candidate({
        constraints: { ...OK, unresolvedSafetyBlockers: 1 },
        ranking: {
          travelFit: 1,
          scheduleFit: 1,
          recipientLogisticsFit: 1,
          equipmentFit: 1,
          fairnessRotation: 1,
        },
      }),
    );
    expect(decision.eligible).toBe(false);
    expect(decision.score).toBeNull();
  });

  it("accepts a coordinator-approved no-recipient plan when capacity is short", () => {
    const decision = evaluateCandidate(
      candidate({
        constraints: {
          ...OK,
          recipientCapacitySufficient: false,
          coordinatorNoRecipientPlanApproved: true,
        },
      }),
    );
    expect(decision.eligible).toBe(true);
    expect(decision.blockers).toEqual([]);
  });

  it("reports every blocker, not just the first", () => {
    const decision = evaluateCandidate(
      candidate({
        constraints: {
          ...OK,
          ownerApprovalCurrent: false,
          trainedLeaderAvailable: false,
          unresolvedSafetyBlockers: 3,
        },
      }),
    );
    expect(decision.blockers).toEqual([
      "owner_approval_missing",
      "trained_leader_missing",
      "safety_blocker_unresolved",
    ]);
  });
});

describe("deterministic ordering", () => {
  const high = candidate({
    volunteerId: "volunteer-b",
    displayName: "B",
    ranking: {
      travelFit: 0.9,
      scheduleFit: 0.9,
      recipientLogisticsFit: 0.9,
      equipmentFit: 0.9,
      fairnessRotation: 0.9,
    },
  });
  const tiedOne = candidate({ volunteerId: "volunteer-z", displayName: "Z" });
  const tiedTwo = candidate({ volunteerId: "volunteer-a", displayName: "A" });

  it("ranks by score then server tie key, ignoring input order", () => {
    const first = rankEligible([tiedOne, high, tiedTwo], {
      "volunteer-a": "bbbb",
      "volunteer-z": "aaaa",
    });
    const second = rankEligible([tiedTwo, tiedOne, high], {
      "volunteer-a": "bbbb",
      "volunteer-z": "aaaa",
    });
    expect(first.map((row) => row.volunteerId)).toEqual([
      "volunteer-b",
      "volunteer-z",
      "volunteer-a",
    ]);
    expect(JSON.stringify(first)).toEqual(JSON.stringify(second));
  });

  it("falls back to volunteer id when no tie key is supplied", () => {
    const rows = rankEligible([tiedOne, tiedTwo]);
    expect(rows.map((row) => row.volunteerId)).toEqual(["volunteer-a", "volunteer-z"]);
  });

  it("places ineligible volunteers last and alphabetically", () => {
    const blocked = candidate({
      volunteerId: "volunteer-m",
      displayName: "M blocked",
      constraints: { ...OK, equipmentSatisfied: false },
    });
    const rows = boardRows([blocked, high, tiedTwo]);
    expect(rows.map((row) => row.volunteerId)).toEqual([
      "volunteer-b",
      "volunteer-a",
      "volunteer-m",
    ]);
    expect(rows[2]?.eligible).toBe(false);
  });

  it("excludes ineligible candidates from eligible rankings", () => {
    const ineligible = candidate({
      volunteerId: "volunteer-x",
      constraints: { ...OK, volunteerTrainingValid: false },
    });
    expect(rankEligible([ineligible]).map((row) => row.volunteerId)).toEqual([]);
  });
});

describe("assignment approval gate", () => {
  const board = {
    ownerApprovalCurrent: true,
    coordinatorReadinessConfirmed: true,
    gleanState: "matching",
  };

  it("requires owner approval, readiness, active matching, and eligible selections", () => {
    const candidates = [candidate()];
    expect(canApproveAssignments(board, ["volunteer-a"], candidates)).toEqual({
      allowed: true,
      reason: null,
    });
    expect(
      canApproveAssignments({ ...board, ownerApprovalCurrent: false }, ["volunteer-a"], candidates),
    ).toEqual({ allowed: false, reason: "Owner approval is required before matching." });
    expect(
      canApproveAssignments(
        { ...board, coordinatorReadinessConfirmed: false },
        ["volunteer-a"],
        candidates,
      ).allowed,
    ).toBe(false);
    expect(canApproveAssignments({ ...board, gleanState: "closed" }, ["a"], candidates).allowed).toBe(
      false,
    );
    expect(canApproveAssignments(board, [], candidates).reason).toBe(
      "Select at least one eligible volunteer.",
    );
    expect(
      canApproveAssignments(
        board,
        ["volunteer-x"],
        [candidate({ volunteerId: "volunteer-x", constraints: { ...OK, availabilityIntersects: false } })],
      ).reason,
    ).toBe("Every selected volunteer must pass the hard filters.");
  });
});
