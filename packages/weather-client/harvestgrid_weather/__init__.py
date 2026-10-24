"""Coarse-location weather adapter boundary for HarvestGrid forecasting."""

from .open_meteo import (
    ARCHIVE_URL,
    COARSE_DECIMALS,
    DAILY_FIELDS,
    FORECAST_URL,
    GDD_BASE_CELSIUS,
    WEATHER_ERROR_CODES,
    CoarsePoint,
    DailyWeather,
    OpenMeteoWeatherAdapter,
    UrllibTransport,
    WeatherError,
    WeatherFeatures,
    WeatherObservation,
    WeatherTransport,
    coarse_point,
    compute_features,
)

__all__ = [
    "ARCHIVE_URL",
    "COARSE_DECIMALS",
    "DAILY_FIELDS",
    "FORECAST_URL",
    "GDD_BASE_CELSIUS",
    "WEATHER_ERROR_CODES",
    "CoarsePoint",
    "DailyWeather",
    "OpenMeteoWeatherAdapter",
    "UrllibTransport",
    "WeatherError",
    "WeatherFeatures",
    "WeatherObservation",
    "WeatherTransport",
    "coarse_point",
    "compute_features",
]
