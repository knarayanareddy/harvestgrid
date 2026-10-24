/**
 * Volunteer pickup notices for the demo.
 *
 * Mirrors the guarantees of `services/notifications/harvestgrid_notifications/`: consent first,
 * a per-recipient daily cap, quiet hours in the volunteer's own timezone, duplicate suppression
 * keyed on `glean/state/recipient/template/version`, and a rendered body that is refused if it
 * contains a phone number or the glean's restricted address/access note.
 *
 * Bodies are rendered only for messages that will actually be sent. The UI shows a masked phone
 * number and the SHA-256 of the body — never the raw number and never a full address.
 */

import { sha256Hex } from "./sha256";

export const TEMPLATE_NAME = "pickup";
export const TEMPLATE_VERSION = "pickup-v2";
export const MAX_SMS_CHARS = 480;
export const SMS_CHARS_PER_SEGMENT = 153;
export const QUIET_HOURS_START = 21;
export const QUIET_HOURS_END = 7;
export const MAX_PER_RECIPIENT_PER_DAY = 3;
export const MAX_SENDS_PER_TICK = 50;

export interface Volunteer {
  readonly volunteerId: string;
  readonly displayName: string;
  readonly phoneE164: string;
  readonly smsOptIn: boolean;
  readonly smsOptInVersion: string | null;
  readonly timezone: string;
  readonly suppressed?: boolean;
  readonly travelNote: string;
}

export interface PickupAlert {
  readonly gleanId: string;
  readonly gleanState: "confirmed" | "cancelled";
  readonly cropLabel: string;
  readonly coarseAreaLabel: string;
  readonly windowStart: string;
  readonly windowEnd: string;
  readonly accessConstraints: readonly string[];
  readonly safetyNotes: readonly string[];
  readonly templateVersion: string;
}

export type OutcomeStatus = "sent" | "skipped" | "deferred";

export interface DispatchOutcome {
  readonly volunteerId: string;
  readonly displayName: string;
  readonly status: OutcomeStatus;
  readonly reason: string;
  readonly maskedPhone: string;
  readonly body: string | null;
  readonly bodySha256: string | null;
  readonly segments: number;
}

export interface DispatchReport {
  readonly at: string;
  readonly templateVersion: string;
  readonly sent: number;
  readonly skipped: number;
  readonly deferred: number;
  readonly outcomes: readonly DispatchOutcome[];
  readonly resultSha256: string;
}

export class NoticeError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "NoticeError";
    this.code = code;
  }
}

export function maskPhone(phoneE164: string): string {
  const digits = phoneE164.replace(/[^\d]/g, "");
  if (digits.length < 6) return "•••";
  return `+${digits.slice(0, 2)} ${digits.slice(2, 3)} ••• •• ${digits.slice(-2)}`;
}

export function localHour(iso: string, timezone: string): number {
  const formatted = new Intl.DateTimeFormat("en-GB", {
    timeZone: timezone,
    hour: "2-digit",
    hour12: false,
  }).format(new Date(iso));
  return Number.parseInt(formatted, 10) % 24;
}

export function isQuietHours(iso: string, timezone: string): boolean {
  const hour = localHour(iso, timezone);
  return hour >= QUIET_HOURS_START || hour < QUIET_HOURS_END;
}

export function idempotencyKey(alert: PickupAlert, volunteerId: string): string {
  return [alert.gleanId, alert.gleanState, volunteerId, TEMPLATE_NAME, alert.templateVersion].join(
    "/",
  );
}

function windowLabel(alert: PickupAlert): string {
  const start = new Date(alert.windowStart);
  const end = new Date(alert.windowEnd);
  const day = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Amsterdam",
    weekday: "short",
    day: "2-digit",
    month: "short",
  }).format(start);
  const clock = (value: Date): string =>
    new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/Amsterdam",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(value);
  return `${day} ${clock(start)}–${clock(end)} local`;
}

const PHONE_GUARD = /\+?\d[\d\s().-]{7,}\d/;
const ADDRESS_GUARD = /\b\d{1,4}\s+[A-Z][a-z]{2,}\s+(Street|Straat|Strasse|Road|Lane|Avenue)\b/;

