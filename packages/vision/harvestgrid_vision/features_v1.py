"""``features_v1`` column order and missing-value contract (SPEC §8.1).

Model columns must never reorder silently: training and inference both import this
module. Unknown values use an explicit sentinel instead of being coerced to a plausible
zero, and negative values are impossible for the real quantities (counts, fractions), so
the sentinel cannot collide with a measurement.
"""

from __future__ import annotations

FEATURE_VERSION = "features_v1"
MISSING_VALUE = -1.0
SCHEMA_VERSION = 1

FEATURES_V1_COLUMNS: tuple[str, ...] = (
    "visible_fruit_count",
    "count_lower",
    "count_upper",
    "detection_confidence_mean",
    "detection_area_p10",
    "detection_area_p50",
    "detection_area_p90",
    "area_change_ratio",
    "colour_saturation_mean",
    "colour_value_mean",
    "occlusion_fraction",
    "quality_score",
    "roi_coverage",
    "registration_score",
    "day_of_year",
    "observation_hour_utc",
)

# Units are documented so a downstream consumer cannot reinterpret a column.
COLUMN_UNITS: dict[str, str] = {
    "visible_fruit_count": "clusters",
    "count_lower": "clusters",
    "count_upper": "clusters",
    "detection_confidence_mean": "ratio_0_1",
    "detection_area_p10": "fraction_of_roi",
    "detection_area_p50": "fraction_of_roi",
    "detection_area_p90": "fraction_of_roi",
    "area_change_ratio": "ratio",
    "colour_saturation_mean": "ratio_0_1",
    "colour_value_mean": "ratio_0_1",
    "occlusion_fraction": "ratio_0_1",
    "quality_score": "ratio_0_1",
    "roi_coverage": "ratio_0_1",
    "registration_score": "ratio_0_1",
    "day_of_year": "days",
    "observation_hour_utc": "hours",
}

NON_NEGATIVE_COLUMNS = frozenset(
    {
        "visible_fruit_count",
        "count_lower",
        "count_upper",
        "detection_confidence_mean",
        "detection_area_p10",
        "detection_area_p50",
        "detection_area_p90",
        "colour_saturation_mean",
        "colour_value_mean",
        "occlusion_fraction",
        "quality_score",
        "roi_coverage",
        "registration_score",
        "day_of_year",
        "observation_hour_utc",
    }
)

RATIO_COLUMNS = frozenset(
    {
        "detection_confidence_mean",
        "occlusion_fraction",
        "quality_score",
        "roi_coverage",
        "registration_score",
        "colour_saturation_mean",
        "colour_value_mean",
    }
)


class FeatureContractError(ValueError):
    """Row violates the ``features_v1`` contract."""


def to_row(features: dict[str, float | None]) -> tuple[float, ...]:
    """Render a feature mapping into the canonical column order."""

    unknown = sorted(set(features) - set(FEATURES_V1_COLUMNS))
    if unknown:
        raise FeatureContractError(f"unknown feature columns: {','.join(unknown)}")
    row: list[float] = []
    for column in FEATURES_V1_COLUMNS:
        value = features.get(column)
        if value is None:
            row.append(MISSING_VALUE)
            continue
        number = float(value)
        if number < 0.0 and column in NON_NEGATIVE_COLUMNS:
            raise FeatureContractError(f"negative value for non-negative column: {column}")
        if column in RATIO_COLUMNS and number > 1.0:
            raise FeatureContractError(f"ratio above 1.0 for column: {column}")
        row.append(number)
    return tuple(row)


def validate_row(row: tuple[float, ...]) -> None:
    if len(row) != len(FEATURES_V1_COLUMNS):
        raise FeatureContractError(
            f"expected {len(FEATURES_V1_COLUMNS)} columns, received {len(row)}"
        )
    to_row(dict(zip(FEATURES_V1_COLUMNS, row, strict=True)))
