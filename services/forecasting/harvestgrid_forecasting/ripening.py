"""Temperature-driven ripening curves for coarse-location fruit forecasting.

The curve is a *phenology model*, not an image model. It converts accumulated
growing-degree-days (GDD) from coarse Open-Meteo weather into a phenological stage and a
projected harvest window, and it recomputes both whenever new weather arrives. That is the
"dynamic adjustment" half of ``INT-HG-005``: no ripeness is ever inferred from RGB pixels,
and every projection keeps ``experimental``/human-review state until a reviewed cultivar
prior exists.

Honesty boundaries enforced by construction:

* A prior that is not ``approved`` can never produce a confident, non-experimental result.
* Missing weather days are counted and widen the window; they are never filled with zeros.
* GDD is monotonic in time, so a stage can advance but never silently regress.
* Only a coarse geohash is accepted as input; no exact coordinate or address can reach this
  module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Literal

PHENOLOGY_ERROR_CODES: tuple[str, ...] = (
    "prior_invalid",
    "prior_stage_thresholds_unordered",
    "prior_base_temperature_invalid",
    "weather_series_empty",
    "weather_timestamp_after_as_of",
    "weather_temperature_invalid",
    "coarse_location_invalid",
    "projection_input_invalid",
)

StageReviewStatus = Literal["approved", "draft", "fixture_unreviewed"]

MIN_OBSERVED_DAYS = 14
TRAILING_RATE_DAYS = 14
WINDOW_SPREAD_RATIO = 0.2
MIN_WINDOW_SPREAD_DAYS = 2
BEYOND_FORECAST_PENALTY_DAYS = 3
RIPE_GRACE_DAYS = 14
GEOHASH_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"
MIN_COARSE_GEOHASH = 4
MAX_COARSE_GEOHASH = 5


class PhenologyError(ValueError):
    """Invalid phenology input; ``code`` is stable for callers and tests."""

    def __init__(self, code: str, detail: str = "") -> None:
        if code not in PHENOLOGY_ERROR_CODES:
            raise AssertionError(f"unknown phenology error code: {code}")
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class RipeningStage(StrEnum):
    DORMANT = "dormant"
    BUD_BREAK = "bud_break"
    BLOOM = "bloom"
    FRUIT_SET = "fruit_set"
    PIT_HARDENING = "pit_hardening"
    VERAISON = "veraison"
    HARVEST_RIPE = "harvest_ripe"
    PAST_RIPE = "past_ripe"


#: Ordered so index arithmetic on stage thresholds is meaningful.
STAGE_ORDER: tuple[RipeningStage, ...] = (
    RipeningStage.DORMANT,
    RipeningStage.BUD_BREAK,
    RipeningStage.BLOOM,
    RipeningStage.FRUIT_SET,
    RipeningStage.PIT_HARDENING,
    RipeningStage.VERAISON,
    RipeningStage.HARVEST_RIPE,
    RipeningStage.PAST_RIPE,
)


@dataclass(frozen=True)
class PhenologyPrior:
    """Cultivar growing-degree-day thresholds.

    ``review_status`` is carried through to every projection. Only ``approved`` priors may
    produce an operational (non-experimental) projection, and even then human readiness
    review stays mandatory.
    """

    cultivar_id: str
    content_version: str
    base_temperature_c: float
    stage_gdd: tuple[tuple[RipeningStage, float], ...]
    review_status: StageReviewStatus = "draft"
    source: str = "unreviewed"

    def __post_init__(self) -> None:
        if not self.cultivar_id.strip() or not self.content_version.strip():
            raise PhenologyError("prior_invalid", "cultivar_id")
        base = self.base_temperature_c
        if not math.isfinite(base) or not -5.0 <= base <= 20.0:
            raise PhenologyError("prior_base_temperature_invalid", str(self.base_temperature_c))
        if len(self.stage_gdd) < 3:
            raise PhenologyError("prior_invalid", "stage_thresholds")
        last = -math.inf
        previous_stage: RipeningStage | None = None
        for stage, threshold in self.stage_gdd:
            if stage not in STAGE_ORDER:
                raise PhenologyError("prior_invalid", f"unknown_stage={stage}")
            if previous_stage is not None and STAGE_ORDER.index(stage) <= STAGE_ORDER.index(
                previous_stage
            ):
                raise PhenologyError("prior_stage_thresholds_unordered", stage.value)
            if not math.isfinite(threshold) or threshold <= last:
                raise PhenologyError("prior_stage_thresholds_unordered", stage.value)
            last = threshold
            previous_stage = stage
        if self.review_status not in {"approved", "draft", "fixture_unreviewed"}:
            raise PhenologyError("prior_invalid", "review_status")

    def threshold(self, stage: RipeningStage) -> float:
        for candidate, threshold in self.stage_gdd:
            if candidate is stage:
                return threshold
        raise PhenologyError("prior_invalid", f"missing_stage={stage}")

    def last_below(self, accumulated_gdd: float) -> tuple[RipeningStage, float]:
        """Highest stage whose threshold is reached, with its threshold value."""

        current = self.stage_gdd[0][0]
        current_threshold = self.stage_gdd[0][1]
        for stage, threshold in self.stage_gdd:
            if accumulated_gdd + 1e-9 >= threshold:
                current, current_threshold = stage, threshold
        return current, current_threshold

    def next_after(self, stage: RipeningStage) -> tuple[RipeningStage, float] | None:
        for index, (candidate, _threshold) in enumerate(self.stage_gdd):
            if candidate is stage:
                if index + 1 >= len(self.stage_gdd):
                    return None
                return self.stage_gdd[index + 1]
        return None

    @property
    def approved(self) -> bool:
        return self.review_status == "approved"

    def as_dict(self) -> dict[str, Any]:
        return {
            "cultivar_id": self.cultivar_id,
            "content_version": self.content_version,
            "base_temperature_c": self.base_temperature_c,
            "review_status": self.review_status,
            "source": self.source,
            "stage_gdd": {stage.value: threshold for stage, threshold in self.stage_gdd},
        }


@dataclass(frozen=True)
class DailyTemperature:
    day: date
    temperature_min_c: float | None
    temperature_max_c: float | None

    def __post_init__(self) -> None:
        for value in (self.temperature_min_c, self.temperature_max_c):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PhenologyError("weather_temperature_invalid", "non_numeric")
            if not math.isfinite(float(value)) or not -80.0 <= float(value) <= 60.0:
                raise PhenologyError("weather_temperature_invalid", str(value))

    @property
    def mean_c(self) -> float | None:
        if self.temperature_min_c is None or self.temperature_max_c is None:
            return None
        return (self.temperature_min_c + self.temperature_max_c) / 2

    def growing_degree_days(self, base_temperature_c: float) -> float | None:
        mean = self.mean_c
        if mean is None:
            return None
        return max(0.0, mean - base_temperature_c)


def validate_coarse_geohash(value: str) -> str:
    cell = value.strip().lower()
    if not cell or any(character not in GEOHASH_ALPHABET for character in cell):
        raise PhenologyError("coarse_location_invalid", "geohash")
    if not MIN_COARSE_GEOHASH <= len(cell) <= MAX_COARSE_GEOHASH:
        raise PhenologyError("coarse_location_invalid", f"precision={len(cell)}")
    return cell


def _daily_gdd_series(
    *, daily: tuple[DailyTemperature, ...], base: float, as_of: date, future: bool
) -> list[tuple[date, float | None]]:
    ordered = sorted(daily, key=lambda item: item.day)
    seen: dict[date, float | None] = {}
    for item in ordered:
        if future and item.day <= as_of:
            raise PhenologyError("weather_timestamp_after_as_of", f"future_day={item.day}")
        if not future and item.day > as_of:
            raise PhenologyError("weather_timestamp_after_as_of", f"observed_day={item.day}")
        seen[item.day] = item.growing_degree_days(base)
    return [(day, seen[day]) for day in sorted(seen)]


@dataclass(frozen=True)
class RipeningProjection:
    """Auditable phenology snapshot; never asserts internal ripeness or food safety."""

    cultivar_id: str
    content_version: str
    prior_review_status: StageReviewStatus
    coarse_geohash: str
    as_of: date
    accumulated_gdd: float
    stage: RipeningStage
    stage_progress: float
    next_stage: RipeningStage | None
    gdd_to_next_stage: float | None
    daily_gdd_rate: float | None
    days_observed: int
    missing_days: int
    projected_harvest_start: date | None
    projected_harvest_end: date | None
    confidence: Literal["low", "medium"]
    experimental: bool
    reason_codes: tuple[str, ...]
    human_readiness_review_required: bool = True
    internal_ripeness_inferred: bool = False
    food_safety_inferred: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "cultivar_id": self.cultivar_id,
            "content_version": self.content_version,
            "prior_review_status": self.prior_review_status,
            "coarse_geohash": self.coarse_geohash,
            "as_of": self.as_of.isoformat(),
            "accumulated_gdd": self.accumulated_gdd,
            "stage": self.stage.value,
            "stage_progress": self.stage_progress,
            "next_stage": self.next_stage.value if self.next_stage else None,
            "gdd_to_next_stage": self.gdd_to_next_stage,
            "daily_gdd_rate": self.daily_gdd_rate,
            "days_observed": self.days_observed,
            "missing_days": self.missing_days,
            "projected_harvest_start": (
                self.projected_harvest_start.isoformat() if self.projected_harvest_start else None
            ),
            "projected_harvest_end": (
                self.projected_harvest_end.isoformat() if self.projected_harvest_end else None
            ),
            "confidence": self.confidence,
            "experimental": self.experimental,
            "reason_codes": list(self.reason_codes),
            "human_readiness_review_required": self.human_readiness_review_required,
            "internal_ripeness_inferred": self.internal_ripeness_inferred,
            "food_safety_inferred": self.food_safety_inferred,
        }


def project_ripening(
    *,
    prior: PhenologyPrior,
    coarse_geohash: str,
    as_of: date,
    observed: tuple[DailyTemperature, ...],
    forecast: tuple[DailyTemperature, ...] = (),
) -> RipeningProjection:
    """Recompute stage and harvest window from the latest observed + forecast weather."""

    cell = validate_coarse_geohash(coarse_geohash)
    if not observed:
        raise PhenologyError("weather_series_empty", "observed")
    if not isinstance(as_of, date):
        raise PhenologyError("projection_input_invalid", "as_of")

    observed_series = _daily_gdd_series(
        daily=observed, base=prior.base_temperature_c, as_of=as_of, future=False
    )
    forecast_series = (
        _daily_gdd_series(daily=forecast, base=prior.base_temperature_c, as_of=as_of, future=True)
        if forecast
        else []
    )

    accumulated = round(sum(value for _, value in observed_series if value is not None), 3)
    missing_days = sum(1 for _, value in observed_series if value is None)
    days_observed = len(observed_series) - missing_days

    stage, stage_threshold = prior.last_below(accumulated)
    upcoming = prior.next_after(stage)
    if upcoming is None:
        stage_progress = 1.0
        next_stage = None
        gdd_to_next: float | None = None
    else:
        next_stage, next_threshold = upcoming
        span = max(next_threshold - stage_threshold, 1e-9)
        stage_progress = round(min(1.0, max(0.0, (accumulated - stage_threshold) / span)), 4)
        gdd_to_next = round(max(0.0, next_threshold - accumulated), 3)

    reason_codes: set[str] = set()
    if not prior.approved:
        reason_codes.add("prior_not_approved")
    if days_observed < MIN_OBSERVED_DAYS:
        reason_codes.add("insufficient_weather_days")
    if missing_days:
        reason_codes.add("weather_gaps_present")
    if accumulated <= 0.0:
        reason_codes.add("temperature_below_base")

    forecast_values = [value for _, value in forecast_series if value is not None]
    trailing = [value for _, value in observed_series[-TRAILING_RATE_DAYS:] if value is not None]
    if forecast_values:
        rate: float | None = round(sum(forecast_values) / len(forecast_values), 4)
        rate_source = "forecast"
    elif trailing:
        rate = round(sum(trailing) / len(trailing), 4)
        rate_source = "trailing_observed"
        if forecast_series:
            reason_codes.add("forecast_has_no_temperatures")
    else:
        rate = None
        rate_source = "unavailable"
    if rate == 0.0:
        reason_codes.add("no_gdd_accumulation_in_window")

    ripe_threshold = prior.threshold(RipeningStage.HARVEST_RIPE)
    remaining = round(ripe_threshold - accumulated, 3)
    harvest_start: date | None = None
    harvest_end: date | None = None

    if remaining <= 0.0:
        reason_codes.add("harvest_window_reached")
        harvest_start = as_of
        harvest_end = as_of.fromordinal(as_of.toordinal() + RIPE_GRACE_DAYS)
    elif rate is not None and rate > 0.0:
        days_to_ripe = math.ceil(remaining / rate)
        spread = max(MIN_WINDOW_SPREAD_DAYS, math.ceil(WINDOW_SPREAD_RATIO * days_to_ripe))
        covered_days = len(forecast_values)
        if days_to_ripe > covered_days > 0:
            spread += BEYOND_FORECAST_PENALTY_DAYS
            reason_codes.add("projection_extrapolated_beyond_forecast")
        if missing_days:
            spread += 1
        centre = as_of.toordinal() + days_to_ripe
        harvest_start = as_of.fromordinal(max(as_of.toordinal(), centre - spread))
        harvest_end = as_of.fromordinal(centre + spread)
    else:
        reason_codes.add("harvest_window_unavailable")

    if rate is None:
        reason_codes.add("gdd_rate_unavailable")
    if harvest_start is not None and harvest_start <= as_of:
        reason_codes.add("harvest_window_open_now")

    experimental = (
        not prior.approved or days_observed < MIN_OBSERVED_DAYS or harvested_unknown(harvest_start)
    )
    confidence: Literal["low", "medium"] = (
        "low" if experimental or rate_source == "unavailable" else "medium"
    )
    if experimental and "insufficient_weather_days" not in reason_codes:
        reason_codes.add("experimental_projection")

    return RipeningProjection(
        cultivar_id=prior.cultivar_id,
        content_version=prior.content_version,
        prior_review_status=prior.review_status,
        coarse_geohash=cell,
        as_of=as_of,
        accumulated_gdd=accumulated,
        stage=stage,
        stage_progress=stage_progress,
        next_stage=next_stage,
        gdd_to_next_stage=gdd_to_next,
        daily_gdd_rate=rate,
        days_observed=days_observed,
        missing_days=missing_days,
        projected_harvest_start=harvest_start,
        projected_harvest_end=harvest_end,
        confidence=confidence,
        experimental=experimental,
        reason_codes=tuple(sorted(reason_codes)),
    )


def harvested_unknown(value: date | None) -> bool:
    """A projection without a harvest window cannot support an operational claim."""

    return value is None


@dataclass(frozen=True)
class RipeningAdjustment:
    """Difference between two projections of the same tree, for audit and notifications."""

    tree_id: str
    cultivar_id: str
    previous_as_of: date
    current_as_of: date
    previous_stage: RipeningStage
    current_stage: RipeningStage
    accumulated_gdd_delta: float
    harvest_start_shift_days: int | None
    harvest_end_shift_days: int | None
    direction: Literal["earlier", "later", "unchanged", "unavailable"]
    material: bool
    experimental: bool
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "tree_id": self.tree_id,
            "cultivar_id": self.cultivar_id,
            "previous_as_of": self.previous_as_of.isoformat(),
            "current_as_of": self.current_as_of.isoformat(),
            "previous_stage": self.previous_stage.value,
            "current_stage": self.current_stage.value,
            "accumulated_gdd_delta": self.accumulated_gdd_delta,
            "harvest_start_shift_days": self.harvest_start_shift_days,
            "harvest_end_shift_days": self.harvest_end_shift_days,
            "direction": self.direction,
            "material": self.material,
            "experimental": self.experimental,
            "reason_codes": list(self.reason_codes),
        }


def adjust_projection(
    *, tree_id: str, previous: RipeningProjection, current: RipeningProjection
) -> RipeningAdjustment:
    """Compare two projections. A warmer forecast moves the window earlier, never silently."""

    if not tree_id.strip():
        raise PhenologyError("projection_input_invalid", "tree_id")
    if previous.cultivar_id != current.cultivar_id:
        raise PhenologyError("projection_input_invalid", "cultivar_mismatch")
    if current.as_of < previous.as_of:
        raise PhenologyError("projection_input_invalid", "as_of_regressed")

    def shift(before: date | None, after: date | None) -> int | None:
        if before is None or after is None:
            return None
        return (after - before).days

    start_shift = shift(previous.projected_harvest_start, current.projected_harvest_start)
    end_shift = shift(previous.projected_harvest_end, current.projected_harvest_end)
    reasons: set[str] = set()
    if current.accumulated_gdd > previous.accumulated_gdd:
        reasons.add("gdd_recalculated")
    if current.stage is not previous.stage:
        reasons.add("stage_advanced")
        if STAGE_ORDER.index(current.stage) < STAGE_ORDER.index(previous.stage):
            reasons.add("stage_regressed")
    if start_shift is None or end_shift is None:
        direction: Literal["earlier", "later", "unchanged", "unavailable"] = "unavailable"
        reasons.add("harvest_window_incomplete")
    elif start_shift < 0 or end_shift < 0:
        direction = "earlier"
        reasons.add("harvest_window_earlier")
    elif start_shift > 0 or end_shift > 0:
        direction = "later"
        reasons.add("harvest_window_later")
    else:
        direction = "unchanged"

    material = (
        current.stage is not previous.stage
        or direction in {"earlier", "later"}
        and (abs(start_shift or 0) >= 1 or abs(end_shift or 0) >= 1)
    )
    if current.as_of > previous.as_of:
        reasons.add("as_of_advanced")
    if not material:
        reasons.add("no_material_change")
    return RipeningAdjustment(
        tree_id=tree_id,
        cultivar_id=current.cultivar_id,
        previous_as_of=previous.as_of,
        current_as_of=current.as_of,
        previous_stage=previous.stage,
        current_stage=current.stage,
        accumulated_gdd_delta=round(current.accumulated_gdd - previous.accumulated_gdd, 3),
        harvest_start_shift_days=start_shift,
        harvest_end_shift_days=end_shift,
        direction=direction,
        material=material,
        experimental=current.experimental or previous.experimental,
        reason_codes=tuple(sorted(reasons)),
    )


def projection_fingerprint(projection: RipeningProjection) -> str:
    """Stable digest used to deduplicate scheduler writes (``NFR-HG-004``)."""

    import hashlib

    payload = repr(sorted(projection.as_dict().items())).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
