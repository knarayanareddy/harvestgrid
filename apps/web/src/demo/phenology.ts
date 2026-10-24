/**
 * Growing-degree-day ripening curve for the interactive demo.
 *
 * This is a browser mirror of `services/forecasting/harvestgrid_forecasting/ripening.py`, kept
 * deliberately small: same stage order, same thresholds, same "experimental unless the prior is
 * approved" rule. The Python service remains the authority for stored projections — the demo
 * exists so a judge can drag a slider and watch a curve move, not to issue forecasts.
 *
 * Nothing here infers internal ripeness, sugar content, disease, or food safety. Colour and
 * temperature only describe visible phenology; `internalRiPennessInferred` and
 * `foodSafetyInferred` stay false on every result.
 */

export const STAGES = [
  "dormant",
  "bud_break",
  "bloom",
  "fruit_set",
  "pit_hardening",
  "veraison",
  "harvest_ripe",
  "past_ripe",
] as const;

export type Stage = (typeof STAGES)[number];

export const STAGE_LABELS: Readonly<Record<Stage, string>> = {
  dormant: "Dormant",
  bud_break: "Bud break",
  bloom: "Bloom",
  fruit_set: "Fruit set",
  pit_hardening: "Pit hardening",
  veraison: "Veraison",
  harvest_ripe: "Harvest ripe",
  past_ripe: "Past ripe",
};

/** Unreviewed fixture prior, identical to `fixtures.fixture_prior()`. */
export const FIXTURE_PRIOR = {
  cultivarId: "european_plum",
  contentVersion: "european_plum_v1",
  baseTemperatureC: 5,
  reviewStatus: "fixture_unreviewed",
  stageGdd: [
    ["dormant", 0],
    ["bud_break", 60],
    ["bloom", 140],
    ["fruit_set", 240],
    ["pit_hardening", 460],
    ["veraison", 780],
    ["harvest_ripe", 1050],
    ["past_ripe", 1350],
  ] as readonly (readonly [Stage, number])[],
} as const;

export const MIN_OBSERVED_DAYS = 14;
export const FORECAST_HORIZON_DAYS = 10;
export const WINDOW_SPREAD_RATIO = 0.2;
export const MIN_WINDOW_SPREAD_DAYS = 2;
export const RIPE_GRACE_DAYS = 14;

export interface DailyTemperature {
  readonly date: string;
  readonly meanTemperatureC: number;
  readonly kind: "observed" | "forecast";
}

export interface RipeningPoint {
  readonly date: string;
  readonly accumulatedGdd: number;
  readonly kind: "observed" | "forecast";
}

export interface RipeningProjection {
  readonly cultivarId: string;
  readonly coarseGeohash: string;
  readonly asOf: string;
  readonly accumulatedGdd: number;
  readonly dailyRate: number;
  readonly stage: Stage;
  readonly stageProgress: number;
  readonly nextStage: Stage | null;
  readonly gddToNextStage: number | null;
  readonly daysObserved: number;
  readonly harvestStart: string | null;
  readonly harvestEnd: string | null;
  readonly curve: readonly RipeningPoint[];
  readonly experimental: true;
  readonly confidence: "low" | "medium";
  readonly reviewStatus: string;
  readonly reasonCodes: readonly string[];
  readonly humanReadinessReviewRequired: true;
  readonly internalRipenessInferred: false;
  readonly foodSafetyInferred: false;
}

