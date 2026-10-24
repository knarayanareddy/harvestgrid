/**
 * Fixture adapter: deterministic demo data, labelled `fixture` everywhere it surfaces.
 *
 * Fixture data must never satisfy a live-integration gate. The UI shows a persistent
 * "FIXTURE DATA" banner whenever this adapter is active, and receipts carry
 * `provenance: fixture`.
 */

import { buildReceipt, validateWeights } from "../domain/receipts";
import type {
  AssignmentSummary,
  Candidate,
  DonationReceipt,
  MatchingBoard,
  ReceiptInput,
  ValidationResult,
} from "../domain/types";
import type { ApproveAssignmentsInput, CoordinatorApi, CoordinatorSession } from "./CoordinatorApi";

const SESSION: CoordinatorSession = {
  actorId: "coordinator-fixture",
  roles: ["coordinator"],
  organizationId: "org-fixture",
};

const CONSTRAINTS_OK = {
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
} as const;

export const FIXTURE_CANDIDATES: readonly Candidate[] = [
  {
    volunteerId: "volunteer-a",
    displayName: "Fixture volunteer A",
    trainingLevel: "trained_leader",
    equipment: ["ladder", "picking_bag"],
    constraints: CONSTRAINTS_OK,
    ranking: {
      travelFit: 0.7,
      scheduleFit: 0.8,
      recipientLogisticsFit: 0.6,
      equipmentFit: 0.9,
      fairnessRotation: 0.5,
    },
    recentAssignments: 3,
  },
  {
    volunteerId: "volunteer-b",
    displayName: "Fixture volunteer B",
    trainingLevel: "trained_leader",
    equipment: ["ladder"],
    constraints: CONSTRAINTS_OK,
    ranking: {
      travelFit: 0.9,
      scheduleFit: 0.85,
      recipientLogisticsFit: 0.75,
      equipmentFit: 0.8,
      fairnessRotation: 0.4,
    },
    recentAssignments: 1,
  },
  {
    volunteerId: "volunteer-c",
    displayName: "Fixture volunteer C",
    trainingLevel: "trainee",
    equipment: ["picking_bag"],
    constraints: {
      ...CONSTRAINTS_OK,
      volunteerTrainingValid: false,
      trainedLeaderAvailable: true,
    },
    ranking: {
      travelFit: 1,
      scheduleFit: 1,
      recipientLogisticsFit: 1,
      equipmentFit: 1,
      fairnessRotation: 1,
    },
    recentAssignments: 0,
  },
];

export function fixtureBoard(): MatchingBoard {
  return {
    provenance: "fixture",
    organizationId: "org-fixture",
    gleanId: "glean-fixture",
    gleanState: "matching",
    ownerApprovalCurrent: true,
    coordinatorReadinessConfirmed: true,
    forecast: {
      forecastId: "forecast-fixture",
      treeId: "tree-fixture",
      coarseArea: "u173z",
      readyStart: "2026-10-28T00:00:00Z",
      readyEnd: "2026-11-06T00:00:00Z",
      visibleCount: 40,
      countLower: 32,
      countUpper: 48,
      modelVersion: "seasonal-baseline-v1",
      dataVersion: "no_training_data",
      experimental: true,
      issuedAt: "2026-10-24T08:00:00Z",
      status: "review_required",
      confidenceReasons: ["baseline_model_only", "visible_count_only", "no_cultivar_prior"],
    },
    candidates: FIXTURE_CANDIDATES,
    assignments: [],
    serverTieKeys: {
      "volunteer-a": "0a1f",
      "volunteer-b": "0b2e",
      "volunteer-c": "0c3d",
    },
  };
}

export class FixtureCoordinatorApi implements CoordinatorApi {
  readonly session = SESSION;
  private board: MatchingBoard;
  private readonly now: () => string;

  constructor(options: { board?: MatchingBoard; now?: () => string } = {}) {
    this.board = options.board ?? fixtureBoard();
    this.now = options.now ?? (() => "2026-10-24T09:00:00Z");
  }

  async loadBoard(): Promise<MatchingBoard> {
    return this.board;
  }

  async approveAssignments(
    input: ApproveAssignmentsInput,
  ): Promise<readonly AssignmentSummary[]> {
    if (input.volunteerIds.length === 0) {
      throw new Error("At least one volunteer is required.");
    }
    const assignments: AssignmentSummary[] = input.volunteerIds.map((volunteerId) => ({
      assignmentId: `assignment-${volunteerId}`,
      volunteerId,
      displayName:
        this.board.candidates.find((candidate) => candidate.volunteerId === volunteerId)
          ?.displayName ?? volunteerId,
      state: "invited",
      revealNotBefore: input.revealNotBefore,
      revealUntil: input.revealUntil,
    }));
    this.board = {
      ...this.board,
      gleanState: "coordinator_assignment_approval",
      assignments: [...this.board.assignments, ...assignments],
    };
    return assignments;
  }

  async recordReceipt(input: ReceiptInput): Promise<ValidationResult<DonationReceipt>> {
    const validation = validateWeights(input.weights, { roles: this.session.roles });
    if (!validation.ok) return validation;
    return {
      ok: true,
      value: buildReceipt(input, validation.value, {
        provenance: "fixture",
        now: this.now(),
      }),
    };
  }
}
