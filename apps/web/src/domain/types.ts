/**
 * Coordinator-facing domain contracts.
 *
 * These types mirror the Python domain (`packages/domain`, `packages/matching`). The
 * browser only ever renders what the API returns; it never calls Ring, weather, or media
 * endpoints, and no type here can carry an owner address, exact coordinates, or raw media.
 */

export type MembershipRole = "owner" | "coordinator" | "volunteer" | "recipient" | "evaluator";

export type DecisionStatus =
  | "go"
  | "no_go"
  | "conditional"
  | "review_required"
  | "stale"
  | "blocked"
  | "draft";

export type GleanState =
  | "owner_review_required"
  | "coordinator_review_required"
  | "matching"
  | "coordinator_assignment_approval"
  | "invited"
  | "confirmed"
  | "in_progress"
  | "completion_review"
  | "weighed"
  | "closed"
  | "cancelled";

export type Provenance = "fixture" | "live";

/** Hard filters. Every field must be satisfied; scoring can never waive one. */
export interface HardConstraints {
  readonly ownerApprovalCurrent: boolean;
  readonly coordinatorApprovalCurrent: boolean;
  readonly trainedLeaderAvailable: boolean;
  readonly volunteerTrainingValid: boolean;
  readonly equipmentSatisfied: boolean;
  readonly availabilityIntersects: boolean;
  readonly recipientCapacitySufficient: boolean;
  readonly coordinatorNoRecipientPlanApproved: boolean;
  readonly unresolvedSafetyBlockers: number;
  readonly addressRevealPolicySatisfied: boolean;
  readonly insuranceWaiverSatisfied: boolean;
}

/** Ranking inputs are numeric only; protected traits have no field at all. */
export interface RankingFeatures {
  readonly travelFit: number;
  readonly scheduleFit: number;
  readonly recipientLogisticsFit: number;
  readonly equipmentFit: number;
  readonly fairnessRotation: number;
}

export interface Candidate {
  readonly volunteerId: string;
  readonly displayName: string;
  readonly trainingLevel: "trained_leader" | "trained_volunteer" | "trainee";
  readonly equipment: readonly string[];
  readonly constraints: HardConstraints;
  readonly ranking: RankingFeatures;
  /** Recent assignment count for the fairness component; never a protected trait. */
  readonly recentAssignments: number;
}

export type BlockerCode =
  | "owner_approval_missing"
  | "coordinator_approval_missing"
  | "trained_leader_missing"
  | "volunteer_training_missing"
  | "equipment_missing"
  | "availability_mismatch"
  | "recipient_capacity_insufficient"
  | "safety_blocker_unresolved"
  | "address_reveal_policy_unsatisfied"
  | "insurance_or_waiver_missing";

export interface ScoreComponents {
  readonly travelFit: number;
  readonly scheduleFit: number;
  readonly recipientLogisticsFit: number;
  readonly equipmentFit: number;
  readonly fairnessRotation: number;
}

export interface EligibilityDecision {
  readonly volunteerId: string;
  readonly displayName: string;
  readonly eligible: boolean;
  readonly blockers: readonly BlockerCode[];
  readonly score: number | null;
  readonly components: ScoreComponents | null;
}

export interface ForecastSummary {
  readonly forecastId: string;
  readonly treeId: string;
  readonly coarseArea: string;
  readonly readyStart: string;
  readonly readyEnd: string;
  readonly visibleCount: number;
  readonly countLower: number;
  readonly countUpper: number;
  readonly modelVersion: string;
  readonly dataVersion: string;
  readonly experimental: boolean;
  readonly issuedAt: string;
  readonly status: DecisionStatus;
  readonly confidenceReasons: readonly string[];
}

export interface AssignmentSummary {
  readonly assignmentId: string;
  readonly volunteerId: string;
  readonly displayName: string;
  readonly state: "invited" | "confirmed" | "partial_fill" | "cancelled";
  readonly revealNotBefore: string;
  readonly revealUntil: string;
}

export interface MatchingBoard {
  readonly provenance: Provenance;
  readonly organizationId: string;
  readonly gleanId: string;
  readonly gleanState: GleanState;
  readonly ownerApprovalCurrent: boolean;
  readonly coordinatorReadinessConfirmed: boolean;
  readonly forecast: ForecastSummary;
  readonly candidates: readonly Candidate[];
  readonly assignments: readonly AssignmentSummary[];
  readonly serverTieKeys: Readonly<Record<string, string>>;
}

export type RejectionReason =
  | "spoiled"
  | "damaged"
  | "undersized"
  | "pest_damage"
  | "recipient_declined";

export type WeightSource = "calibrated_scale" | "coordinator_manual";

export interface WeightInput {
  readonly grossKg: string;
  readonly netKg: string;
  readonly acceptedKg: string;
  readonly rejectedKg: string;
  readonly source: WeightSource;
  readonly scaleReference?: string;
  readonly rejectionReasons: readonly RejectionReason[];
  readonly evidenceSha256?: string;
}

export interface ReceiptInput {
  readonly gleanId: string;
  readonly recipientId: string;
  readonly harvestedOn: string;
  readonly weights: WeightInput;
}

/** Non-sensitive donation receipt: no address, volunteer, rejection, or scale details. */
export interface DonationReceipt {
  readonly receiptNumber: string;
  readonly gleanId: string;
  readonly recipientId: string;
  readonly donationDate: string;
  readonly acceptedKg: string;
  readonly crop: "european_plum";
  readonly provenance: Provenance;
  readonly issuedAt: string;
}

export interface FieldError {
  readonly path: string;
  readonly code: string;
  readonly message: string;
}

export type ValidationResult<T> =
  | { readonly ok: true; readonly value: T }
  | { readonly ok: false; readonly errors: readonly FieldError[] };

export interface AccessRevealRequest {
  readonly actorId: string;
  readonly actorRoles: readonly MembershipRole[];
  readonly gleanState: GleanState;
  readonly ownerApprovalCurrent: boolean;
  readonly consentActive: boolean;
  readonly assignment: AssignmentSummary | null;
  readonly now: string;
}

export type AccessDecision =
  | {
      readonly allowed: true;
      readonly expiresAt: string;
      readonly minimumWindowHours: number;
    }
  | { readonly allowed: false; readonly reason: string; readonly message: string };
