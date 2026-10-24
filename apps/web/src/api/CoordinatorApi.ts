/**
 * Coordinator API port.
 *
 * Every adapter receives exactly the fields the board needs. There is no `addressGet`,
 * no raw-media URL, and no Ring credential method on this interface: the coordinator UI
 * cannot reach those resources even by mistake.
 */

import type {
  AssignmentSummary,
  DonationReceipt,
  MatchingBoard,
  ReceiptInput,
  ValidationResult,
  MembershipRole,
} from "../domain/types";

export interface ApproveAssignmentsInput {
  readonly gleanId: string;
  readonly volunteerIds: readonly string[];
  readonly coordinatorId: string;
  readonly revealNotBefore: string;
  readonly revealUntil: string;
  readonly idempotencyKey: string;
}

export interface CoordinatorSession {
  readonly actorId: string;
  readonly roles: readonly MembershipRole[];
  readonly organizationId: string;
}

export interface CoordinatorApi {
  readonly session: CoordinatorSession;
  loadBoard(): Promise<MatchingBoard>;
  approveAssignments(input: ApproveAssignmentsInput): Promise<readonly AssignmentSummary[]>;
  recordReceipt(input: ReceiptInput): Promise<ValidationResult<DonationReceipt>>;
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}
