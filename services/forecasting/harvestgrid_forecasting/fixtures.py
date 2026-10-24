"""Deterministic, explicitly labelled fixtures for the forecasting worker.

Nothing here is provider data. The synthetic source produces a stable seasonal temperature
curve per coarse geohash so API tests, worker tests, and demo runs are reproducible offline.
Every observation it returns is stamped ``provider="fixture-provider"`` and every prior is
``fixture_unreviewed``, which keeps projections experimental by construction.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from harvestgrid_weather import (
    DailyWeather,
    WeatherFeatures,
    WeatherObservation,
    compute_features,
)

from .ripening import PhenologyPrior, RipeningStage
from .scheduler import TreeSubscription

FIXTURE_PROVIDER = "fixture-provider"
FIXTURE_ORGANIZATION = "org-fixture-a"
OTHER_FIXTURE_ORGANIZATION = "org-fixture-b"

#: Unreviewed cultivar thresholds used only to exercise the curve machinery.
FIXTURE_STAGES: tuple[tuple[RipeningStage, float], ...] = (
    (RipeningStage.DORMANT, 0.0),
    (RipeningStage.BUD_BREAK, 60.0),
    (RipeningStage.BLOOM, 140.0),
    (RipeningStage.FRUIT_SET, 240.0),
    (RipeningStage.PIT_HARDENING, 460.0),
    (RipeningStage.VERAISON, 780.0),
    (RipeningStage.HARVEST_RIPE, 1050.0),
    (RipeningStage.PAST_RIPE, 1350.0),
)


def fixture_prior(cultivar_id: str = "european_plum") -> PhenologyPrior:
    """An explicitly unreviewed prior: projections using it can never look operational."""

    return PhenologyPrior(
        cultivar_id=cultivar_id,
        content_version="european_plum_v1",
        base_temperature_c=5.0,
        stage_gdd=FIXTURE_STAGES,
        review_status="fixture_unreviewed",
        source="fixture_only_not_a_reviewed_prior",
    )


@dataclass(frozen=True)
class SyntheticWeatherSource:
    """Deterministic coarse-weather source; labelled fixture, no network access."""

    season_phase_days: int = 90
    amplitude_c: float = 6.0
    mean_c: float = 14.0
    provider: str = FIXTURE_PROVIDER
    calls: tuple[tuple[str, str], ...] = ()

    def _day_temperature(self, day: date, offset: int) -> float:
        # A smooth sine over the season plus a small deterministic ripple.
        position = (day.timetuple().tm_yday + offset) % 365
        seasonal = self.mean_c + self.amplitude_c * (
            1.0 - abs((position % self.season_phase_days) / self.season_phase_days * 2.0 - 1.0)
        )
        ripple = ((day.toordinal() % 7) - 3) * 0.2
        return round(seasonal + ripple, 3)

    def _observation(
        self, *, kind: str, geohash: str, start: date, end: date
    ) -> WeatherObservation:
        days = (end - start).days + 1
        if days < 1:
            raise ValueError("synthetic weather range must include at least one day")
        daily = tuple(
            DailyWeather(
                day=start + timedelta(days=index),
                temperature_mean_c=self._day_temperature(start + timedelta(days=index), 0),
                temperature_min_c=round(
                    self._day_temperature(start + timedelta(days=index), 0) - 4.0, 3
                ),
                temperature_max_c=round(
                    self._day_temperature(start + timedelta(days=index), 0) + 4.0, 3
                ),
                precipitation_mm=round(((start.toordinal() + index) % 5) * 0.4, 3),
            )
            for index in range(days)
        )
        features: WeatherFeatures = compute_features(daily)
        fingerprint = hashlib.sha256(
            f"{kind}:{geohash}:{start}:{end}:{features.growing_degree_days}".encode()
        ).hexdigest()
        return WeatherObservation(
            observation_id=f"weather-{kind}-{geohash}-{start}-{end}-{fingerprint[:12]}",
            kind=kind,
            provider=self.provider,
            endpoint=f"fixture://{kind}",
            coarse_geohash=geohash,
            coarse_latitude=52.0,
            coarse_longitude=4.4,
            requested_start=start,
            requested_end=end,
            source_timestamps=tuple(day.day for day in daily),
            fetched_at=datetime.combine(end, datetime.min.time(), tzinfo=UTC),
            payload_sha256=fingerprint,
            daily=daily,
            features=features,
            from_cache=False,
        )

    def historical(self, *, geohash: str, start: date, end: date) -> WeatherObservation:
        return self._observation(kind="historical", geohash=geohash, start=start, end=end)

    def forecast(self, *, geohash: str, start: date, end: date) -> WeatherObservation:
        return self._observation(kind="forecast", geohash=geohash, start=start, end=end)


def fixture_subscriptions() -> tuple[TreeSubscription, ...]:
    """Two approved fixture trees in the same organization, on different coarse cells."""

    return (
        TreeSubscription(
            tree_id="tree-plum-01",
            organization_id=FIXTURE_ORGANIZATION,
            cultivar_id="european_plum",
            coarse_geohash="u173z",
            prior=fixture_prior(),
        ),
        TreeSubscription(
            tree_id="tree-plum-02",
            organization_id=FIXTURE_ORGANIZATION,
            cultivar_id="european_plum",
            coarse_geohash="u173y",
            prior=fixture_prior(),
        ),
    )


def seed_fixture_store(store: object) -> object:
    """Register the fixture subscriptions on any store exposing ``add``."""

    add = getattr(store, "add", None)
    if add is None:  # pragma: no cover - defensive, prototype is explicit
        raise TypeError("store does not support add()")
    for subscription in fixture_subscriptions():
        add(subscription)
    return store