export function renderNotice(
  alert: PickupAlert,
  restrictedMarkers: readonly string[] = [],
): string {
  const parts = [
    `HarvestGrid: ${alert.cropLabel} ready in ${alert.coarseAreaLabel}.`,
    `Window: ${windowLabel(alert)}.`,
  ];
  if (alert.accessConstraints.length > 0) {
    parts.push(`Access: ${alert.accessConstraints.join("; ")}.`);
  }
  if (alert.safetyNotes.length > 0) {
    parts.push(`Safety: ${alert.safetyNotes.join("; ")}.`);
  }
  parts.push("Reply YES to claim; a coordinator confirms every assignment.");
  parts.push(`Ref ${alert.templateVersion}`);
  const body = parts.join(" ");

  if (body.length > MAX_SMS_CHARS) {
    throw new NoticeError("message_too_long", "This notice would exceed the SMS length limit.");
  }
  if (PHONE_GUARD.test(body)) {
    throw new NoticeError(
      "message_contains_phone_number",
      "A notice may not contain a phone number; volunteers reach the coordinator in-app.",
    );
  }
  if (ADDRESS_GUARD.test(body)) {
    throw new NoticeError(
      "message_contains_address_pattern",
      "A notice may not contain a street address; only the coarse area is shared by SMS.",
    );
  }
  for (const marker of restrictedMarkers) {
    const probe = marker.trim().toLowerCase();
    if (probe.length >= 4 && body.toLowerCase().includes(probe)) {
      throw new NoticeError(
        "message_contains_restricted_content",
        "The glean's restricted access detail would have leaked into this notice, so it was blocked.",
      );
    }
  }
  return body;
}

export function messageSegments(body: string): number {
  return Math.max(1, Math.ceil(body.length / SMS_CHARS_PER_SEGMENT));
}

export function dispatchNotices(input: {
  readonly alert: PickupAlert;
  readonly volunteers: readonly Volunteer[];
  readonly restrictedMarkers?: readonly string[];
  readonly at: string;
  readonly ledger: Readonly<Record<string, number>>;
  readonly sentToday: Readonly<Record<string, number>>;
}): { readonly report: DispatchReport; readonly ledger: Record<string, number>; readonly sentToday: Record<string, number> } {
  const markers = input.restrictedMarkers ?? [];
  const ledger: Record<string, number> = { ...input.ledger };
  const sentToday: Record<string, number> = { ...input.sentToday };
  const outcomes: DispatchOutcome[] = [];
  let sendsThisTick = 0;

  for (const volunteer of input.volunteers) {
    const base = {
      volunteerId: volunteer.volunteerId,
      displayName: volunteer.displayName,
      maskedPhone: maskPhone(volunteer.phoneE164),
    };
    const skip = (status: OutcomeStatus, reason: string): DispatchOutcome => ({
      ...base,
      status,
      reason,
      body: null,
      bodySha256: null,
      segments: 0,
    });

    if (volunteer.suppressed === true) {
      outcomes.push(skip("skipped", "recipient_suppressed"));
      continue;
    }
    if (!volunteer.smsOptIn) {
      outcomes.push(skip("skipped", "sms_opt_in_required"));
      continue;
    }
    const key = idempotencyKey(input.alert, volunteer.volunteerId);
    if ((ledger[key] ?? 0) > 0) {
      outcomes.push(skip("skipped", "duplicate_suppressed"));
      continue;
    }
    if (isQuietHours(input.at, volunteer.timezone)) {
      outcomes.push(skip("deferred", "quiet_hours"));
      continue;
    }
    if (sendsThisTick >= MAX_SENDS_PER_TICK) {
      outcomes.push(skip("deferred", "tick_cap_reached"));
      continue;
    }
    if ((sentToday[volunteer.volunteerId] ?? 0) >= MAX_PER_RECIPIENT_PER_DAY) {
      outcomes.push(skip("skipped", "daily_cap_reached"));
      continue;
    }
    let body: string;
    try {
      body = renderNotice(input.alert, markers);
    } catch (error) {
      outcomes.push(
        skip("skipped", error instanceof NoticeError ? error.code : "notice_render_failed"),
      );
      continue;
    }
    ledger[key] = 1;
    sentToday[volunteer.volunteerId] = (sentToday[volunteer.volunteerId] ?? 0) + 1;
    sendsThisTick += 1;
    outcomes.push({
      ...base,
      status: "sent",
      reason: "delivered_to_provider",
      body,
      bodySha256: sha256Hex(body),
      segments: messageSegments(body),
    });
  }

  const count = (status: OutcomeStatus): number =>
    outcomes.filter((outcome) => outcome.status === status).length;
  const digestPayload = outcomes.map((outcome) => ({
    volunteerId: outcome.volunteerId,
    status: outcome.status,
    reason: outcome.reason,
    bodySha256: outcome.bodySha256,
  }));
  return {
    report: {
      at: input.at,
      templateVersion: input.alert.templateVersion,
      sent: count("sent"),
      skipped: count("skipped"),
      deferred: count("deferred"),
      outcomes,
      resultSha256: sha256Hex(JSON.stringify(digestPayload)),
    },
    ledger,
    sentToday,
  };
}
