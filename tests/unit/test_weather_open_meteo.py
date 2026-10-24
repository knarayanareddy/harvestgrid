import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from harvestgrid_weather import (
    ARCHIVE_URL,
    DAILY_FIELDS,
    FORECAST_URL,
    GDD_BASE_CELSIUS,
    WEATHER_ERROR_CODES,
    CoarsePoint,
    OpenMeteoWeatherAdapter,
    WeatherError,
    coarse_point,
    compute_features,
)

TODAY = date(2026, 10, 24)
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


class StubTransport:
    """Records requests and replays queued responses; no network access."""

    def __init__(self, responses: list[tuple[int, Mapping[str, str], bytes]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def get(
        self, url: str, params: Mapping[str, str], timeout: float
    ) -> tuple[int, Mapping[str, str], bytes]:
        self.calls.append((url, dict(params), timeout))
        if not self.responses:
            raise AssertionError("unexpected extra provider request")
        return self.responses.pop(0)


def payload(
    *,
    days: int = 3,
    start: date = date(2026, 10, 1),
    means: list[float | None] | None = None,
    mins: list[float | None] | None = None,
    maxs: list[float | None] | None = None,
    rain: list[float | None] | None = None,
) -> bytes:
    stamps = [(start + timedelta(days=index)).isoformat() for index in range(days)]
    return json.dumps(
        {
            "latitude": 52.0,
            "longitude": 4.3,
            "timezone": "UTC",
            "daily": {
                "time": stamps,
                "temperature_2m_mean": means if means is not None else [12.0] * days,
                "temperature_2m_min": mins if mins is not None else [8.0] * days,
                "temperature_2m_max": maxs if maxs is not None else [16.0] * days,
                "precipitation_sum": rain if rain is not None else [0.0] * days,
            },
        },
        separators=(",", ":"),
    ).encode()


def adapter(
    *responses: tuple[int, Mapping[str, str], bytes],
    clock: Any = None,
    cache_ttl: timedelta = timedelta(hours=6),
    max_retries: int = 2,
) -> tuple[OpenMeteoWeatherAdapter, StubTransport, list[float]]:
    transport = StubTransport(list(responses))
    sleeps: list[float] = []
    instance = OpenMeteoWeatherAdapter(
        transport=transport,
        clock=clock or (lambda: NOW),
        cache_ttl=cache_ttl,
        sleeper=sleeps.append,
        max_retries=max_retries,
        timeout_seconds=2.5,
    )
    return instance, transport, sleeps


def test_coarse_geohash_centre_is_rounded_before_any_request() -> None:
    point = coarse_point("u173z")
    assert isinstance(point, CoarsePoint)
    assert point.latitude == round(point.latitude, 1)
    assert point.longitude == round(point.longitude, 1)
    assert 50.0 <= point.latitude <= 54.0
    assert 3.0 <= point.longitude <= 6.0
    assert point.as_params() == {
        "latitude": f"{point.latitude:.1f}",
        "longitude": f"{point.longitude:.1f}",
    }
    assert coarse_point("  U173Z ") == point

    with pytest.raises(WeatherError, match="coarse_location_invalid"):
        coarse_point("")
    with pytest.raises(WeatherError, match="coarse_location_invalid"):
        coarse_point("52.0067")
    with pytest.raises(WeatherError, match="coarse_location_invalid") as precision:
        coarse_point("u173zp")
    assert precision.value.detail == "precision=6"
    with pytest.raises(WeatherError, match="coarse_location_invalid"):
        CoarsePoint(latitude=52.0067, longitude=4.3, geohash="u173z")
    with pytest.raises(WeatherError, match="coarse_location_invalid"):
        CoarsePoint(latitude=120.0, longitude=4.3, geohash="u173z")


def test_historical_fetch_sends_only_coarse_coordinates_and_records_provenance() -> None:
    instance, transport, _ = adapter((200, {}, payload(days=4)))
    observation = instance.historical(
        geohash="u173z", start=date(2026, 10, 1), end=date(2026, 10, 4)
    )

    url, params, timeout = transport.calls[0]
    assert url == ARCHIVE_URL
    assert timeout == 2.5
    assert params["latitude"] == f"{observation.coarse_latitude:.1f}"
    assert params["longitude"] == f"{observation.coarse_longitude:.1f}"
    assert len(params["latitude"].split(".")[1]) == 1
    assert params["daily"] == ",".join(DAILY_FIELDS)
    assert params["timezone"] == "UTC"
    assert "address" not in params

    assert observation.kind == "historical"
    assert observation.provider == "open-meteo"
    assert observation.coarse_geohash == "u173z"
    assert observation.requested_start == date(2026, 10, 1)
    assert observation.requested_end == date(2026, 10, 4)
    assert len(observation.source_timestamps) == 4
    assert len(observation.payload_sha256) == 64
    assert observation.from_cache is False
    provenance = observation.provenance
    assert provenance["exact_coordinates_included"] is False
    assert provenance["payload_sha256"] == observation.payload_sha256
    assert observation.observation_id.startswith("weather-historical-u173z-")


def test_features_summarize_temperature_and_rainfall_deterministically() -> None:
    body = payload(
        days=3,
        means=[10.0, 12.0, 14.0],
        mins=[6.0, 8.0, 10.0],
        maxs=[14.0, 16.0, 18.0],
        rain=[0.0, 2.5, 4.5],
    )
    instance, _, _ = adapter((200, {}, body))
    features = instance.historical(
        geohash="u173z", start=date(2026, 10, 1), end=date(2026, 10, 3)
    ).features

    assert features.mean_temperature_c == 12.0
    assert features.min_temperature_c == 6.0
    assert features.max_temperature_c == 18.0
    assert features.total_precipitation_mm == 7.0
    assert features.wettest_day_mm == 4.5
    assert features.days_with_rain == 2
    assert features.day_count == 3
    assert features.missing_days == 0
    # GDD base 5 °C over daily means of 10/12/14.
    assert features.growing_degree_days == pytest.approx(5.0 + 7.0 + 9.0, abs=1e-6)
    assert GDD_BASE_CELSIUS == 5.0
    assert features.as_dict()["growing_degree_days"] == features.growing_degree_days


def test_missing_provider_values_stay_unknown_instead_of_becoming_zero() -> None:
    body = payload(
        days=2,
        means=[None, 10.0],
        mins=[None, 4.0],
        maxs=[None, 20.0],
        rain=[None, 3.0],
    )
    instance, _, _ = adapter((200, {}, body))
    observation = instance.historical(
        geohash="u173z", start=date(2026, 10, 1), end=date(2026, 10, 2)
    )
    features = observation.features
    assert features.mean_temperature_c == 10.0
    assert features.missing_days == 1
    assert features.day_count == 2
    # Only the fully observed day contributes rainfall and growing degree days:
    # the partial first day has no min/max pair, so it adds nothing.
    assert features.total_precipitation_mm == 3.0
    assert features.growing_degree_days == pytest.approx((4.0 + 20.0) / 2 - GDD_BASE_CELSIUS)
    assert observation.daily[0].temperature_mean_c is None


def test_cache_prevents_duplicate_provider_calls_for_identical_requests() -> None:
    body = payload(days=2)
    instance, transport, _ = adapter((200, {}, body))
    first = instance.historical(geohash="u173z", start=date(2026, 10, 1), end=date(2026, 10, 2))
    second = instance.historical(geohash="u173z", start=date(2026, 10, 1), end=date(2026, 10, 2))
    assert len(transport.calls) == 1
    assert instance.request_count == 1
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.payload_sha256 == first.payload_sha256
    assert len(instance.cached_observations()) == 1

    # Forecast and historical are separate cache entries and endpoints.
    forecast_instance, forecast_transport, _ = adapter((200, {}, body), (200, {}, body))
    forecast_instance.forecast(geohash="u173z", start=TODAY, end=TODAY + timedelta(days=1))
    assert forecast_transport.calls[0][0] == FORECAST_URL


def test_expired_cache_triggers_a_new_request() -> None:
    body = payload(days=2)
    current = [NOW]
    instance, transport, _ = adapter(
        (200, {}, body),
        (200, {}, body),
        clock=lambda: current[0],
        cache_ttl=timedelta(minutes=30),
    )
    instance.historical(geohash="u173z", start=date(2026, 10, 1), end=date(2026, 10, 2))
    current[0] = NOW + timedelta(minutes=31)
    refreshed = instance.historical(geohash="u173z", start=date(2026, 10, 1), end=date(2026, 10, 2))
    assert len(transport.calls) == 2
    assert refreshed.from_cache is False


def test_rate_limit_retries_with_retry_after_then_succeeds() -> None:
    instance, transport, sleeps = adapter(
        (429, {"Retry-After": "3"}, b""), (200, {}, payload(days=1))
    )
    observation = instance.historical(
        geohash="u173z", start=date(2026, 10, 1), end=date(2026, 10, 1)
    )
    assert len(transport.calls) == 2
    assert sleeps == [3.0]
    assert observation.features.day_count == 1


def test_rate_limit_and_server_errors_fail_visibly_when_retries_are_exhausted() -> None:
    limited, limited_transport, sleeps = adapter(
        (429, {"Retry-After": "2"}, b""),
        (429, {}, b""),
        (429, {}, b""),
    )
    with pytest.raises(WeatherError, match="provider_rate_limited") as rate_error:
        limited.historical(geohash="u173z", start=date(2026, 10, 1), end=date(2026, 10, 1))
    assert rate_error.value.detail == "retry_after=1"
    assert len(limited_transport.calls) == 3
    assert sleeps == [2.0, 1.0]

    unavailable, _, backoff = adapter((503, {}, b""), (502, {}, b""), (500, {}, b""))
    with pytest.raises(WeatherError, match="provider_unavailable"):
        unavailable.historical(geohash="u173z", start=date(2026, 10, 1), end=date(2026, 10, 1))
    assert backoff == [2.0, 4.0]

    forbidden, _, _ = adapter((403, {}, b"{}"))
    with pytest.raises(WeatherError, match="provider_http_error") as http_error:
        forbidden.historical(geohash="u173z", start=date(2026, 10, 1), end=date(2026, 10, 1))
    assert http_error.value.detail == "status=403"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"not json", "provider_response_invalid"),
        (b"[]", "provider_response_invalid"),
        (b'{"error":true,"reason":"bad"}', "provider_response_invalid"),
        (b'{"daily":{}}', "source_timestamp_missing"),
        (
            b'{"daily":{"time":["2026-10-01"],"temperature_2m_mean":[1],"temperature_2m_min":[1]}}',
            "provider_response_invalid",
        ),
        (
            b'{"daily":{"time":["not-a-date"],"temperature_2m_mean":[1],'
            b'"temperature_2m_min":[1],"temperature_2m_max":[1],"precipitation_sum":[0]}}',
            "provider_response_invalid",
        ),
        (
            b'{"daily":{"time":["2026-10-01"],"temperature_2m_mean":["hot"],'
            b'"temperature_2m_min":[1],"temperature_2m_max":[1],"precipitation_sum":[0]}}',
            "provider_response_invalid",
        ),
    ],
)
def test_malformed_provider_payloads_fail_explicitly(body: bytes, code: str) -> None:
    instance, _, _ = adapter((200, {}, body))
    with pytest.raises(WeatherError, match=code):
        instance.historical(geohash="u173z", start=date(2026, 10, 1), end=date(2026, 10, 1))
    assert instance.cached_observations() == ()


