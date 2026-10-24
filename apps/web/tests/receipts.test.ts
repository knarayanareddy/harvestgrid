import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  buildReceipt,
  formatKg,
  receiptExport,
  receiptNumber,
  summarizeWeights,
  validateWeights,
} from "../src/domain/receipts";
import type { ReceiptInput, WeightInput } from "../src/domain/types";

const COORDINATOR = { roles: ["coordinator"] as const };
const LEADER = { roles: ["volunteer"] as const };

function weights(overrides: Partial<WeightInput> = {}): WeightInput {
  return {
    grossKg: "10.0",
    netKg: "8.5",
    acceptedKg: "8.0",
    rejectedKg: "0.5",
    source: "calibrated_scale",
    scaleReference: "scale-01",
    rejectionReasons: ["spoiled"],
    ...overrides,
  };
}

function receiptInput(overrides: Partial<WeightInput> = {}): ReceiptInput {
  return {
    gleanId: "glean-fixture",
    recipientId: "recipient-fixture",
    harvestedOn: "2026-10-30",
    weights: weights(overrides),
  };
}

describe("weight validation", () => {
  it("accepts balanced weights and converts them to integer grams", () => {
    const result = validateWeights(weights(), COORDINATOR);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.value).toEqual({
      grossGrams: 10_000,
      netGrams: 8_500,
      acceptedGrams: 8_000,
      rejectedGrams: 500,
    });
  });

  it("rejects missing, non-numeric, and over-precise weights", () => {
    const empty = validateWeights(weights({ grossKg: "  " }), COORDINATOR);
    expect(empty.ok).toBe(false);
    if (!empty.ok) {
      expect(empty.errors[0]).toEqual({
        path: "weights.grossKg",
        code: "required",
        message: "Enter a weight in kilograms.",
      });
    }
    const letters = validateWeights(weights({ netKg: "eight" }), COORDINATOR);
    expect(letters.ok).toBe(false);
    const precise = validateWeights(weights({ acceptedKg: "1.2345" }), COORDINATOR);
    expect(precise.ok).toBe(false);
    const huge = validateWeights(weights({ grossKg: "20000" }), COORDINATOR);
    expect(huge.ok).toBe(false);
  });

  it("enforces the arithmetic relationships", () => {
    const netAboveGross = validateWeights(
      weights({ grossKg: "8.0", netKg: "9.0", acceptedKg: "9.0", rejectedKg: "0" }),
      COORDINATOR,
    );
    expect(netAboveGross.ok).toBe(false);
    if (!netAboveGross.ok) {
      expect(netAboveGross.errors.map((error) => error.code)).toContain("net_exceeds_gross");
    }
    const unbalanced = validateWeights(weights({ acceptedKg: "7.0" }), COORDINATOR);
    expect(unbalanced.ok).toBe(false);
    if (!unbalanced.ok) {
      expect(unbalanced.errors.map((error) => error.code)).toContain("weights_do_not_balance");
    }
    const noReason = validateWeights(weights({ rejectionReasons: [] }), COORDINATOR);
    expect(noReason.ok).toBe(false);
    if (!noReason.ok) {
      expect(noReason.errors.map((error) => error.code)).toContain(
        "rejection_reason_required",
      );
    }
  });

  it("treats kilogram decimals as integers, so float drift cannot break validation", () => {
    const result = validateWeights(
      weights({
        grossKg: "8.1",
        netKg: "8.1",
        acceptedKg: "8.0",
        rejectedKg: "0.1",
        rejectionReasons: ["damaged"],
      }),
      COORDINATOR,
    );
    expect(result.ok).toBe(true);
  });

  it("requires coordinator authority and a note for manual weights", () => {
    const asLeader = validateWeights(
      weights({ source: "coordinator_manual", scaleReference: "no scale available" }),
      LEADER,
    );
    expect(asLeader.ok).toBe(false);
    if (!asLeader.ok) {
      expect(asLeader.errors.map((error) => error.code)).toContain("coordinator_required");
    }
    const noNote = validateWeights(
      weights({ source: "coordinator_manual", scaleReference: "  " }),
      COORDINATOR,
    );
    expect(noNote.ok).toBe(false);
    if (!noNote.ok) {
      expect(noNote.errors.map((error) => error.code)).toContain(
        "manual_source_note_required",
      );
    }
    const manual = validateWeights(
      weights({ source: "coordinator_manual", scaleReference: "borrowed scale, checked" }),
      COORDINATOR,
    );
    expect(manual.ok).toBe(true);
  });

  it("validates the optional evidence hash", () => {
    const good = validateWeights(weights({ evidenceSha256: "a".repeat(64) }), COORDINATOR);
    expect(good.ok).toBe(true);
    const bad = validateWeights(weights({ evidenceSha256: "not-a-hash" }), COORDINATOR);
    expect(bad.ok).toBe(false);
    if (!bad.ok) {
      expect(bad.errors.map((error) => error.code)).toEqual(["evidence_hash_invalid"]);
    }
    const shortScale = validateWeights(weights({ scaleReference: "x" }), COORDINATOR);
    expect(shortScale.ok).toBe(false);
  });
});

