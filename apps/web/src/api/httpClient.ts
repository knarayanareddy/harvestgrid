/**
 * Live HTTP adapter.
 *
 * Requests use relative `/v1/...` paths, so the browser only ever talks to its own origin
 * (Vite proxies `/v1` to the API in development; the load balancer routes it in
 * production). No Ring OAuth, device, or media endpoint exists in this module.
 */

import { validateWeights } from "../domain/receipts";
import type {
  AssignmentSummary,
  DonationReceipt,
  MatchingBoard,
  ReceiptInput,
  ValidationResult,
} from "../domain/types";
import {
  ApiError,
  type ApproveAssignmentsInput,
  type CoordinatorApi,
  type CoordinatorSession,
} from "./CoordinatorApi";

interface ProblemBody {
  readonly code?: string;
  readonly title?: string;
  readonly status?: number;
}

async function requestJson<T>(
  path: string,
  init: RequestInit & { readonly idempotencyKey?: string },
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
  if (init.idempotencyKey) headers["Idempotency-Key"] = init.idempotencyKey;
  const response = await fetch(path, { ...init, headers: { ...headers, ...init.headers } });
  if (!response.ok) {
    let problem: ProblemBody = {};
    try {
      problem = (await response.json()) as ProblemBody;
    } catch {
      problem = {};
    }
    throw new ApiError(
      problem.code ?? "request_failed",
      response.status,
      problem.title ?? "The request could not be completed.",
    );
  }
  return (await response.json()) as T;
}

export class HttpCoordinatorApi implements CoordinatorApi {
  readonly session: CoordinatorSession;

  constructor(session: CoordinatorSession) {
    this.session = session;
  }

  async loadBoard(): Promise<MatchingBoard> {
    return requestJson<MatchingBoard>("/v1/coordination/board", { method: "GET" });
  }

  async approveAssignments(
    input: ApproveAssignmentsInput,
  ): Promise<readonly AssignmentSummary[]> {
    return requestJson<readonly AssignmentSummary[]>(`/v1/gleans/${input.gleanId}/assignments`, {
      method: "POST",
      body: JSON.stringify({
        volunteer_ids: input.volunteerIds,
        reveal_not_before: input.revealNotBefore,
        reveal_until: input.revealUntil,
      }),
      idempotencyKey: input.idempotencyKey,
    });
  }

  async recordReceipt(input: ReceiptInput): Promise<ValidationResult<DonationReceipt>> {
    // Validate locally first so an obvious arithmetic error never leaves the browser.
    const validation = validateWeights(input.weights, { roles: this.session.roles });
    if (!validation.ok) return validation;
    const receipt = await requestJson<DonationReceipt>(
      `/v1/gleans/${input.gleanId}/receipt`,
      {
        method: "POST",
        body: JSON.stringify({
          recipient_id: input.recipientId,
          harvested_on: input.harvestedOn,
          gross_kg: input.weights.grossKg,
          net_kg: input.weights.netKg,
          accepted_kg: input.weights.acceptedKg,
          rejected_kg: input.weights.rejectedKg,
          source: input.weights.source,
          scale_reference: input.weights.scaleReference ?? null,
          rejection_reasons: input.weights.rejectionReasons,
        }),
        idempotencyKey: `receipt/${input.gleanId}/${input.recipientId}/${input.harvestedOn}`,
      },
    );
    return { ok: true, value: receipt };
  }
}
