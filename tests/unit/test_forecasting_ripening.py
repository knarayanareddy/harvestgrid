from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from harvestgrid_forecasting import (
    DailyTemperature,
    PhenologyError,
    PhenologyPrior,
    RipeningStage,
    adjust_projection,
    project_ripening,
    projection_fingerprint,
    validate_coarse_geohash,
)

START = date(2026, 6, 1)
GEOHASH = "u173z"
STAGES: tuple[tuple[RipeningStage, float], ...] = (
    (RipeningStage.DORMANT, 0.0),
    (RipeningStage.BUD_BREAK, 50.0),
    (RipeningStage.BLOOM, 120.0),
    (RipeningStage.FRUIT_SET, 200.0),
    (RipeningStage.PIT_HARDENING, 400.0),
    (RipeningStage.VERAISON, 700.0),
    (RipeningStage.HARVEST_RIPE, 1000.0),
    (RipeningStage.PAST_RIPE, 1300.0),
)


def prior(**overrides: object) -> PhenologyPrior:
    values: dict[str, object] = {
        "cultivar_id": "european_plum",
        "content_version": "european_plum_v1",
        "base_temperature_c": 5.0,
        "stage_gdd": STAGES,
        "review_status": "approved",
        "source": "fixture_prior_for_tests",
    }
    values.update(overrides)
    return PhenologyPrior(**values)  # type: ignore[arg-type]


def series(days: int, *, mean: float, start: date = START) -> tuple[DailyTemperature, ...]:
    return tuple(
        DailyTemperature(
            day=start + timedelta(days=offset),
            temperature_min_c=mean - 5.0,
            temperature_max_c=mean + 5.0,
        )
        for offset in range(days)
    )


def test_stage_advances_with_accumulated_gdd_and_opens_the_window() -> None:
    observed = series(100, mean=15.0)  # 10 GDD per day -> exactly 1000
    projection = project_ripening(
        prior=prior(),
        coarse_geohash=GEOHASH,
        as_of=observed[-1].day,
        observed=observed,
    )
    assert projection.accumulated_gdd == pytest.approx(1000.0)
    assert projection.days_observed == 100
    assert projection.missing_days == 0
    assert projection.stage is RipeningStage.HARVEST_RIPE
    assert projection.projected_harvest_start == observed[-1].day
    assert projection.projected_harvest_end == observed[-1].day + timedelta(days=14)
    assert "harvest_window_reached" in projection.reason_codes
    assert projection.experimental is False
    assert projection.confidence == "medium"
    assert projection.human_readiness_review_required is True
    assert projection.internal_ripeness_inferred is False
    assert projection.food_safety_inferred is False


def test_stage_progress_and_next_stage_are_reported() -> None:
    observed = series(30, mean=15.0)  # 300 GDD -> fruit_set (200) toward pit hardening (400)
    projection = project_ripening(
        prior=prior(), coarse_geohash=GEOHASH, as_of=observed[-1].day, observed=observed
    )
    assert projection.stage is RipeningStage.FRUIT_SET
    assert projection.next_stage is RipeningStage.PIT_HARDENING
    assert projection.stage_progress == pytest.approx(0.5)
    assert projection.gdd_to_next_stage == pytest.approx(100.0)


def test_forecast_rate_sets_the_projected_window() -> None:
    observed = series(60, mean=13.0, start=date(2026, 5, 1))  # 8 GDD/day -> 480
    as_of = observed[-1].day
    forecast = series(10, mean=17.0, start=as_of + timedelta(days=1))  # 12 GDD/day
    projection = project_ripening(
        prior=prior(),
        coarse_geohash=GEOHASH,
        as_of=as_of,
        observed=observed,
        forecast=forecast,
    )
    assert projection.accumulated_gdd == pytest.approx(480.0)
    assert projection.daily_gdd_rate == pytest.approx(12.0)
    remaining = 1000.0 - 480.0
    days_to_ripe = 44  # ceil(520 / 12)
    assert projection.projected_harvest_start == as_of + timedelta(days=days_to_ripe - 12)
    assert projection.projected_harvest_end == as_of + timedelta(days=days_to_ripe + 12)
    assert "projection_extrapolated_beyond_forecast" in projection.reason_codes
    assert remaining == pytest.approx(520.0)


def test_trailing_observed_rate_is_used_without_a_forecast() -> None:
    observed = series(20, mean=15.0)
    projection = project_ripening(
        prior=prior(), coarse_geohash=GEOHASH, as_of=observed[-1].day, observed=observed
    )
    assert projection.daily_gdd_rate == pytest.approx(10.0)
    assert projection.projected_harvest_start is not None


