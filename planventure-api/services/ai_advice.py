"""Orchestration for safe, cached Gemini trip advice."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from pydantic import ValidationError

from extensions import cache
from schemas.ai_advice import AIAdviceRequest, TripAIAdvice
from services.gemini import (
    PROMPT_VERSION,
    GeminiConfigurationError,
    GeminiInvalidResponseError,
    generate_trip_advice,
)
from services.weather import (
    ForecastNotAvailableYetError,
    TripInPastError,
    WeatherServiceError,
    get_forecast_for_trip,
)


AI_ADVICE_CACHE_SCHEMA_VERSION = "v1"
AI_ADVICE_CACHE_PREFIX = f"ai-advice:{AI_ADVICE_CACHE_SCHEMA_VERSION}"

WEATHER_UNAVAILABLE_CODE = "WEATHER_UNAVAILABLE"
WEATHER_UNAVAILABLE_MESSAGE = "Weather forecast is currently unavailable."
WEATHER_PARTIAL_COVERAGE_CODE = "WEATHER_PARTIAL_COVERAGE"
WEATHER_PARTIAL_COVERAGE_MESSAGE = (
    "Weather forecast does not cover the entire trip."
)

_EXPECTED_WEATHER_ERRORS = (
    WeatherServiceError,
    TripInPastError,
    ForecastNotAvailableYetError,
)
_MISSING = object()


@dataclass(frozen=True, slots=True)
class AIAdviceWarning:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class AIAdviceResult:
    advice: TripAIAdvice
    warnings: tuple[AIAdviceWarning, ...]
    cache_hit: bool
    weather_cache_hit: bool | None


class AIAdviceServiceError(RuntimeError):
    """Base exception for orchestration failures."""


class AIAdviceDisabledError(AIAdviceServiceError):
    """Gemini advice is disabled by application configuration."""


class AIAdviceInputError(AIAdviceServiceError):
    """Trip, request, or cache configuration input is invalid."""


def _required_attribute(value: object, name: str) -> Any:
    attribute = getattr(value, name, _MISSING)
    if attribute is _MISSING:
        raise AIAdviceInputError(f"Trip requires {name}")
    return attribute


def _serialize_date(value: object, field_name: str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise AIAdviceInputError(
                f"Trip {field_name} must be an ISO date"
            ) from exc
        if parsed.isoformat() != value:
            raise AIAdviceInputError(
                f"Trip {field_name} must use YYYY-MM-DD format"
            )
        return value
    raise AIAdviceInputError(
        f"Trip {field_name} must be a date or ISO date string"
    )


def _plain_json_copy(value: object, field_name: str) -> Any:
    try:
        serialized = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return json.loads(serialized)
    except (TypeError, ValueError) as exc:
        raise AIAdviceInputError(
            f"{field_name} must contain JSON-compatible data"
        ) from exc


def _serialize_trip_for_ai(
    trip: object,
) -> tuple[int, dict[str, object], dict[str, object] | list[object]]:
    trip_id = _required_attribute(trip, "id")
    if (
        isinstance(trip_id, bool)
        or not isinstance(trip_id, int)
        or trip_id <= 0
    ):
        raise AIAdviceInputError("Trip ID must be a positive integer")

    destination = _required_attribute(trip, "destination")
    if not isinstance(destination, str) or not destination.strip():
        raise AIAdviceInputError("Trip destination is required")

    start_date = _serialize_date(
        _required_attribute(trip, "start_date"),
        "start_date",
    )
    end_date = _serialize_date(
        _required_attribute(trip, "end_date"),
        "end_date",
    )
    if date.fromisoformat(start_date) > date.fromisoformat(end_date):
        raise AIAdviceInputError(
            "Trip start_date must be before or equal to end_date"
        )

    itinerary = _required_attribute(trip, "itinerary")
    if not isinstance(itinerary, (dict, list)):
        raise AIAdviceInputError(
            "Trip itinerary must be an object or an array"
        )
    plain_itinerary = _plain_json_copy(itinerary, "Trip itinerary")

    trip_payload = {
        "destination": destination.strip(),
        "start_date": start_date,
        "end_date": end_date,
    }
    return trip_id, trip_payload, plain_itinerary


def _serialize_coverage(coverage: object) -> dict[str, object]:
    requested_from = _serialize_date(
        _required_attribute(coverage, "requested_from"),
        "weather requested_from",
    )
    requested_through = _serialize_date(
        _required_attribute(coverage, "requested_through"),
        "weather requested_through",
    )
    available_through = _serialize_date(
        _required_attribute(coverage, "available_through"),
        "weather available_through",
    )

    provider_days = _required_attribute(coverage, "provider_days")
    if (
        isinstance(provider_days, bool)
        or not isinstance(provider_days, int)
        or provider_days <= 0
    ):
        raise AIAdviceInputError(
            "Weather coverage provider_days must be a positive integer"
        )

    complete = _required_attribute(coverage, "complete")
    if not isinstance(complete, bool):
        raise AIAdviceInputError(
            "Weather coverage complete must be a boolean"
        )

    return {
        "requested_from": requested_from,
        "requested_through": requested_through,
        "available_through": available_through,
        "provider_days": provider_days,
        "complete": complete,
    }


def _resolve_weather(
    trip: object,
    *,
    include_weather: bool,
) -> tuple[
    dict[str, object],
    tuple[AIAdviceWarning, ...],
    bool | None,
]:
    if not include_weather:
        return (
            {
                "status": "not_requested",
                "data": None,
                "coverage": None,
            },
            (),
            None,
        )

    try:
        weather, coverage, weather_cache_hit = get_forecast_for_trip(trip)
    except _EXPECTED_WEATHER_ERRORS:
        return (
            {
                "status": "unavailable",
                "data": None,
                "coverage": None,
            },
            (
                AIAdviceWarning(
                    code=WEATHER_UNAVAILABLE_CODE,
                    message=WEATHER_UNAVAILABLE_MESSAGE,
                ),
            ),
            None,
        )

    if not isinstance(weather, dict):
        raise AIAdviceInputError(
            "Weather service must return an object"
        )
    plain_weather = _plain_json_copy(weather, "Weather data")
    plain_coverage = _serialize_coverage(coverage)
    if not isinstance(weather_cache_hit, bool):
        raise AIAdviceInputError(
            "Weather cache state must be a boolean"
        )

    warnings: tuple[AIAdviceWarning, ...] = ()
    if not plain_coverage["complete"]:
        warnings = (
            AIAdviceWarning(
                code=WEATHER_PARTIAL_COVERAGE_CODE,
                message=WEATHER_PARTIAL_COVERAGE_MESSAGE,
            ),
        )

    return (
        {
            "status": "available",
            "data": plain_weather,
            "coverage": plain_coverage,
        },
        warnings,
        weather_cache_hit,
    )


def build_ai_advice_cache_key(
    *,
    trip_id: int,
    model: str,
    payload: Mapping[str, object],
) -> str:
    if (
        isinstance(trip_id, bool)
        or not isinstance(trip_id, int)
        or trip_id <= 0
    ):
        raise AIAdviceInputError("Trip ID must be a positive integer")
    if not isinstance(model, str) or not model.strip():
        raise GeminiConfigurationError("Gemini model is not configured")
    if not isinstance(payload, Mapping):
        raise AIAdviceInputError("AI advice payload must be a mapping")

    cache_input = {
        "cache_schema_version": AI_ADVICE_CACHE_SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model": model.strip(),
        "trip_id": trip_id,
        "payload": dict(payload),
    }
    try:
        canonical_input = json.dumps(
            cache_input,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise AIAdviceInputError(
            "AI advice payload must contain JSON-compatible data"
        ) from exc

    digest = hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()
    return f"{AI_ADVICE_CACHE_PREFIX}:{digest}"


def _cache_get(cache_key: str) -> object | None:
    try:
        return cache.get(cache_key)
    except Exception:
        return None


def _cache_delete(cache_key: str) -> None:
    try:
        cache.delete(cache_key)
    except Exception:
        return


def _cache_set(
    cache_key: str,
    advice: TripAIAdvice,
    *,
    cache_ttl_seconds: int,
) -> None:
    try:
        cache.set(
            cache_key,
            advice.model_dump(mode="json"),
            timeout=cache_ttl_seconds,
        )
    except Exception:
        return


def _validate_configuration(
    *,
    api_key: object,
    model: object,
    timeout_seconds: object,
    cache_ttl_seconds: object,
) -> tuple[str, str, int, int]:
    if not isinstance(api_key, str) or not api_key.strip():
        raise GeminiConfigurationError("Gemini API key is not configured")
    if not isinstance(model, str) or not model.strip():
        raise GeminiConfigurationError("Gemini model is not configured")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds <= 0
    ):
        raise GeminiConfigurationError(
            "Gemini timeout must be a positive integer"
        )
    if (
        isinstance(cache_ttl_seconds, bool)
        or not isinstance(cache_ttl_seconds, int)
        or cache_ttl_seconds <= 0
    ):
        raise AIAdviceInputError(
            "AI advice cache TTL must be a positive integer"
        )
    return (
        api_key.strip(),
        model.strip(),
        timeout_seconds,
        cache_ttl_seconds,
    )


def get_trip_ai_advice(
    trip: object,
    request_data: AIAdviceRequest,
    *,
    gemini_enabled: bool,
    api_key: str,
    model: str,
    timeout_seconds: int,
    cache_ttl_seconds: int,
) -> AIAdviceResult:
    """Resolve weather, use cached advice, or call Gemini for a trip."""

    if gemini_enabled is not True:
        raise AIAdviceDisabledError("Gemini trip advice is disabled")

    validated_api_key, validated_model, validated_timeout, validated_ttl = (
        _validate_configuration(
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            cache_ttl_seconds=cache_ttl_seconds,
        )
    )

    if not isinstance(request_data, AIAdviceRequest):
        raise AIAdviceInputError(
            "request_data must be an AIAdviceRequest"
        )

    trip_id, trip_payload, itinerary_payload = _serialize_trip_for_ai(trip)
    preferences_payload = request_data.preferences.model_dump(mode="json")
    preferences_payload = _plain_json_copy(
        preferences_payload,
        "AI advice preferences",
    )

    weather_payload, warnings, weather_cache_hit = _resolve_weather(
        trip,
        include_weather=request_data.include_weather,
    )
    payload = {
        "language": request_data.language,
        "trip": trip_payload,
        "itinerary": itinerary_payload,
        "weather": weather_payload,
        "preferences": preferences_payload,
    }
    cache_key = build_ai_advice_cache_key(
        trip_id=trip_id,
        model=validated_model,
        payload=payload,
    )

    if not request_data.regenerate:
        cached_value = _cache_get(cache_key)
        if cached_value is not None:
            if isinstance(cached_value, dict):
                try:
                    cached_advice = TripAIAdvice.model_validate(cached_value)
                except ValidationError:
                    cached_advice = None
                if cached_advice is not None:
                    return AIAdviceResult(
                        advice=cached_advice,
                        warnings=warnings,
                        cache_hit=True,
                        weather_cache_hit=weather_cache_hit,
                    )
            _cache_delete(cache_key)

    advice = generate_trip_advice(
        payload,
        api_key=validated_api_key,
        model=validated_model,
        timeout_seconds=validated_timeout,
    )
    if not isinstance(advice, TripAIAdvice):
        raise GeminiInvalidResponseError(
            "Gemini provider returned an invalid response"
        )

    _cache_set(
        cache_key,
        advice,
        cache_ttl_seconds=validated_ttl,
    )
    return AIAdviceResult(
        advice=advice,
        warnings=warnings,
        cache_hit=False,
        weather_cache_hit=weather_cache_hit,
    )


__all__ = [
    "AIAdviceDisabledError",
    "AIAdviceInputError",
    "AIAdviceResult",
    "AIAdviceServiceError",
    "AIAdviceWarning",
    "build_ai_advice_cache_key",
    "get_trip_ai_advice",
]
