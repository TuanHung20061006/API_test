"""Authenticated HTTP endpoint for Gemini trip advice."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity
from pydantic import ValidationError

from extensions import limiter
from middleware import auth_required
from routes.trips import find_current_user_trip
from schemas.ai_advice import AIAdviceRequest
from services.ai_advice import (
    AIAdviceDisabledError,
    AIAdviceInputError,
    get_trip_ai_advice as orchestrate_trip_ai_advice,
)
from services.gemini import (
    GeminiAuthenticationError,
    GeminiConfigurationError,
    GeminiInvalidResponseError,
    GeminiProviderError,
    GeminiRateLimitError,
    GeminiTimeoutError,
)


ai_advice_bp = Blueprint(
    "ai_advice",
    __name__,
    url_prefix="/trip",
)

_MAX_VALIDATION_ERRORS = 20
_VALIDATION_MESSAGES = {
    "bool_type": "Value must be a boolean.",
    "dict_type": "Value must be an object.",
    "extra_forbidden": "Unexpected field.",
    "list_type": "Value must be a list.",
    "literal_error": "Value is not supported.",
    "missing": "Field is required.",
    "model_type": "Value must be an object.",
    "string_pattern_mismatch": "Value has an invalid format.",
    "string_too_long": "String exceeds the maximum allowed length.",
    "string_too_short": "String is shorter than the minimum allowed length.",
    "string_type": "Value must be a string.",
    "too_long": "List contains too many items.",
}


def ai_advice_rate_limit_key() -> str:
    return f"user:{get_jwt_identity()}"


def _error_response(
    code: str,
    message: str,
    status: int,
    *,
    details: dict[str, object] | None = None,
):
    error: dict[str, object] = {
        "code": code,
        "message": message,
    }
    if details is not None:
        error["details"] = details
    return jsonify({"error": error}), status


def _validation_details(error: ValidationError) -> dict[str, object]:
    fields = []
    for item in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )[:_MAX_VALIDATION_ERRORS]:
        error_type = str(item.get("type", "validation_error"))
        location = item.get("loc", ())
        field = ".".join(str(part) for part in location) or "request"
        fields.append(
            {
                "field": field,
                "type": error_type,
                "message": _VALIDATION_MESSAGES.get(
                    error_type,
                    "Invalid value.",
                ),
            }
        )
    return {"fields": fields}


def _parse_request_body():
    if not request.is_json:
        return None, _error_response(
            "AI_INVALID_REQUEST",
            "Request body must be valid JSON.",
            400,
        )

    max_request_bytes = current_app.config[
        "GEMINI_ADVICE_MAX_REQUEST_BYTES"
    ]
    if (
        request.content_length is not None
        and request.content_length > max_request_bytes
    ):
        return None, _error_response(
            "AI_REQUEST_TOO_LARGE",
            "AI advice request body is too large.",
            413,
        )

    raw_body = request.get_data(cache=True)
    if len(raw_body) > max_request_bytes:
        return None, _error_response(
            "AI_REQUEST_TOO_LARGE",
            "AI advice request body is too large.",
            413,
        )

    if not raw_body:
        return {}, None

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, _error_response(
            "AI_INVALID_REQUEST",
            "Request body must be valid JSON.",
            400,
        )
    return payload, None


@ai_advice_bp.route("/<int:trip_id>/ai-advice", methods=["POST"])
@auth_required
@limiter.limit(
    lambda: current_app.config["GEMINI_ADVICE_RATE_LIMIT"],
    key_func=ai_advice_rate_limit_key,
)
def get_trip_ai_advice(trip_id: int):
    trip = find_current_user_trip(trip_id)
    if not trip:
        return jsonify({"error": "Trip not found"}), 404

    payload, request_error = _parse_request_body()
    if request_error is not None:
        return request_error

    if payload.get("mode", "review") != "review":
        return _error_response(
            "AI_UNSUPPORTED_MODE",
            "Only review mode is currently supported.",
            400,
        )

    try:
        validated_request = AIAdviceRequest.model_validate(payload)
    except ValidationError as error:
        return _error_response(
            "AI_INVALID_REQUEST",
            "AI advice request is invalid.",
            400,
            details=_validation_details(error),
        )

    try:
        result = orchestrate_trip_ai_advice(
            trip,
            validated_request,
            gemini_enabled=current_app.config["GEMINI_ENABLED"],
            api_key=current_app.config["GEMINI_API_KEY"],
            model=current_app.config["GEMINI_MODEL"],
            timeout_seconds=current_app.config["GEMINI_TIMEOUT_SECONDS"],
            cache_ttl_seconds=current_app.config[
                "GEMINI_ADVICE_CACHE_TTL_SECONDS"
            ],
        )
    except AIAdviceDisabledError:
        return _error_response(
            "AI_DISABLED",
            "AI advice is currently disabled.",
            503,
        )
    except AIAdviceInputError:
        return _error_response(
            "AI_INVALID_REQUEST",
            "Trip data cannot be processed for AI advice.",
            400,
        )
    except GeminiAuthenticationError:
        return _error_response(
            "AI_PROVIDER_AUTHENTICATION_FAILED",
            "AI advice provider authentication failed.",
            502,
        )
    except GeminiConfigurationError:
        return _error_response(
            "AI_NOT_CONFIGURED",
            "AI advice service is not configured.",
            503,
        )
    except GeminiTimeoutError:
        return _error_response(
            "AI_PROVIDER_TIMEOUT",
            "AI advice provider timed out.",
            504,
        )
    except GeminiRateLimitError:
        return _error_response(
            "AI_PROVIDER_RATE_LIMITED",
            "AI advice provider is temporarily rate limited.",
            503,
        )
    except GeminiProviderError:
        return _error_response(
            "AI_PROVIDER_UNAVAILABLE",
            "AI advice provider is temporarily unavailable.",
            502,
        )
    except GeminiInvalidResponseError:
        return _error_response(
            "AI_INVALID_RESPONSE",
            "AI advice provider returned an invalid response.",
            502,
        )

    return jsonify(
        {
            "trip_id": trip.id,
            "mode": "review",
            "advice": result.advice.model_dump(mode="json"),
            "warnings": [
                {
                    "code": warning.code,
                    "message": warning.message,
                }
                for warning in result.warnings
            ],
            "meta": {
                "cache_hit": result.cache_hit,
                "weather_cache_hit": result.weather_cache_hit,
            },
        }
    ), 200


__all__ = [
    "ai_advice_bp",
    "ai_advice_rate_limit_key",
    "get_trip_ai_advice",
]