def test_missing_temperatures_are_counted_and_never_invented() -> None:
    observed = list(series(20, mean=15.0))
    observed[3] = DailyTemperature(
        day=observed[3].day, temperature_min_c=None, temperature_max_c=None
    )
    projection = project_ripening(
        prior=prior(),
        coarse_geohash=GEOHASH,
        as_of=observed[-1].day,
        observed=tuple(observed),
    )
    assert projection.missing_days == 1
    assert projection.days_observed == 19
    assert projection.accumulated_gdd == pytest.approx(190.0)
    assert "weather_gaps_present" in projection.reason_codes


def test_unreviewed_prior_can_never_look_operational() -> None:
    observed = series(60, mean=15.0)
    projection = project_ripening(
        prior=prior(review_status="fixture_unreviewed", source="draft"),
        coarse_geohash=GEOHASH,
        as_of=observed[-1].day,
        observed=observed,
    )
    assert projection.experimental is True
    assert projection.confidence == "low"
    assert "prior_not_approved" in projection.reason_codes
    assert projection.human_readiness_review_required is True


def test_short_weather_history_is_experimental() -> None:
    observed = series(5, mean=18.0)
    projection = project_ripening(
        prior=prior(), coarse_geohash=GEOHASH, as_of=observed[-1].day, observed=observed
    )
    assert projection.experimental is True
    assert "insufficient_weather_days" in projection.reason_codes
    assert projection.confidence == "low"


def test_cold_weather_reports_zero_gdd_and_no_window() -> None:
    observed = tuple(
        DailyTemperature(
            day=START + timedelta(days=offset), temperature_min_c=0.0, temperature_max_c=2.0
        )
        for offset in range(20)
    )
    projection = project_ripening(
        prior=prior(), coarse_geohash=GEOHASH, as_of=observed[-1].day, observed=observed
    )
    assert projection.accumulated_gdd == 0.0
    assert "temperature_below_base" in projection.reason_codes
    assert projection.stage is RipeningStage.DORMANT
    assert projection.daily_gdd_rate == 0.0
    assert projection.projected_harvest_start is None
    assert "harvest_window_unavailable" in projection.reason_codes
    assert projection.experimental is True


def test_projection_is_deterministic_and_fingerprinted() -> None:
    observed = series(30, mean=15.0)
    kwargs = {"prior": prior(), "coarse_geohash": GEOHASH, "as_of": observed[-1].day}
    first = project_ripening(observed=observed, **kwargs)
    second = project_ripening(observed=observed, **kwargs)
    assert first == second
    assert projection_fingerprint(first) == projection_fingerprint(second)
    warmer = project_ripening(
        observed=observed,
        forecast=series(5, mean=22.0, start=observed[-1].day + timedelta(days=1)),
        **kwargs,
    )
    assert projection_fingerprint(warmer) != projection_fingerprint(first)


def test_input_order_does_not_change_the_projection() -> None:
    observed = series(30, mean=15.0)
    shuffled = tuple(reversed(observed))
    first = project_ripening(
        prior=prior(), coarse_geohash=GEOHASH, as_of=observed[-1].day, observed=observed
    )
    second = project_ripening(
        prior=prior(), coarse_geohash=GEOHASH, as_of=observed[-1].day, observed=shuffled
    )
    assert first == second


def test_duplicate_days_are_collapsed_to_the_last_value() -> None:
    observed = series(20, mean=15.0)
    overridden = observed[:-1] + (
        DailyTemperature(day=observed[-1].day, temperature_min_c=25.0, temperature_max_c=35.0),
    )
    projection = project_ripening(
        prior=prior(),
        coarse_geohash=GEOHASH,
        as_of=observed[-1].day,
        observed=overridden,
    )
    assert projection.days_observed == 20
    assert projection.accumulated_gdd == pytest.approx(190.0 + 25.0)


def test_warmer_forecast_moves_the_window_earlier() -> None:
    observed = series(60, mean=13.0, start=date(2026, 5, 1))
    as_of = observed[-1].day
    previous = project_ripening(
        prior=prior(),
        coarse_geohash=GEOHASH,
        as_of=as_of,
        observed=observed,
        forecast=series(10, mean=15.0, start=as_of + timedelta(days=1)),
    )
    current = project_ripening(
        prior=prior(),
        coarse_geohash=GEOHASH,
        as_of=as_of,
        observed=observed,
        forecast=series(10, mean=19.0, start=as_of + timedelta(days=1)),
    )
    adjustment = adjust_projection(tree_id="tree-1", previous=previous, current=current)
    assert adjustment.direction == "earlier"
    assert adjustment.material is True
    assert adjustment.harvest_start_shift_days is not None
    assert adjustment.harvest_start_shift_days < 0
    assert "harvest_window_earlier" in adjustment.reason_codes
    # Accumulated GDD is unchanged (same observed series), so only the window moved.
    assert adjustment.accumulated_gdd_delta == 0.0
    assert "gdd_recalculated" not in adjustment.reason_codes
    assert adjustment.as_dict()["tree_id"] == "tree-1"


