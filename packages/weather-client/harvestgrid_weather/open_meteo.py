"""Open-Meteo coarse-location weather adapter (``INT-HG-005``).

Rules enforced here:

* only **coarse** coordinates leave the process: the coarse geohash is decoded to its cell
  centre and rounded to ``0.1`` degrees before any request is built;
* every response records the provider, endpoint, rounded coordinates, source timestamps,
  and a payload SHA-256 so features stay reproducible;
* rate limits, provider errors, and malformed payloads are explicit failures, never
  silent zeros, and a failed fetch never becomes a "dry" weather observation;
* identical requests inside the cache TTL do not re-fetch, which keeps duplicate Ring or
  scheduled events from producing duplicate weather observations.

The transport is injectable. :class:`UrllibTransport` is the real HTTP transport; tests
use a stub so the suite performs no network I/O.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DAILY_FIELDS = (
    "temperature_2m_mean",
    "temperature_2m_min",
    "temperature_2m_max",
    "precipitation_sum",
)
COARSE_DECIMALS = 1
MAX_RANGE_DAYS = 366
CACHE_TTL = timedelta(hours=6)
GDD_BASE_CELSIUS = 5.0
GEOHASH_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"

WEATHER_ERROR_CODES = frozenset(
    {
        "coarse_location_invalid",
        "date_range_invalid",
        "date_range_too_long",
        "provider_http_error",
        "provider_rate_limited",
        "provider_response_invalid",
        "provider_unavailable",
        "source_timestamp_missing",
    }
)


class WeatherError(RuntimeError):
    """Deterministic weather-adapter failure with a stable code."""

    def __init__(self, code: str, *, detail: str = "") -> None:
        if code not in WEATHER_ERROR_CODES:
            raise ValueError(f"Unknown weather error code: {code}")
        super().__init__(code)
        self.code = code
        self.detail = detail


class WeatherTransport(Protocol):
    def get(
        self, url: str, params: Mapping[str, str], timeout: float
    ) -> tuple[int, Mapping[str, str], bytes]: ...


class UrllibTransport:
    """Real HTTPS transport (stdlib only); never used by the default test suite."""

    def get(
        self, url: str, params: Mapping[str, str], timeout: float
    ) -> tuple[int, Mapping[str, str], bytes]:
        query = urllib.parse.urlencode(dict(sorted(params.items())))
        request = urllib.request.Request(  # noqa: S310 - fixed https endpoints
            f"{url}?{query}", headers={"User-Agent": "HarvestGrid/0.1 (weather-adapter)"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            body = error.read()
            return error.code, dict(error.headers or {}), body
        except urllib.error.URLError as error:
            raise WeatherError("provider_unavailable", detail=str(error.reason)) from None


def _decode_geohash(geohash: str) -> tuple[float, float, float, float]:
    lat_interval = [-90.0, 90.0]
    lon_interval = [-180.0, 180.0]
    even = True
    for character in geohash:
        index = GEOHASH_ALPHABET.find(character)
        if index < 0:
            raise WeatherError("coarse_location_invalid", detail="geohash")
        for mask in (16, 8, 4, 2, 1):
            interval = lon_interval if even else lat_interval
            midpoint = (interval[0] + interval[1]) / 2
            if index & mask:
                interval[0] = midpoint
            else:
                interval[1] = midpoint
            even = not even
    return lat_interval[0], lat_interval[1], lon_interval[0], lon_interval[1]


@dataclass(frozen=True)
class CoarsePoint:
    """Provider-facing coordinate, rounded to 0.1 degrees (about 11 km)."""

    latitude: float
    longitude: float
    geohash: str

    def __post_init__(self) -> None:
        if (
            round(self.latitude, COARSE_DECIMALS) != self.latitude
            or round(self.longitude, COARSE_DECIMALS) != self.longitude
        ):
            raise WeatherError("coarse_location_invalid", detail="precision")
        if not (-90.0 <= self.latitude <= 90.0) or not (-180.0 <= self.longitude <= 180.0):
            raise WeatherError("coarse_location_invalid", detail="range")

    def as_params(self) -> dict[str, str]:
        return {"latitude": f"{self.latitude:.1f}", "longitude": f"{self.longitude:.1f}"}


def coarse_point(geohash: str) -> CoarsePoint:
    """Cell centre rounded to 0.1 degrees; precision is capped before any request."""

    cell = geohash.strip().lower()
    if not cell or any(character not in GEOHASH_ALPHABET for character in cell):
        raise WeatherError("coarse_location_invalid", detail="geohash")
    if not 4 <= len(cell) <= 5:
        raise WeatherError("coarse_location_invalid", detail=f"precision={len(cell)}")
    south, north, west, east = _decode_geohash(cell)
    latitude = round((south + north) / 2, COARSE_DECIMALS)
    longitude = round((west + east) / 2, COARSE_DECIMALS)
    # Rounding can land exactly on a .05 boundary; keep the value a clean 0.1 multiple.
    latitude = round(latitude + 0.0, COARSE_DECIMALS)
    longitude = round(longitude + 0.0, COARSE_DECIMALS)
    return CoarsePoint(latitude=latitude, longitude=longitude, geohash=cell)


@dataclass(frozen=True)
class DailyWeather:
    day: date
    temperature_mean_c: float | None
    temperature_min_c: float | None
    temperature_max_c: float | None
    precipitation_mm: float | None


@dataclass(frozen=True)
class WeatherFeatures:
    """Coarse-location features consumed by the forecast model."""

    mean_temperature_c: float | None
    min_temperature_c: float | None
    max_temperature_c: float | None
    total_precipitation_mm: float
    growing_degree_days: float
    day_count: int
    missing_days: int
    warmest_day_c: float | None
    wettest_day_mm: float | None
    days_with_rain: int

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "mean_temperature_c": self.mean_temperature_c,
            "min_temperature_c": self.min_temperature_c,
            "max_temperature_c": self.max_temperature_c,
            "total_precipitation_mm": self.total_precipitation_mm,
            "growing_degree_days": self.growing_degree_days,
            "day_count": self.day_count,
            "missing_days": self.missing_days,
            "warmest_day_c": self.warmest_day_c,
            "wettest_day_mm": self.wettest_day_mm,
            "days_with_rain": self.days_with_rain,
        }


@dataclass(frozen=True)
class WeatherObservation:
    """Reproducible, provenance-bearing weather record (no exact coordinates)."""

    observation_id: str
    kind: str
    provider: str
    endpoint: str
    coarse_geohash: str
    coarse_latitude: float
    coarse_longitude: float
    requested_start: date
    requested_end: date
    source_timestamps: tuple[date, ...]
    fetched_at: datetime
    payload_sha256: str
    daily: tuple[DailyWeather, ...]
    features: WeatherFeatures
    from_cache: bool = False

    @property
    def provenance(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "endpoint": self.endpoint,
            "coarse_geohash": self.coarse_geohash,
            "coarse_latitude": self.coarse_latitude,
            "coarse_longitude": self.coarse_longitude,
            "payload_sha256": self.payload_sha256,
            "fetched_at": self.fetched_at.isoformat(),
            "exact_coordinates_included": False,
        }


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherError("provider_response_invalid", detail="non_numeric")
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number, 3)


def compute_features(daily: tuple[DailyWeather, ...]) -> WeatherFeatures:
    """Aggregate daily weather into model features without inventing missing values."""

    if not daily:
        raise WeatherError("provider_response_invalid", detail="no_days")
    means = [day.temperature_mean_c for day in daily if day.temperature_mean_c is not None]
    minima = [day.temperature_min_c for day in daily if day.temperature_min_c is not None]
    maxima = [day.temperature_max_c for day in daily if day.temperature_max_c is not None]
    rain = [day.precipitation_mm for day in daily if day.precipitation_mm is not None]
    gdd = 0.0
    for day in daily:
        if day.temperature_max_c is None or day.temperature_min_c is None:
            continue
        average = (day.temperature_max_c + day.temperature_min_c) / 2
        gdd += max(0.0, average - GDD_BASE_CELSIUS)
    return WeatherFeatures(
        mean_temperature_c=round(sum(means) / len(means), 3) if means else None,
        min_temperature_c=min(minima) if minima else None,
        max_temperature_c=max(maxima) if maxima else None,
        total_precipitation_mm=round(sum(rain), 3),
        growing_degree_days=round(gdd, 3),
        day_count=len(daily),
        missing_days=sum(1 for day in daily if day.temperature_mean_c is None),
        warmest_day_c=max(maxima) if maxima else None,
        wettest_day_mm=max(rain) if rain else None,
        days_with_rain=sum(1 for value in rain if value > 0.0),
    )


@dataclass
class _CacheEntry:
    payload_sha256: str
    observation: WeatherObservation
    stored_at: datetime


@dataclass
class OpenMeteoWeatherAdapter:
    """Historical/forecast weather for coarse geohashes via Open-Meteo."""

    transport: WeatherTransport
    clock: Callable[[], datetime]
    timeout_seconds: float = 10.0
    cache_ttl: timedelta = CACHE_TTL
    sleeper: Callable[[float], None] = time.sleep
    max_retries: int = 2
    provider: str = "open-meteo"
    _cache: dict[str, _CacheEntry] = field(default_factory=dict)
    request_count: int = 0

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("weather clock must be timezone-aware")
        return now.astimezone(UTC)

    def _validate_range(self, start: date, end: date) -> None:
        if start > end:
            raise WeatherError("date_range_invalid", detail="start_after_end")
        if (end - start).days > MAX_RANGE_DAYS:
            raise WeatherError("date_range_too_long", detail=str((end - start).days))
        if end > self._now().date() + timedelta(days=16):
            raise WeatherError("date_range_invalid", detail="beyond_forecast_horizon")

    def _cache_key(self, kind: str, point: CoarsePoint, start: date, end: date) -> str:
        return f"{kind}:{point.geohash}:{point.latitude:.1f}:{point.longitude:.1f}:{start}:{end}"

    def _fetch(self, *, kind: str, point: CoarsePoint, start: date, end: date) -> tuple[bytes, int]:
        endpoint = ARCHIVE_URL if kind == "historical" else FORECAST_URL
        params = {
            **point.as_params(),
            "daily": ",".join(DAILY_FIELDS),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": "UTC",
            "temperature_unit": "celsius",
            "precipitation_unit": "mm",
        }
        attempt = 0
        while True:
            self.request_count += 1
            status, headers, body = self.transport.get(endpoint, params, self.timeout_seconds)
            if status == 429:
                retry_after = _retry_after(headers)
                if attempt >= self.max_retries:
                    raise WeatherError(
                        "provider_rate_limited", detail=f"retry_after={retry_after:.0f}"
                    )
                attempt += 1
                self.sleeper(retry_after)
                continue
            if status >= 500:
                if attempt >= self.max_retries:
                    raise WeatherError("provider_unavailable", detail=f"status={status}")
                attempt += 1
                self.sleeper(min(2.0**attempt, 10.0))
                continue
            if status != 200:
                raise WeatherError("provider_http_error", detail=f"status={status}")
            return body, status

    def _build(
        self,
        *,
        kind: str,
        point: CoarsePoint,
        start: date,
        end: date,
        body: bytes,
        from_cache: bool,
    ) -> WeatherObservation:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise WeatherError("provider_response_invalid", detail="json") from None
        if not isinstance(payload, dict):
            raise WeatherError("provider_response_invalid", detail="object_required")
        if payload.get("error"):
            raise WeatherError("provider_response_invalid", detail="provider_error")
        daily_block = payload.get("daily")
        if not isinstance(daily_block, dict):
            raise WeatherError("provider_response_invalid", detail="daily_missing")
        times = daily_block.get("time")
        if not isinstance(times, list) or not times:
            raise WeatherError("source_timestamp_missing", detail="daily.time")
        columns: dict[str, list[Any]] = {}
        for name in DAILY_FIELDS:
            values = daily_block.get(name)
            if not isinstance(values, list) or len(values) != len(times):
                raise WeatherError("provider_response_invalid", detail=f"daily.{name}")
            columns[name] = values
        daily: list[DailyWeather] = []
        for index, stamp in enumerate(times):
            try:
                day = date.fromisoformat(str(stamp))
            except ValueError:
                raise WeatherError("provider_response_invalid", detail="daily.time") from None
            daily.append(
                DailyWeather(
                    day=day,
                    temperature_mean_c=_as_float(columns["temperature_2m_mean"][index]),
                    temperature_min_c=_as_float(columns["temperature_2m_min"][index]),
                    temperature_max_c=_as_float(columns["temperature_2m_max"][index]),
                    precipitation_mm=_as_float(columns["precipitation_sum"][index]),
                )
            )
        features = compute_features(tuple(daily))
        payload_sha256 = hashlib.sha256(body).hexdigest()
        fetched_at = self._now()
        endpoint = ARCHIVE_URL if kind == "historical" else FORECAST_URL
        return WeatherObservation(
            observation_id=f"weather-{kind}-{point.geohash}-{start}-{end}-{payload_sha256[:12]}",
            kind=kind,
            provider=self.provider,
            endpoint=endpoint,
            coarse_geohash=point.geohash,
            coarse_latitude=point.latitude,
            coarse_longitude=point.longitude,
            requested_start=start,
            requested_end=end,
            source_timestamps=tuple(day.day for day in daily),
            fetched_at=fetched_at,
            payload_sha256=payload_sha256,
            daily=tuple(daily),
            features=features,
            from_cache=from_cache,
        )

    def _get(self, kind: str, geohash: str, start: date, end: date) -> WeatherObservation:
        point = coarse_point(geohash)
        self._validate_range(start, end)
        key = self._cache_key(kind, point, start, end)
        cached = self._cache.get(key)
        if cached is not None and self._now() - cached.stored_at <= self.cache_ttl:
            return _with_cache_flag(cached.observation, True)
        body, _status = self._fetch(kind=kind, point=point, start=start, end=end)
        observation = self._build(
            kind=kind, point=point, start=start, end=end, body=body, from_cache=False
        )
        self._cache[key] = _CacheEntry(
            payload_sha256=observation.payload_sha256,
            observation=observation,
            stored_at=self._now(),
        )
        return observation

    def historical(self, *, geohash: str, start: date, end: date) -> WeatherObservation:
        return self._get("historical", geohash, start, end)

    def forecast(self, *, geohash: str, start: date, end: date) -> WeatherObservation:
        return self._get("forecast", geohash, start, end)

    def cached_observations(self) -> tuple[WeatherObservation, ...]:
        return tuple(entry.observation for entry in self._cache.values())


def _with_cache_flag(observation: WeatherObservation, from_cache: bool) -> WeatherObservation:
    return WeatherObservation(
        observation_id=observation.observation_id,
        kind=observation.kind,
        provider=observation.provider,
        endpoint=observation.endpoint,
        coarse_geohash=observation.coarse_geohash,
        coarse_latitude=observation.coarse_latitude,
        coarse_longitude=observation.coarse_longitude,
        requested_start=observation.requested_start,
        requested_end=observation.requested_end,
        source_timestamps=observation.source_timestamps,
        fetched_at=observation.fetched_at,
        payload_sha256=observation.payload_sha256,
        daily=observation.daily,
        features=observation.features,
        from_cache=from_cache,
    )


def _retry_after(headers: Mapping[str, str]) -> float:
    lowered = {key.lower(): value for key, value in headers.items()}
    raw = lowered.get("retry-after")
    if raw is None:
        return 1.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 1.0
