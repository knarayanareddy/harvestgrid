/**
 * Weight entry and non-sensitive donation receipts (FR-HG-014 display side, AC-HG-019).
 *
 * Kilogram strings are parsed into integer grams before any arithmetic, so
 * `8.1 + 0.2` style float drift cannot silently pass or fail validation. The receipt that
 * leaves this module is deliberately minimal: no address, no volunteer identity, no
 * rejection reasons, no scale or evidence references (SEC-HG-007).
 */

import type {
  DonationReceipt,
  FieldError,
  MembershipRole,
  ReceiptInput,
  ValidationResult,
  WeightInput,
} from "./types";

export const CROP = "european_plum" as const;
const MAX_KG = 10_000;
const GRAMS_PER_KG = 1000;
const HEX64 = /^[0-9a-f]{64}$/;

export interface ParsedWeights {
  readonly grossGrams: number;
  readonly netGrams: number;
  readonly acceptedGrams: number;
  readonly rejectedGrams: number;
}

function parseKg(value: string, path: string, errors: FieldError[]): number | null {
  const text = value.trim();
  if (!text) {
    errors.push({ path, code: "required", message: "Enter a weight in kilograms." });
    return null;
  }
  if (!/^\d+(\.\d{1,3})?$/.test(text)) {
    errors.push({
      path,
      code: "invalid_decimal",
      message: "Use kilograms with up to three decimals, e.g. 8.250.",
    });
    return null;
  }
  const grams = Math.round(Number(text) * GRAMS_PER_KG);
  if (grams > MAX_KG * GRAMS_PER_KG) {
    errors.push({ path, code: "out_of_range", message: "Weight exceeds the recorded maximum." });
    return null;
  }
  return grams;
}

export function formatKg(grams: number): string {
  return (grams / GRAMS_PER_KG).toFixed(3);
}

/** Validate the four weights, their arithmetic, and the required evidence. */
export function validateWeights(
  weights: WeightInput,
  context: { readonly roles: readonly MembershipRole[] },
): ValidationResult<ParsedWeights> {
  const errors: FieldError[] = [];
  const gross = parseKg(weights.grossKg, "weights.grossKg", errors);
  const net = parseKg(weights.netKg, "weights.netKg", errors);
  const accepted = parseKg(weights.acceptedKg, "weights.acceptedKg", errors);
  const rejected = parseKg(weights.rejectedKg, "weights.rejectedKg", errors);

  if (gross !== null && net !== null && net > gross) {
    errors.push({
      path: "weights.netKg",
      code: "net_exceeds_gross",
      message: "Net weight cannot exceed gross weight.",
    });
  }
  if (net !== null && accepted !== null && rejected !== null) {
    if (accepted + rejected !== net) {
      errors.push({
        path: "weights.acceptedKg",
        code: "weights_do_not_balance",
        message: "Accepted plus rejected weight must equal net weight.",
      });
    }
    if (rejected > 0 && weights.rejectionReasons.length === 0) {
      errors.push({
        path: "weights.rejectionReasons",
        code: "rejection_reason_required",
        message: "Select a coarse rejection reason when rejected weight is recorded.",
      });
    }
  }
  if (weights.source === "coordinator_manual") {
    if (!context.roles.includes("coordinator")) {
      errors.push({
        path: "weights.source",
        code: "coordinator_required",
        message: "Only a coordinator may record a manual weight.",
      });
    }
    if (!weights.scaleReference?.trim()) {
      errors.push({
        path: "weights.scaleReference",
        code: "manual_source_note_required",
        message: "Record why a manual weight was used.",
      });
    }
  }
  if (weights.source === "calibrated_scale" && weights.scaleReference !== undefined) {
    if (weights.scaleReference.trim().length < 2) {
      errors.push({
        path: "weights.scaleReference",
        code: "scale_reference_invalid",
        message: "Provide the scale identifier or omit it.",
      });
    }
  }
  if (weights.evidenceSha256 !== undefined && !HEX64.test(weights.evidenceSha256)) {
    errors.push({
      path: "weights.evidenceSha256",
      code: "evidence_hash_invalid",
      message: "Evidence hash must be 64 lowercase hexadecimal characters.",
    });
  }
  if (errors.length > 0) return { ok: false, errors };
  return {
    ok: true,
    value: {
      grossGrams: gross as number,
      netGrams: net as number,
      acceptedGrams: accepted as number,
      rejectedGrams: rejected as number,
    },
  };
}

/** Deterministic receipt number: no personal data, stable for the same input. */
export function receiptNumber(input: ReceiptInput): string {
  const seed = `${input.gleanId}:${input.recipientId}:${input.harvestedOn}:${input.weights.acceptedKg}`;
  let hash = 0x811c9dc5;
  for (let index = 0; index < seed.length; index += 1) {
    hash ^= seed.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `HG-${hash.toString(16).padStart(8, "0").toUpperCase()}`;
}

export function buildReceipt(
  input: ReceiptInput,
  parsed: ParsedWeights,
  context: { readonly provenance: "fixture" | "live"; readonly now: string },
): DonationReceipt {
  return {
    receiptNumber: receiptNumber(input),
    gleanId: input.gleanId,
    recipientId: input.recipientId,
    donationDate: input.harvestedOn,
    acceptedKg: formatKg(parsed.acceptedGrams),
    crop: CROP,
    provenance: context.provenance,
    issuedAt: context.now,
  };
}

/** The only receipt fields permitted in exports, logs, prompts, or public views. */
export function receiptExport(receipt: DonationReceipt): Readonly<Record<string, string>> {
  return {
    receipt_number: receipt.receiptNumber,
    glean_id: receipt.gleanId,
    recipient_id: receipt.recipientId,
    donation_date: receipt.donationDate,
    accepted_weight_kg: receipt.acceptedKg,
    crop: receipt.crop,
    provenance: receipt.provenance,
  };
}

export function summarizeWeights(parsed: ParsedWeights): string {
  const rejected =
    parsed.rejectedGrams > 0 ? `, ${formatKg(parsed.rejectedGrams)} kg rejected` : "";
  return `${formatKg(parsed.grossGrams)} kg gross → ${formatKg(parsed.netGrams)} kg net${rejected}`;
}