def test_cooler_forecast_moves_the_window_later() -> None:
    observed = series(60, mean=13.0, start=date(2026, 5, 1))
    as_of = observed[-1].day
    previous = project_ripening(
        prior=prior(),
        coarse_geohash=GEOHASH,
        as_of=as_of,
        observed=observed,
        forecast=series(10, mean=19.0, start=as_of + timedelta(days=1)),
    )
    current = project_ripening(
        prior=prior(),
        coarse_geohash=GEOHASH,
        as_of=as_of,
        observed=observed,
        forecast=series(10, mean=15.0, start=as_of + timedelta(days=1)),
    )
    adjustment = adjust_projection(tree_id="tree-1", previous=previous, current=current)
    assert adjustment.direction == "later"
    assert (adjustment.harvest_start_shift_days or 0) > 0
    assert "harvest_window_later" in adjustment.reason_codes


def test_identical_projections_are_not_material() -> None:
    observed = series(40, mean=15.0)
    projection = project_ripening(
        prior=prior(), coarse_geohash=GEOHASH, as_of=observed[-1].day, observed=observed
    )
    adjustment = adjust_projection(tree_id="tree-1", previous=projection, current=projection)
    assert adjustment.material is False
    assert adjustment.direction == "unchanged"
    assert "no_material_change" in adjustment.reason_codes


def test_stage_advance_is_reported_even_without_a_window_shift() -> None:
    as_of = date(2026, 5, 1) + timedelta(days=69)
    previous = project_ripening(
        prior=prior(),
        coarse_geohash=GEOHASH,
        as_of=as_of,
        observed=series(69, mean=15.0, start=date(2026, 5, 1)),
    )
    current = project_ripening(
        prior=prior(),
        coarse_geohash=GEOHASH,
        as_of=as_of,
        observed=series(70, mean=15.0, start=date(2026, 5, 1)),
    )
    adjustment = adjust_projection(tree_id="tree-1", previous=previous, current=current)
    assert adjustment.previous_stage is RipeningStage.PIT_HARDENING
    assert current.stage is RipeningStage.VERAISON
    assert "stage_advanced" in adjustment.reason_codes
    assert "gdd_recalculated" in adjustment.reason_codes
    assert adjustment.material is True


def test_adjustment_rejects_cultivar_mismatch_and_time_travel() -> None:
    observed = series(30, mean=15.0)
    projection = project_ripening(
        prior=prior(), coarse_geohash=GEOHASH, as_of=observed[-1].day, observed=observed
    )
    other = project_ripening(
        prior=prior(cultivar_id="other_plum"),
        coarse_geohash=GEOHASH,
        as_of=observed[-1].day,
        observed=observed,
    )
    with pytest.raises(PhenologyError, match="cultivar_mismatch"):
        adjust_projection(tree_id="tree-1", previous=projection, current=other)
    older = project_ripening(
        prior=prior(),
        coarse_geohash=GEOHASH,
        as_of=observed[-1].day - timedelta(days=1),
        observed=observed[:-1],
    )
    with pytest.raises(PhenologyError, match="as_of_regressed"):
        adjust_projection(tree_id="tree-1", previous=projection, current=older)
    with pytest.raises(PhenologyError, match="projection_input_invalid"):
        adjust_projection(tree_id=" ", previous=projection, current=projection)


@pytest.mark.parametrize(
    "value, expected",
    [("u173", "u173"), ("u173z", "u173z"), ("U173Z", "u173z"), (" u173 ", "u173")],
)
def test_coarse_geohash_accepts_only_precision_four_or_five(value: str, expected: str) -> None:
    assert validate_coarse_geohash(value) == expected


