/**
 * Client-side demo store.
 *
 * `DemoStore` is the single source of truth for the interactive demo: tree onboarding, the
 * privacy view (ROI polygon plus exclusion masks), the simulated Ring frame, the ripening curve,
 * coordinator approval, and volunteer dispatch. Every mutation recomputes derived state through
 * the pure modules next to it, so the UI never keeps a second copy of a rule.
 *
 * The store is framework-free and synchronous: React subscribes with
 * `useSyncExternalStore`, and tests drive it directly. It is only constructed when the build is
 * in demo mode, so nothing here can silently replace a live API.
 *
 * Honesty rules carried from the Python services:
 * - projections stay `experimental` with a `fixture_unreviewed` prior and always require human
 *   readiness review; internal ripeness and food safety are never inferred;
 * - a notice is rendered only for a recipient that will actually receive it, and the store keeps
 *   digests and masked phone numbers rather than bodies and numbers;
 * - the simulated webhook carries detections, never media references.
 */

import { filterClusters, ripeShare, syntheticClusters, type FilteredFrame, type FruitCluster } from "./detections";
import {
  allowedRaster,
  maskCoverageOfRoi,
  validateView,
  type AllowedRaster,
  type MaskKind,
  type MaskRegion,
  type Point,
  type ViewValidation,
} from "./geometry";
import {
  dispatchNotices,
  maskPhone,
  type DispatchReport,
  type PickupAlert,
  type Volunteer,
} from "./notices";
import {
  harvestWindow,
  projectRipening,
  seasonalTemperatures,
  type DailyTemperature,
  type HarvestWindow,
  type RipeningProjection,
} from "./phenology";

export const AS_OF = "2026-10-24";
export const COARSE_GEOHASH = "u173z";

export interface TreeProfile {
  readonly treeId: string;
  readonly organizationId: string;
  readonly cultivarId: string;
  readonly coarseGeohash: string;
  readonly regionLabel: string;
  readonly ownerConsentActive: boolean;
  readonly cameraCollectionConsent: boolean;
  readonly collectionSchedule: string;
}

export interface ViewState {
  readonly roi: readonly Point[];
  readonly masks: readonly MaskRegion[];
  readonly ownerApproved: boolean;
  readonly coordinatorApproved: boolean;
}

export interface WebhookEvent {
  readonly eventId: string;
  readonly receivedAt: string;
  readonly deviceId: string;
  readonly signatureValid: boolean;
  readonly duplicate: boolean;
  readonly clusters: readonly FruitCluster[];
  readonly frame: FilteredFrame;
  readonly ripeShare: number;
}

export interface GleanRecord {
  readonly gleanId: string;
  readonly ownerApprovalCurrent: boolean;
  readonly coordinatorReadinessConfirmed: boolean;
  readonly approvedVolunteerIds: readonly string[];
  readonly dispatch: DispatchReport | null;
}

export interface DemoState {
  readonly tree: TreeProfile;
  readonly view: ViewState;
  readonly validation: ViewValidation;
  readonly raster: AllowedRaster;
  readonly warmthOffsetC: number;
  readonly projection: RipeningProjection;
  readonly window: HarvestWindow;
  readonly webhook: WebhookEvent | null;
  readonly glean: GleanRecord;
  readonly volunteers: readonly Volunteer[];
  readonly ledger: Readonly<Record<string, number>>;
  readonly sentToday: Readonly<Record<string, number>>;
  readonly activity: readonly { readonly at: string; readonly message: string }[];
}

export const DEFAULT_ROI: readonly Point[] = [
  { x: 0.1, y: 0.12 },
  { x: 0.88, y: 0.09 },
  { x: 0.92, y: 0.84 },
  { x: 0.13, y: 0.9 },
];

export const DEFAULT_MASKS: readonly MaskRegion[] = [
  { id: "mask-neighbor", kind: "neighbor_property", x: 0.66, y: 0, width: 0.34, height: 0.34 },
  { id: "mask-sidewalk", kind: "public_sidewalk", x: 0, y: 0.84, width: 1, height: 0.16 },
];

