import hashlib
import requests
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta

from flask import current_app

from extensions import cache


class WeatherServiceError(RuntimeError):
    pass


class WeatherServiceNotConfigured(WeatherServiceError):
    pass


class WeatherServiceTimeout(WeatherServiceError):
    pass


class WeatherProviderResponseError(WeatherServiceError):
    pass


class WeatherLocationNotFound(WeatherProviderResponseError):
    pass


class WeatherProviderAuthenticationError(WeatherProviderResponseError):
    pass


class WeatherProviderQuotaExceeded(WeatherProviderResponseError):
    pass


class WeatherProviderAccessDenied(WeatherProviderResponseError):
    pass


# Backward-compatible name used by the first normalization tests.
WeatherAPIConfigurationError = WeatherServiceNotConfigured


class TripInPastError(ValueError):
    pass


class ForecastNotAvailableYetError(ValueError):
    def __init__(self, forecast_available_from):
        self.forecast_available_from = forecast_available_from
        super().__init__(
            f"Forecast will be available from {forecast_available_from.isoformat()}."
        )


@dataclass(frozen=True)
class ForecastCoverage:
    requested_from: date
    requested_through: date
    available_through: date
    provider_days: int
    complete: bool


PROVIDER_ERROR_TYPES = {
    1006: WeatherLocationNotFound,
    2006: WeatherProviderAuthenticationError,
    2007: WeatherProviderQuotaExceeded,
    2009: WeatherProviderAccessDenied,
}
WEATHER_CACHE_SCHEMA_VERSION = "v1"
WEATHER_CACHE_HASH_LENGTH = 16


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _as_list(value):
    return value if isinstance(value, list) else []


def _format_coordinate(value):
    return f"{value:.5f}".rstrip("0").rstrip(".")


def _valid_coordinate(value, minimum, maximum):
    return (
        value is not None
        and not isinstance(value, bool)
        and isinstance(value, (int, float))
        and minimum <= value <= maximum
    )


def get_trip_weather_query(trip):
    coordinates = _as_dict(getattr(trip, "coordinates", None))
    latitude = coordinates.get("lat")
    longitude = coordinates.get("lng")

    if _valid_coordinate(latitude, -90, 90) and _valid_coordinate(
        longitude, -180, 180
    ):
        return f"{_format_coordinate(latitude)},{_format_coordinate(longitude)}"

    destination = getattr(trip, "destination", None)
    if isinstance(destination, str) and destination.strip():
        return destination.strip()

    raise ValueError("Trip requires valid coordinates or a destination.")


def calculate_forecast_coverage(trip_start, trip_end, today=None):
    current_date = today or date.today()
    if not all(isinstance(value, date) for value in (trip_start, trip_end, current_date)):
        raise ValueError("Trip dates and today must be date values.")
    if trip_start > trip_end:
        raise ValueError("Trip start date must be before or equal to end date.")
    if trip_end < current_date:
        raise TripInPastError("Weather forecasts are unavailable for past trips.")

    requested_from = max(trip_start, current_date)
    provider_last_date = current_date + timedelta(days=13)
    if requested_from > provider_last_date:
        raise ForecastNotAvailableYetError(trip_start - timedelta(days=13))

    available_through = min(trip_end, provider_last_date)
    provider_days = (available_through - current_date).days + 1

    return ForecastCoverage(
        requested_from=requested_from,
        requested_through=trip_end,
        available_through=available_through,
        provider_days=provider_days,
        complete=trip_start >= current_date and trip_end <= provider_last_date,
    )


def normalize_forecast_day(item):
    forecast_day = _as_dict(item)
    day = _as_dict(forecast_day.get("day"))
    condition = _as_dict(day.get("condition"))
    astronomy = _as_dict(forecast_day.get("astro"))

    return {
        "date": forecast_day.get("date"),
        "temperature": {
            "min_c": day.get("mintemp_c"),
            "max_c": day.get("maxtemp_c"),
            "average_c": day.get("avgtemp_c"),
        },
        "condition": {
            "text": condition.get("text"),
            "code": condition.get("code"),
        },
        "chance_of_rain_percent": day.get("daily_chance_of_rain"),
        "total_precipitation_mm": day.get("totalprecip_mm"),
        "max_wind_kph": day.get("maxwind_kph"),
        "average_humidity_percent": day.get("avghumidity"),
        "uv_index": day.get("uv"),
        "sunrise": astronomy.get("sunrise"),
        "sunset": astronomy.get("sunset"),
    }


def normalize_alerts(payload):
    alerts_container = _as_dict(_as_dict(payload).get("alerts"))
    alerts = _as_list(alerts_container.get("alert"))

    return [
        {
            "headline": alert.get("headline"),
            "severity": alert.get("severity"),
            "urgency": alert.get("urgency"),
            "areas": alert.get("areas"),
            "effective": alert.get("effective"),
            "expires": alert.get("expires"),
            "description": alert.get("desc"),
            "instruction": alert.get("instruction"),
        }
        for alert in alerts
        if isinstance(alert, dict)
    ]


