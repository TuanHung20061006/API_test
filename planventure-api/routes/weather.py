from flask import Blueprint, current_app, g, jsonify
from flask_jwt_extended import get_jwt_identity

from extensions import db, limiter
from middleware import auth_required
from models import Trip
from services.weather import (
    ForecastNotAvailableYetError,
    TripInPastError,
    WeatherLocationNotFound,
    WeatherProviderQuotaExceeded,
    WeatherProviderResponseError,
    WeatherServiceError,
    WeatherServiceNotConfigured,
    WeatherServiceTimeout,
    get_forecast_for_trip,
)


weather_bp = Blueprint("weather", __name__, url_prefix="/trip")


def weather_rate_limit_key():
    return f"user:{get_jwt_identity()}"


def _error_response(message, code, status, **extra):
    return jsonify({"error": {"message": message, "code": code}, **extra}), status


def _log_weather_failure(error, trip_id):
    current_app.logger.warning(
        "Weather service failure type=%s trip_id=%s",
        type(error).__name__,
        trip_id,
    )


@weather_bp.route("/<int:trip_id>/weather", methods=["GET"])
@auth_required
@limiter.limit(
    lambda: current_app.config["WEATHER_RATE_LIMIT"],
    key_func=weather_rate_limit_key,
)
def get_trip_weather(trip_id):
    trip = db.session.execute(
        db.select(Trip).filter_by(id=trip_id, user_id=g.current_user.id)
    ).scalar_one_or_none()
    if not trip:
        return jsonify({"error": "Không tìm thấy chuyến đi."}), 404

    try:
        weather, coverage, cache_hit = get_forecast_for_trip(trip)
    except TripInPastError:
        return _error_response(
            "Chuyến đi đã kết thúc.",
            "TRIP_IS_IN_THE_PAST",
            422,
        )
    except ForecastNotAvailableYetError as error:
        return _error_response(
            "Dự báo chưa khả dụng cho ngày của chuyến đi.",
            "FORECAST_NOT_AVAILABLE_YET",
            422,
            forecast_available_from=error.forecast_available_from.isoformat(),
        )
    except WeatherLocationNotFound:
        return _error_response(
            "Không tìm thấy địa điểm thời tiết.",
            "WEATHER_LOCATION_NOT_FOUND",
            422,
        )
    except WeatherServiceTimeout as error:
        _log_weather_failure(error, trip.id)
        return _error_response(
            "Dịch vụ thời tiết phản hồi quá chậm.",
            "WEATHER_SERVICE_TIMEOUT",
            504,
        )
    except WeatherServiceNotConfigured as error:
        _log_weather_failure(error, trip.id)
        return _error_response(
            "Dịch vụ thời tiết chưa được cấu hình.",
            "WEATHER_SERVICE_NOT_CONFIGURED",
            503,
        )
    except WeatherProviderQuotaExceeded as error:
        _log_weather_failure(error, trip.id)
        return _error_response(
            "Dịch vụ thời tiết đã vượt giới hạn sử dụng.",
            "WEATHER_PROVIDER_QUOTA_EXCEEDED",
            503,
        )
    except (WeatherProviderResponseError, WeatherServiceError) as error:
        _log_weather_failure(error, trip.id)
        return _error_response(
            "Dịch vụ thời tiết tạm thời không khả dụng.",
            "WEATHER_SERVICE_UNAVAILABLE",
            503,
        )

    return jsonify(
        {
            "trip": {
                "id": trip.id,
                "destination": trip.destination,
                "start_date": trip.start_date.isoformat(),
                "end_date": trip.end_date.isoformat(),
            },
            "coverage": {
                "requested_from": coverage.requested_from.isoformat(),
                "requested_through": coverage.requested_through.isoformat(),
                "available_through": coverage.available_through.isoformat(),
                "complete": coverage.complete,
            },
            "weather": weather,
            "meta": {"cached": cache_hit},
        }
    ), 200