export const DEMO_VOLUNTEERS: readonly Volunteer[] = [
  {
    volunteerId: "volunteer-a",
    displayName: "Fixture volunteer A",
    phoneE164: "+31600000001",
    smsOptIn: true,
    smsOptInVersion: "sms-opt-in-v1",
    timezone: "Europe/Amsterdam",
    travelNote: "10 min by bike",
  },
  {
    volunteerId: "volunteer-b",
    displayName: "Fixture volunteer B",
    phoneE164: "+31600000002",
    smsOptIn: true,
    smsOptInVersion: "sms-opt-in-v1",
    timezone: "Europe/Amsterdam",
    travelNote: "trained leader",
  },
  {
    volunteerId: "volunteer-c",
    displayName: "Fixture volunteer C",
    phoneE164: "+31600000003",
    smsOptIn: false,
    smsOptInVersion: null,
    timezone: "Europe/Amsterdam",
    travelNote: "has not opted in to SMS",
  },
];

const DEFAULT_TREE: TreeProfile = {
  treeId: "tree-plum-01",
  organizationId: "org-fixture-a",
  cultivarId: "european_plum",
  coarseGeohash: COARSE_GEOHASH,
  regionLabel: "Delft south",
  ownerConsentActive: true,
  cameraCollectionConsent: true,
  collectionSchedule: "every 6 hours, 07:00–20:00",
};

/** 2026-07-02 08:00 UTC: morning in Delft, outside quiet hours. */
const DEFAULT_NOW = "2026-07-02T08:00:00Z";

export interface DemoStoreOptions {
  readonly now?: string;
  readonly roi?: readonly Point[];
  readonly masks?: readonly MaskRegion[];
  readonly volunteers?: readonly Volunteer[];
  readonly clusters?: readonly FruitCluster[];
}

export class DemoStore {
  private state: DemoState;
  private readonly listeners = new Set<() => void>();
  private readonly nowIso: string;
  private readonly clusters: readonly FruitCluster[];

  constructor(options: DemoStoreOptions = {}) {
    this.nowIso = options.now ?? DEFAULT_NOW;
    this.clusters = options.clusters ?? syntheticClusters();
    this.state = this.build({
      tree: DEFAULT_TREE,
      view: {
        roi: options.roi ?? DEFAULT_ROI,
        masks: options.masks ?? DEFAULT_MASKS,
        ownerApproved: false,
        coordinatorApproved: false,
      },
      warmthOffsetC: 0,
      webhook: null,
      glean: {
        gleanId: "glean-demo-01",
        ownerApprovalCurrent: true,
        coordinatorReadinessConfirmed: false,
        approvedVolunteerIds: [],
        dispatch: null,
      },
      volunteers: options.volunteers ?? DEMO_VOLUNTEERS,
      ledger: {},
      sentToday: {},
      activity: [{ at: this.nowIso, message: "Demo started with fixture tree, weather, and volunteers." }],
    });
  }

  // ---------------------------------------------------------------- subscription

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  readonly getSnapshot = (): DemoState => this.state;

  private emit(): void {
    for (const listener of this.listeners) listener();
  }

  private update(patch: Partial<DemoState>, message?: string): void {
    const activity =
      message === undefined
        ? this.state.activity
        : [{ at: this.nowIso, message }, ...this.state.activity].slice(0, 12);
    this.state = this.build({ ...toDraft(this.state), ...patch, activity });
    this.emit();
  }

  private build(draft: Draft): DemoState {
    const validation = validateView(draft.view.roi, draft.view.masks);
    const raster = allowedRaster(draft.view.roi, draft.view.masks);
    const observed = seasonalTemperatures("2026-04-01", 118, {
      warmthOffsetC: draft.warmthOffsetC,
    });
    const forecast: DailyTemperature[] = seasonalTemperatures("2026-07-28", 10, {
      warmthOffsetC: draft.warmthOffsetC,
    }).map((day) => ({ ...day, kind: "forecast" }));
    const projection = projectRipening({
      coarseGeohash: draft.tree.coarseGeohash,
      asOf: AS_OF,
      observed,
      forecast,
    });
    return {
      ...draft,
      validation,
      raster,
      projection,
      window: harvestWindow(projection, "2026-08-26"),
    };
  }

  // ---------------------------------------------------------------- tree onboarding