def normalize_forecast(payload):
    provider_payload = _as_dict(payload)
    location = _as_dict(provider_payload.get("location"))
    current = _as_dict(provider_payload.get("current"))
    current_condition = _as_dict(current.get("condition"))
    forecast_container = _as_dict(provider_payload.get("forecast"))
    forecast_days = _as_list(forecast_container.get("forecastday"))

    return {
        "provider": "weatherapi",
        "location": {
            "name": location.get("name"),
            "region": location.get("region"),
            "country": location.get("country"),
            "latitude": location.get("lat"),
            "longitude": location.get("lon"),
            "timezone": location.get("tz_id"),
            "localtime": location.get("localtime"),
        },
        "current": {
            "updated_at": current.get("last_updated"),
            "temperature_c": current.get("temp_c"),
            "feels_like_c": current.get("feelslike_c"),
            "condition": current_condition.get("text"),
            "condition_code": current_condition.get("code"),
            "humidity_percent": current.get("humidity"),
            "wind_kph": current.get("wind_kph"),
            "precipitation_mm": current.get("precip_mm"),
            "visibility_km": current.get("vis_km"),
            "uv_index": current.get("uv"),
        },
        "forecast": [
            normalize_forecast_day(item)
            for item in forecast_days
            if isinstance(item, dict)
        ],
        "alerts": normalize_alerts(provider_payload),
    }


def _raise_provider_error(payload):
    error = _as_dict(_as_dict(payload).get("error"))
    if not error:
        return

    error_type = PROVIDER_ERROR_TYPES.get(
        error.get("code"), WeatherProviderResponseError
    )
    raise error_type(error.get("message") or "Weather provider returned an error.")


def build_weather_cache_key(query, days, language="vi"):
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Weather location query is required.")
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 14:
        raise ValueError("Weather forecast days must be between 1 and 14.")
    if not isinstance(language, str) or not language.strip():
        raise ValueError("Weather forecast language is required.")

    normalized_query = query.strip().lower()
    normalized_language = language.strip().lower()
    location_hash = hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()[
        :WEATHER_CACHE_HASH_LENGTH
    ]
    return (
        f"weather:forecast:{WEATHER_CACHE_SCHEMA_VERSION}:{date.today().isoformat()}:"
        f"{location_hash}:{days}:{normalized_language}"
    )


def fetch_forecast(location, days, language="vi"):
    if not isinstance(location, str) or not location.strip():
        raise ValueError("Weather location query is required.")
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 14:
        raise ValueError("Weather forecast days must be between 1 and 14.")

    api_key = current_app.config["WEATHER_API_KEY"]
    if not api_key:
        raise WeatherServiceNotConfigured("WeatherAPI key is not configured.")

    try:
        response = requests.get(
            f"{current_app.config['WEATHER_API_BASE_URL']}/forecast.json",
            params={
                "key": api_key,
                "q": location.strip(),
                "days": days,
                "lang": language.strip().lower(),
                "alerts": "yes",
                "aqi": "no",
            },
            timeout=(
                current_app.config["WEATHER_API_CONNECT_TIMEOUT_SECONDS"],
                current_app.config["WEATHER_API_READ_TIMEOUT_SECONDS"],
            ),
        )
    except requests.Timeout as exc:
        raise WeatherServiceTimeout("Weather provider request timed out.") from exc
    except requests.RequestException as exc:
        raise WeatherServiceError("Weather provider request failed.") from exc

    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise WeatherProviderResponseError(
            "Weather provider returned invalid JSON."
        ) from exc

    if not isinstance(payload, dict):
        raise WeatherProviderResponseError(
            "Weather provider returned an invalid response."
        )

    _raise_provider_error(payload)

    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise WeatherServiceError("Weather provider request failed.") from exc

    return normalize_forecast(payload)


def get_cached_forecast(query, days, language="vi"):
    cache_key = build_weather_cache_key(query, days, language)
    cached_forecast = cache.get(cache_key)
    if cached_forecast is not None:
        return cached_forecast, True

    forecast = fetch_forecast(query, days, language=language)
    cache.set(
        cache_key,
        forecast,
        timeout=current_app.config["WEATHER_CACHE_TTL_SECONDS"],
    )
    return forecast, False


def get_forecast_for_trip(trip, today=None):
    coverage = calculate_forecast_coverage(
        trip.start_date,
        trip.end_date,
        today=today,
    )
    query = get_trip_weather_query(trip)
    cached_weather, cache_hit = get_cached_forecast(query, coverage.provider_days)
    weather = deepcopy(cached_weather)

    filtered_days = []
    for forecast_day in _as_list(weather.get("forecast")):
        if not isinstance(forecast_day, dict):
            continue
        try:
            forecast_date = date.fromisoformat(forecast_day.get("date", ""))
        except (TypeError, ValueError):
            continue
        if coverage.requested_from <= forecast_date <= coverage.available_through:
            filtered_days.append(forecast_day)

    weather["forecast"] = filtered_days
    return weather, coverage, cache_hit