@pytest.mark.parametrize("value", ["u17", "u173zx", "u173!", "a", "", "  "])
def test_coarse_geohash_rejects_other_inputs(value: str) -> None:
    with pytest.raises(PhenologyError) as error:
        validate_coarse_geohash(value)
    assert error.value.code == "coarse_location_invalid"


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"cultivar_id": ""}, "prior_invalid"),
        ({"content_version": ""}, "prior_invalid"),
        ({"base_temperature_c": 40.0}, "prior_base_temperature_invalid"),
        ({"base_temperature_c": float("nan")}, "prior_base_temperature_invalid"),
        ({"review_status": "trusted"}, "prior_invalid"),
        (
            {"stage_gdd": ((RipeningStage.DORMANT, 0.0), (RipeningStage.BLOOM, 10.0))},
            "prior_invalid",
        ),
        (
            {
                "stage_gdd": (
                    (RipeningStage.DORMANT, 0.0),
                    (RipeningStage.BLOOM, 100.0),
                    (RipeningStage.BUD_BREAK, 50.0),
                )
            },
            "prior_stage_thresholds_unordered",
        ),
        (
            {
                "stage_gdd": (
                    (RipeningStage.DORMANT, 0.0),
                    (RipeningStage.BLOOM, 100.0),
                    (RipeningStage.FRUIT_SET, 100.0),
                )
            },
            "prior_stage_thresholds_unordered",
        ),
    ],
)
def test_prior_validation(overrides: dict[str, object], code: str) -> None:
    with pytest.raises(PhenologyError) as error:
        prior(**overrides)
    assert error.value.code == code


def test_prior_helpers_cover_thresholds_and_lookups() -> None:
    value = prior()
    assert value.approved is True
    assert value.threshold(RipeningStage.BLOOM) == 120.0
    assert value.last_below(430.0) == (RipeningStage.PIT_HARDENING, 400.0)
    assert value.next_after(RipeningStage.PIT_HARDENING) == (RipeningStage.VERAISON, 700.0)
    assert value.next_after(RipeningStage.PAST_RIPE) is None
    assert value.next_after(RipeningStage.DORMANT) == (RipeningStage.BUD_BREAK, 50.0)
    assert value.as_dict()["stage_gdd"]["bloom"] == 120.0
    without_past_ripe = prior(
        stage_gdd=STAGES[:-1],
    )
    with pytest.raises(PhenologyError, match="missing_stage"):
        without_past_ripe.threshold(RipeningStage.PAST_RIPE)


@pytest.mark.parametrize(
    "value",
    [
        {"temperature_min_c": "warm", "temperature_max_c": 20.0},
        {"temperature_min_c": 10.0, "temperature_max_c": 200.0},
        {"temperature_min_c": float("inf"), "temperature_max_c": 20.0},
    ],
)
def test_daily_temperature_validation(value: dict[str, object]) -> None:
    with pytest.raises(PhenologyError) as error:
        DailyTemperature(day=START, **value)  # type: ignore[arg-type]
    assert error.value.code == "weather_temperature_invalid"


def test_daily_temperature_helpers() -> None:
    day = DailyTemperature(day=START, temperature_min_c=4.0, temperature_max_c=14.0)
    assert day.mean_c == pytest.approx(9.0)
    assert day.growing_degree_days(5.0) == pytest.approx(4.0)
    missing = DailyTemperature(day=START, temperature_min_c=None, temperature_max_c=None)
    assert missing.mean_c is None
    assert missing.growing_degree_days(5.0) is None


def test_projection_rejects_empty_series_and_future_observed_days() -> None:
    with pytest.raises(PhenologyError, match="weather_series_empty"):
        project_ripening(prior=prior(), coarse_geohash=GEOHASH, as_of=START, observed=())
    observed = series(5, mean=15.0)
    with pytest.raises(PhenologyError, match="weather_timestamp_after_as_of"):
        project_ripening(
            prior=prior(),
            coarse_geohash=GEOHASH,
            as_of=observed[0].day,
            observed=observed,
        )
    with pytest.raises(PhenologyError, match="weather_timestamp_after_as_of"):
        project_ripening(
            prior=prior(),
            coarse_geohash=GEOHASH,
            as_of=observed[-1].day,
            observed=observed,
            forecast=observed,
        )


def test_projection_never_claims_ripeness_from_imagery() -> None:
    observed = series(30, mean=15.0)
    document = project_ripening(
        prior=prior(), coarse_geohash=GEOHASH, as_of=observed[-1].day, observed=observed
    ).as_dict()
    assert document["internal_ripeness_inferred"] is False
    assert document["food_safety_inferred"] is False
    assert document["human_readiness_review_required"] is True
    assert "address" not in str(document).casefold()
    assert len(document["reason_codes"]) == len(set(document["reason_codes"]))


def test_projection_as_of_is_preserved_and_replaceable() -> None:
    observed = series(30, mean=15.0)
    projection = project_ripening(
        prior=prior(), coarse_geohash=GEOHASH, as_of=observed[-1].day, observed=observed
    )
    assert projection.as_of == observed[-1].day
    assert replace(projection, confidence="low").confidence == "low"


def test_timestamp_helper_is_timezone_aware() -> None:
    assert datetime(2026, 6, 1, tzinfo=UTC).utcoffset() is not None