  completeOnboarding(input: {
    readonly regionLabel: string;
    readonly ownerConsentActive: boolean;
    readonly cameraCollectionConsent: boolean;
  }): void {
    this.update(
      {
        tree: {
          ...this.state.tree,
          regionLabel: input.regionLabel.trim() === "" ? this.state.tree.regionLabel : input.regionLabel.trim(),
          ownerConsentActive: input.ownerConsentActive,
          cameraCollectionConsent: input.cameraCollectionConsent,
        },
      },
      `Tree ${this.state.tree.treeId} onboarding recorded with explicit owner and camera-collection consent.`,
    );
  }

  // ---------------------------------------------------------------- privacy view

  moveRoiPoint(index: number, next: Point): void {
    const roi = this.state.view.roi.map((point, position) =>
      position === index
        ? { x: clamp01(next.x), y: clamp01(next.y) }
        : point,
    );
    this.update(
      { view: { ...this.state.view, roi, ownerApproved: false, coordinatorApproved: false } },
      `Moved ROI vertex ${index + 1}; approvals reset because the geometry changed.`,
    );
  }

  setMaskRectangle(id: string, next: { x: number; y: number; width: number; height: number }): void {
    const masks = this.state.view.masks.map((mask) =>
      mask.id === id
        ? {
            ...mask,
            x: clamp01(next.x),
            y: clamp01(next.y),
            width: Math.min(1 - clamp01(next.x), Math.max(0.02, next.width)),
            height: Math.min(1 - clamp01(next.y), Math.max(0.02, next.height)),
          }
        : mask,
    );
    this.update(
      { view: { ...this.state.view, masks, ownerApproved: false, coordinatorApproved: false } },
      "Adjusted a privacy mask; the view needs re-approval before collection resumes.",
    );
  }

  addMask(kind: MaskKind): void {
    const id = `mask-${kind}-${this.state.view.masks.length + 1}`;
    const masks = [
      ...this.state.view.masks,
      { id, kind, x: 0.35, y: 0.35, width: 0.2, height: 0.2 } satisfies MaskRegion,
    ];
    this.update(
      { view: { ...this.state.view, masks, ownerApproved: false, coordinatorApproved: false } },
      `Added a ${kind.replaceAll("_", " ")} exclusion mask.`,
    );
  }

  removeMask(id: string): void {
    const masks = this.state.view.masks.filter((mask) => mask.id !== id);
    this.update(
      { view: { ...this.state.view, masks, ownerApproved: false, coordinatorApproved: false } },
      "Removed an exclusion mask; unmasked areas become eligible again.",
    );
  }

  approveView(actor: "owner" | "coordinator"): void {
    if (!this.state.validation.ok) {
      this.update(
        {},
        `${actor === "owner" ? "Owner" : "Coordinator"} approval refused: ${this.state.validation.reason ?? "invalid view"}.`,
      );
      return;
    }
    const view =
      actor === "owner"
        ? { ...this.state.view, ownerApproved: true }
        : { ...this.state.view, coordinatorApproved: true };
    const both = view.ownerApproved && view.coordinatorApproved;
    this.update(
      {
        view,
        glean: both
          ? this.state.glean
          : { ...this.state.glean, dispatch: null },
      },
      both
        ? "View approved by owner and coordinator; collection may run."
        : `${actor === "owner" ? "Owner" : "Coordinator"} approval recorded.`,
    );
  }

  // ---------------------------------------------------------------- camera webhook

  simulateWebhook(): WebhookEvent {
    const eventId = `evt-${String(this.state.activity.length + 1).padStart(3, "0")}`;
    const signatureValid = true;
    const duplicate = this.state.webhook?.signatureValid === true && this.state.webhook.clusters.length > 0
      ? this.state.webhook.eventId.endsWith("dup")
      : false;
    const frame = filterClusters(this.clusters, this.state.raster);
    const event: WebhookEvent = {
      eventId: duplicate ? `${eventId}-dup` : eventId,
      receivedAt: this.nowIso,
      deviceId: "ring-fixture-01",
      signatureValid,
      duplicate,
      clusters: this.clusters,
      frame,
      ripeShare: ripeShare(frame.kept),
    };
    this.update(
      { webhook: event },
      signatureValid
        ? `Webhook accepted (HMAC verified): ${frame.kept.length} clusters inside the privacy ROI, ${frame.dropped.length} excluded.`
        : "Webhook rejected: signature did not verify.",
    );
    return event;
  }