function addDays(isoDate: string, days: number): string {
  const date = new Date(`${isoDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function dailyGdd(meanTemperatureC: number, baseTemperatureC: number): number {
  return Math.max(0, meanTemperatureC - baseTemperatureC);
}

/** Deterministic spring-to-summer curve around a seasonal mean, used for the demo series. */
export function seasonalTemperatures(
  startDate: string,
  days: number,
  options: { readonly warmthOffsetC?: number; readonly meanC?: number; readonly amplitudeC?: number } = {},
): readonly DailyTemperature[] {
  const mean = options.meanC ?? 14;
  const amplitude = options.amplitudeC ?? 6;
  const offset = options.warmthOffsetC ?? 0;
  const series: DailyTemperature[] = [];
  for (let index = 0; index < days; index += 1) {
    const position = (index * 2) / Math.max(1, days);
    const seasonal = mean + amplitude * (position - 0.5) * 2;
    const ripple = ((index % 7) - 3) * 0.2;
    series.push({
      date: addDays(startDate, index),
      meanTemperatureC: Math.round((seasonal + ripple + offset) * 1000) / 1000,
      kind: "observed",
    });
  }
  return series;
}

export function projectRipening(input: {
  readonly coarseGeohash: string;
  readonly asOf: string;
  readonly observed: readonly DailyTemperature[];
  readonly forecast?: readonly DailyTemperature[];
  readonly prior?: typeof FIXTURE_PRIOR;
}): RipeningProjection {
  const prior = input.prior ?? FIXTURE_PRIOR;
  const observed = input.observed;
  const forecast = input.forecast ?? [];
  const base = prior.baseTemperatureC;

  const curve: RipeningPoint[] = [];
  let accumulated = 0;
  for (const day of observed) {
    accumulated += dailyGdd(day.meanTemperatureC, base);
    curve.push({ date: day.date, accumulatedGdd: Math.round(accumulated * 1000) / 1000, kind: "observed" });
  }
  for (const day of forecast) {
    accumulated += dailyGdd(day.meanTemperatureC, base);
    curve.push({ date: day.date, accumulatedGdd: Math.round(accumulated * 1000) / 1000, kind: "forecast" });
  }

  const total = Math.round(accumulated * 1000) / 1000;
  let stage: Stage = "dormant";
  let stageThreshold = 0;
  let nextStage: Stage | null = null;
  let nextThreshold: number | null = null;
  for (const [candidate, threshold] of prior.stageGdd) {
    if (total >= threshold) {
      stage = candidate;
      stageThreshold = threshold;
    } else {
      nextStage = candidate;
      nextThreshold = threshold;
      break;
    }
  }
  if (nextStage === null) {
    const last = prior.stageGdd[prior.stageGdd.length - 1];
    if (last) {
      stage = last[0];
      stageThreshold = last[1];
    }
  }

  const stageProgress =
    nextThreshold === null
      ? 1
      : Math.min(1, Math.max(0, (total - stageThreshold) / Math.max(nextThreshold - stageThreshold, 1e-9)));

  const forecastValues = forecast.map((day) => dailyGdd(day.meanTemperatureC, base));
  const trailing = observed
    .slice(-14)
    .map((day) => dailyGdd(day.meanTemperatureC, base));
  const rateValues = forecastValues.length > 0 ? forecastValues : trailing;
  const dailyRate =
    rateValues.length === 0
      ? 0
      : Math.round((rateValues.reduce((sum, value) => sum + value, 0) / rateValues.length) * 10000) /
        10000;

  const reasonCodes: string[] = ["prior_not_approved", "experimental_projection"];
  if (observed.length < MIN_OBSERVED_DAYS) reasonCodes.push("insufficient_weather_days");
  if (total <= 0) reasonCodes.push("temperature_below_base");
  if (forecast.length > 0) reasonCodes.push("projection_extrapolated_beyond_forecast");

  let harvestStart: string | null = null;
  let harvestEnd: string | null = null;
  if (dailyRate > 0) {
    const ripeThreshold = prior.stageGdd.find(([candidate]) => candidate === "harvest_ripe");
    if (ripeThreshold) {
      const remaining = Math.max(0, ripeThreshold[1] - total);
      const daysToRipe = Math.ceil(remaining / dailyRate);
      harvestStart = addDays(input.asOf, daysToRipe);
      const spread = Math.max(MIN_WINDOW_SPREAD_DAYS, Math.round(daysToRipe * WINDOW_SPREAD_RATIO));
      harvestEnd = addDays(harvestStart, spread + RIPE_GRACE_DAYS);
    }
  }

  return {
    cultivarId: prior.cultivarId,
    coarseGeohash: input.coarseGeohash,
    asOf: input.asOf,
    accumulatedGdd: total,
    dailyRate,
    stage,
    stageProgress: Math.round(stageProgress * 10000) / 10000,
    nextStage,
    gddToNextStage: nextThreshold === null ? null : Math.round(Math.max(0, nextThreshold - total) * 1000) / 1000,
    daysObserved: observed.length,
    harvestStart,
    harvestEnd,
    curve,
    experimental: true,
    confidence: observed.length >= MIN_OBSERVED_DAYS && forecast.length > 0 ? "medium" : "low",
    reviewStatus: prior.reviewStatus,
    reasonCodes,
    humanReadinessReviewRequired: true,
    internalRipenessInferred: false,
    foodSafetyInferred: false,
  };
}

export interface HarvestWindow {
  readonly available: boolean;
  readonly openNow: boolean;
  readonly start: string | null;
  readonly end: string | null;
}

export function harvestWindow(projection: RipeningProjection, today: string): HarvestWindow {
  const start = projection.harvestStart;
  const end = projection.harvestEnd;
  if (start === null || end === null) {
    return { available: false, openNow: false, start: null, end: null };
  }
  return { available: true, openNow: start <= today && today <= end, start, end };
}