def test_range_validation_rejects_invalid_and_excessive_windows() -> None:
    instance, transport, _ = adapter()
    with pytest.raises(WeatherError, match="date_range_invalid"):
        instance.historical(geohash="u173z", start=date(2026, 10, 5), end=date(2026, 10, 1))
    with pytest.raises(WeatherError, match="date_range_too_long"):
        instance.historical(geohash="u173z", start=date(2025, 1, 1), end=date(2026, 10, 1))
    with pytest.raises(WeatherError, match="date_range_invalid"):
        instance.forecast(geohash="u173z", start=TODAY, end=TODAY + timedelta(days=30))
    # A transport-level outage surfaces as an explicit provider failure.
    assert adapter_raising() == "dns"
    assert transport.calls == []


def adapter_raising() -> str:
    class Broken:
        def get(self, url: str, params: Mapping[str, str], timeout: float) -> Any:
            raise WeatherError("provider_unavailable", detail="dns")

    instance = OpenMeteoWeatherAdapter(
        transport=Broken(), clock=lambda: NOW, sleeper=lambda _: None
    )
    with pytest.raises(WeatherError, match="provider_unavailable") as failure:
        instance.historical(geohash="u173z", start=date(2026, 10, 1), end=date(2026, 10, 1))
    return failure.value.detail


def test_feature_computation_rejects_an_empty_series() -> None:
    with pytest.raises(WeatherError, match="provider_response_invalid"):
        compute_features(())
    assert {
        "coarse_location_invalid",
        "date_range_invalid",
        "provider_http_error",
        "provider_rate_limited",
        "provider_response_invalid",
        "provider_unavailable",
    } <= WEATHER_ERROR_CODES
    with pytest.raises(ValueError, match="Unknown weather error code"):
        WeatherError("invented_weather_failure")


def test_naive_clock_is_rejected() -> None:
    instance, _, _ = adapter((200, {}, payload(days=1)), clock=lambda: datetime(2026, 10, 24))
    with pytest.raises(ValueError, match="timezone-aware"):
        instance.historical(geohash="u173z", start=date(2026, 10, 1), end=date(2026, 10, 1))