describe("donation receipt", () => {
  const parsed = (() => {
    const result = validateWeights(weights(), COORDINATOR);
    if (!result.ok) throw new Error("fixture weights must validate");
    return result.value;
  })();

  it("exports exactly the fields the receipt contract allows", () => {
    const schema = JSON.parse(
      readFileSync(
        resolve(process.cwd(), "../../packages/contracts/donation-receipt.schema.json"),
        "utf8",
      ),
    ) as { required: string[]; properties: Record<string, unknown> };
    const receipt = buildReceipt(receiptInput(), parsed, {
      provenance: "fixture",
      now: "2026-10-30T16:00:00Z",
    });
    const exported = receiptExport(receipt);
    expect(new Set(Object.keys(exported))).toEqual(new Set(schema.required.concat("crop", "provenance")));
    for (const field of Object.keys(exported)) {
      expect(schema.properties).toHaveProperty(field);
    }
    expect(exported).toMatchObject({
      accepted_weight_kg: "8.000",
      donation_date: "2026-10-30",
      provenance: "fixture",
      crop: "european_plum",
    });
    expect(exported.receipt_number).toMatch(/^HG-[0-9A-F]{8,20}$/);
  });

  it("formats weights with three decimals", () => {
    expect(formatKg(8_500)).toBe("8.500");
    expect(summarizeWeights(parsed)).toBe("10.000 kg gross → 8.500 kg net, 0.500 kg rejected");
    expect(summarizeWeights({ ...parsed, rejectedGrams: 0 })).toBe(
      "10.000 kg gross → 8.500 kg net",
    );
  });

  it("issues a deterministic, non-sensitive receipt", () => {
    const input = receiptInput();
    const receipt = buildReceipt(input, parsed, {
      provenance: "fixture",
      now: "2026-10-30T16:00:00Z",
    });
    expect(receipt).toEqual({
      receiptNumber: receiptNumber(input),
      gleanId: "glean-fixture",
      recipientId: "recipient-fixture",
      donationDate: "2026-10-30",
      acceptedKg: "8.000",
      crop: "european_plum",
      provenance: "fixture",
      issuedAt: "2026-10-30T16:00:00Z",
    });
    expect(receiptNumber(input)).toMatch(/^HG-[0-9A-F]{8}$/);
    expect(receiptNumber(input)).toBe(receiptNumber(receiptInput()));

    const exported = receiptExport(receipt);
    const serialized = JSON.stringify(exported);
    expect(Object.keys(exported).sort()).toEqual([
      "accepted_weight_kg",
      "crop",
      "donation_date",
      "glean_id",
      "provenance",
      "receipt_number",
      "recipient_id",
    ]);
    for (const forbidden of [
      "address",
      "access",
      "volunteer",
      "rejection",
      "scale",
      "evidence",
      "latitude",
    ]) {
      expect(serialized.toLowerCase()).not.toContain(forbidden);
    }
  });

  it("changes the receipt number when the accepted weight changes", () => {
    const lighter = receiptNumber(receiptInput({ acceptedKg: "7.5" }));
    expect(lighter).not.toBe(receiptNumber(receiptInput()));
  });
});