  // ---------------------------------------------------------------- weather / ripening

  setWarmthOffset(offsetC: number): void {
    const bounded = Math.max(-6, Math.min(8, offsetC));
    this.update(
      { warmthOffsetC: bounded },
      `Weather offset set to ${bounded > 0 ? "+" : ""}${bounded} °C; the ripening curve recomputed.`,
    );
  }

  // ---------------------------------------------------------------- coordinator + dispatch

  confirmReadiness(confirmed: boolean): void {
    this.update(
      { glean: { ...this.state.glean, coordinatorReadinessConfirmed: confirmed } },
      confirmed
        ? "Coordinator confirmed the human readiness checklist."
        : "Readiness confirmation withdrawn.",
    );
  }

  setAssignments(volunteerIds: readonly string[]): void {
    this.update(
      { glean: { ...this.state.glean, approvedVolunteerIds: [...volunteerIds] } },
      `Approved ${volunteerIds.length} assignment(s). Exact details stay hidden until each volunteer confirms.`,
    );
  }

  /** True only when approval, approvals-current, and an approved view all hold. */
  canDispatch(): boolean {
    const { glean, view } = this.state;
    return (
      this.state.validation.ok &&
      view.ownerApproved &&
      view.coordinatorApproved &&
      glean.ownerApprovalCurrent &&
      glean.coordinatorReadinessConfirmed &&
      glean.approvedVolunteerIds.length > 0
    );
  }

  dispatch(): DispatchReport | null {
    if (!this.canDispatch()) {
      this.update(
        {},
        "Dispatch blocked: the view must be approved, readiness confirmed, and at least one volunteer assigned.",
      );
      return null;
    }
    const window = this.state.window;
    if (!window.available || window.start === null || window.end === null) {
      this.update({}, "Dispatch blocked: there is no harvest window to invite volunteers to.");
      return null;
    }
    const recipients = this.state.volunteers.filter((volunteer) =>
      this.state.glean.approvedVolunteerIds.includes(volunteer.volunteerId),
    );
    const alert: PickupAlert = {
      gleanId: this.state.glean.gleanId,
      gleanState: "confirmed",
      cropLabel: "European plum (visible estimate only)",
      coarseAreaLabel: this.state.tree.regionLabel,
      windowStart: `${window.start}T08:00:00Z`,
      windowEnd: `${window.end}T12:00:00Z`,
      accessConstraints: ["street parking only", "meet at the coarse area pin"],
      safetyNotes: ["closed shoes", "ladder provided"],
      templateVersion: "pickup-v2",
    };
    const result = dispatchNotices({
      alert,
      volunteers: recipients,
      restrictedMarkers: ["FIXTURE 1 Example Street", "gate code 0000"],
      at: this.nowIso,
      ledger: this.state.ledger,
      sentToday: this.state.sentToday,
    });
    this.update(
      { glean: { ...this.state.glean, dispatch: result.report }, ledger: result.ledger, sentToday: result.sentToday },
      `Dispatch finished: ${result.report.sent} sent, ${result.report.skipped} skipped, ${result.report.deferred} deferred. Bodies are shown as digests.`,
    );
    return result.report;
  }

  maskCoverage(): number {
    return maskCoverageOfRoi(this.state.view.roi, this.state.view.masks);
  }

  maskedPhone(volunteerId: string): string {
    const volunteer = this.state.volunteers.find((item) => item.volunteerId === volunteerId);
    return volunteer ? maskPhone(volunteer.phoneE164) : "•••";
  }
}

interface Draft {
  tree: TreeProfile;
  view: ViewState;
  warmthOffsetC: number;
  webhook: WebhookEvent | null;
  glean: GleanRecord;
  volunteers: readonly Volunteer[];
  ledger: Readonly<Record<string, number>>;
  sentToday: Readonly<Record<string, number>>;
  activity: readonly { readonly at: string; readonly message: string }[];
}

function toDraft(state: DemoState): Draft {
  const { validation: _validation, raster: _raster, projection: _projection, window: _window, ...draft } =
    state;
  return draft;
}

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}
