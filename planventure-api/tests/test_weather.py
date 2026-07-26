import json
import os
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["FLASK_ENV"] = "development"
os.environ["RATELIMIT_ENABLED"] = "false"
os.environ["CACHE_TYPE"] = "SimpleCache"

from app import create_app
from extensions import cache
from services.weather import (
    ForecastNotAvailableYetError,
    TripInPastError,
    WeatherLocationNotFound,
    WeatherProviderAccessDenied,
    WeatherProviderAuthenticationError,
    WeatherProviderQuotaExceeded,
    WeatherProviderResponseError,
    WeatherServiceError,
    WeatherServiceNotConfigured,
    WeatherServiceTimeout,
    build_weather_cache_key,
    calculate_forecast_coverage,
    fetch_forecast,
    get_cached_forecast,
    get_forecast_for_trip,
    get_trip_weather_query,
    normalize_alerts,
    normalize_forecast,
    normalize_forecast_day,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "weatherapi_forecast.json"


def load_weather_fixture():
    with FIXTURE_PATH.open(encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


class WeatherNormalizationTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            WEATHER_API_KEY="test-weather-key",
            WEATHER_API_BASE_URL="https://api.weatherapi.com/v1",
            WEATHER_API_CONNECT_TIMEOUT_SECONDS=3.05,
            WEATHER_API_READ_TIMEOUT_SECONDS=8.0,
            WEATHER_CACHE_TTL_SECONDS=900,
        )
        with self.app.app_context():
            cache.clear()

    @staticmethod
    def provider_payload():
        payload = load_weather_fixture()
        payload["location"]["extra_provider_field"] = "discarded"
        payload["current"]["air_quality"] = {"pm2_5": 12}
        payload["current"]["condition"]["icon"] = "//cdn.example/icon.png"
        payload["forecast"]["forecastday"][0]["day"]["condition"]["icon"] = (
            "//cdn.example/rain.png"
        )
        payload["forecast"]["forecastday"][0]["astro"]["moonrise"] = "01:00 PM"
        payload["forecast"]["forecastday"][0]["hour"] = [
            {"time": "2026-07-26 00:00"}
        ]
        payload["alerts"]["alert"] = [
            {
                "headline": "Heavy rain",
                "severity": "Severe",
                "urgency": "Expected",
                "areas": "Da Nang",
                "effective": "2026-07-26T00:00:00+07:00",
                "expires": "2026-07-27T00:00:00+07:00",
                "desc": "Heavy rainfall is expected.",
                "instruction": "Avoid flooded roads.",
                "event": "Flood Watch",
            }
        ]
        return payload

    def test_normalize_forecast_contract_and_excludes_raw_fields(self):
        normalized = normalize_forecast(self.provider_payload())

        self.assertEqual(normalized["provider"], "weatherapi")
        self.assertEqual(normalized["location"]["latitude"], 16.07)
        self.assertEqual(normalized["current"]["condition_code"], 1003)
        self.assertEqual(normalized["forecast"][0]["temperature"]["min_c"], 26.0)
        self.assertEqual(normalized["alerts"][0]["description"], "Heavy rainfall is expected.")

        self.assertNotIn("extra_provider_field", normalized["location"])
        self.assertNotIn("air_quality", normalized["current"])
        self.assertNotIn("icon", normalized["forecast"][0]["condition"])
        self.assertNotIn("hour", normalized["forecast"][0])
        self.assertNotIn("event", normalized["alerts"][0])

    def test_missing_fields_return_none_or_empty_lists(self):
        normalized = normalize_forecast({})

        self.assertIsNone(normalized["location"]["name"])
        self.assertIsNone(normalized["current"]["temperature_c"])
        self.assertEqual(normalized["forecast"], [])
        self.assertEqual(normalized["alerts"], [])
        self.assertIsNone(normalize_forecast_day({})["temperature"]["min_c"])
        self.assertEqual(normalize_alerts({"alerts": {"alert": None}}), [])

    @patch("services.weather.requests.get")
    def test_fetch_forecast_returns_only_normalized_response(self, mock_get):
        response = Mock()
        response.json.return_value = load_weather_fixture()
        mock_get.return_value = response

        with self.app.app_context():
            result = fetch_forecast("16.07,108.22", 3)

        response.raise_for_status.assert_called_once_with()
        self.assertEqual(result["provider"], "weatherapi")
        self.assertNotIn("hour", result["forecast"][0])
        mock_get.assert_called_once_with(
            "https://api.weatherapi.com/v1/forecast.json",
            params={
                "key": "test-weather-key",
                "q": "16.07,108.22",
                "days": 3,
                "lang": "vi",
                "alerts": "yes",
                "aqi": "no",
            },
            timeout=(3.05, 8.0),
        )

    @patch("services.weather.requests.get")
    def test_fetch_forecast_requires_configured_key(self, mock_get):
        self.app.config["WEATHER_API_KEY"] = ""

        with self.app.app_context():
            with self.assertRaises(WeatherServiceNotConfigured):
                fetch_forecast("Da Nang", 3)
        mock_get.assert_not_called()

    @patch("services.weather.requests.get")
    def test_empty_query_raises_value_error_without_network(self, mock_get):
        with self.app.app_context():
            with self.assertRaises(ValueError):
                fetch_forecast("  ", 3)
        mock_get.assert_not_called()

    @patch("services.weather.requests.get")
    def test_zero_days_raises_value_error_without_network(self, mock_get):
        with self.app.app_context():
            with self.assertRaises(ValueError):
                fetch_forecast("Da Nang", 0)
        mock_get.assert_not_called()

    @patch("services.weather.requests.get")
    def test_more_than_fourteen_days_raises_value_error_without_network(
        self, mock_get
    ):
        with self.app.app_context():
            with self.assertRaises(ValueError):
                fetch_forecast("Da Nang", 15)
        mock_get.assert_not_called()

    @patch("services.weather.requests.get", side_effect=requests.Timeout)
    def test_timeout_is_mapped(self, mock_get):
        with self.app.app_context():
            with self.assertRaises(WeatherServiceTimeout):
                fetch_forecast("Da Nang", 3)
        mock_get.assert_called_once()

    @patch("services.weather.requests.get", side_effect=requests.RequestException)
    def test_request_exception_is_mapped(self, mock_get):
        with self.app.app_context():
            with self.assertRaises(WeatherServiceError):
                fetch_forecast("Da Nang", 3)
        mock_get.assert_called_once()

    @patch("services.weather.requests.get")
    def test_invalid_json_is_mapped(self, mock_get):
        response = Mock()
        response.json.side_effect = ValueError("invalid JSON")
        mock_get.return_value = response

        with self.app.app_context():
            with self.assertRaises(WeatherProviderResponseError):
                fetch_forecast("Da Nang", 3)

    def assert_provider_error(self, code, expected_error):
        response = Mock()
        response.json.return_value = {
            "error": {"code": code, "message": "Provider error"}
        }

        with patch("services.weather.requests.get", return_value=response):
            with self.app.app_context():
                with self.assertRaises(expected_error):
                    fetch_forecast("Da Nang", 3)

    def test_location_not_found_error_is_mapped(self):
        self.assert_provider_error(1006, WeatherLocationNotFound)

    def test_authentication_error_is_mapped(self):
        self.assert_provider_error(2006, WeatherProviderAuthenticationError)

    def test_quota_error_is_mapped(self):
        self.assert_provider_error(2007, WeatherProviderQuotaExceeded)

    def test_access_denied_error_is_mapped(self):
        self.assert_provider_error(2009, WeatherProviderAccessDenied)

    def test_cache_key_is_normalized_hashed_and_user_independent(self):
        first_key = build_weather_cache_key("  Da Nang  ", 3, "VI")
        second_key = build_weather_cache_key("da nang", 3, "vi")

        self.assertEqual(first_key, second_key)
        self.assertTrue(first_key.startswith("weather:forecast:v1:"))
        self.assertTrue(first_key.endswith(":3:vi"))
        self.assertNotIn("da nang", first_key)
        self.assertNotIn("test-weather-key", first_key)

    @patch("services.weather.cache.set")
    @patch("services.weather.cache.get", return_value=None)
    @patch("services.weather.fetch_forecast")
    def test_cache_miss_fetches_and_caches_success(
        self, mock_fetch, mock_cache_get, mock_cache_set
    ):
        forecast = {"provider": "weatherapi", "forecast": []}
        mock_fetch.return_value = forecast

        with self.app.app_context():
            result, cache_hit = get_cached_forecast("Da Nang", 3, "vi")

        self.assertIs(result, forecast)
        self.assertFalse(cache_hit)
        mock_fetch.assert_called_once_with("Da Nang", 3, language="vi")
        cache_key = mock_cache_get.call_args.args[0]
        mock_cache_set.assert_called_once_with(cache_key, forecast, timeout=900)

    @patch("services.weather.cache.set")
    @patch("services.weather.cache.get")
    @patch("services.weather.fetch_forecast")
    def test_cache_hit_does_not_call_provider(
        self, mock_fetch, mock_cache_get, mock_cache_set
    ):
        cached = {"provider": "weatherapi", "forecast": []}
        mock_cache_get.return_value = cached

        with self.app.app_context():
            result, cache_hit = get_cached_forecast("Da Nang", 3)

        self.assertIs(result, cached)
        self.assertTrue(cache_hit)
        mock_fetch.assert_not_called()
        mock_cache_set.assert_not_called()

    def test_provider_errors_are_never_cached(self):
        errors = [
            WeatherServiceTimeout(),
            WeatherLocationNotFound(),
            WeatherProviderAuthenticationError(),
            WeatherProviderQuotaExceeded(),
            WeatherProviderResponseError(),
            WeatherServiceError(),
        ]

        for error in errors:
            with self.subTest(error=type(error).__name__):
                with (
                    patch("services.weather.cache.get", return_value=None),
                    patch("services.weather.cache.set") as mock_cache_set,
                    patch("services.weather.fetch_forecast", side_effect=error),
                    self.app.app_context(),
                ):
                    with self.assertRaises(type(error)):
                        get_cached_forecast("Da Nang", 3)
                mock_cache_set.assert_not_called()


class WeatherCacheTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            WEATHER_API_KEY="test-weather-key",
            WEATHER_CACHE_TTL_SECONDS=900,
        )
        with self.app.app_context():
            cache.clear()

    def tearDown(self):
        with self.app.app_context():
            cache.clear()

    @patch("services.weather.fetch_forecast")
    def test_first_call_misses_and_second_call_hits_same_cache(self, mock_fetch):
        forecast = {"provider": "weatherapi", "forecast": []}
        mock_fetch.return_value = forecast

        with self.app.app_context():
            first_result, first_hit = get_cached_forecast("Hanoi", 3, "vi")
            second_result, second_hit = get_cached_forecast("Hanoi", 3, "vi")

        self.assertFalse(first_hit)
        self.assertTrue(second_hit)
        self.assertEqual(first_result, forecast)
        self.assertEqual(second_result, forecast)
        mock_fetch.assert_called_once_with("Hanoi", 3, language="vi")

    def test_query_variants_create_same_cache_key(self):
        keys = {
            build_weather_cache_key("Hanoi", 3, "vi"),
            build_weather_cache_key(" hanoi ", 3, "vi"),
            build_weather_cache_key("HANOI", 3, "vi"),
        }
        self.assertEqual(len(keys), 1)

    def test_different_days_create_different_cache_keys(self):
        self.assertNotEqual(
            build_weather_cache_key("Hanoi", 2, "vi"),
            build_weather_cache_key("Hanoi", 3, "vi"),
        )

    def test_different_languages_create_different_cache_keys(self):
        self.assertNotEqual(
            build_weather_cache_key("Hanoi", 3, "vi"),
            build_weather_cache_key("Hanoi", 3, "en"),
        )

    def test_cache_key_contains_schema_version_v1(self):
        key = build_weather_cache_key("Hanoi", 3, "vi")
        self.assertIn(":forecast:v1:", key)

    @patch("services.weather.fetch_forecast")
    def test_timeout_is_not_cached_and_next_call_retries(self, mock_fetch):
        forecast = {"provider": "weatherapi", "forecast": []}
        mock_fetch.side_effect = [WeatherServiceTimeout(), forecast]

        with self.app.app_context():
            with self.assertRaises(WeatherServiceTimeout):
                get_cached_forecast("Hanoi", 3, "vi")
            result, second_hit = get_cached_forecast("Hanoi", 3, "vi")
            cached_result, third_hit = get_cached_forecast("Hanoi", 3, "vi")

        self.assertFalse(second_hit)
        self.assertTrue(third_hit)
        self.assertEqual(result, forecast)
        self.assertEqual(cached_result, forecast)
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("services.weather.fetch_forecast")
    def test_trip_filter_does_not_mutate_shared_cached_forecast(self, mock_fetch):
        today = date.today()
        full_forecast = {
            "provider": "weatherapi",
            "forecast": [
                {"date": (today + timedelta(days=offset)).isoformat()}
                for offset in range(4)
            ],
        }
        mock_fetch.return_value = full_forecast
        trip = SimpleNamespace(
            destination="Hanoi",
            coordinates=None,
            start_date=today + timedelta(days=1),
            end_date=today + timedelta(days=2),
            itinerary=[],
        )

        with self.app.app_context():
            initial, initial_hit = get_cached_forecast("Hanoi", 3, "vi")
            trip_weather, _, trip_cache_hit = get_forecast_for_trip(
                trip, today=today
            )
            cached_again, final_hit = get_cached_forecast("Hanoi", 3, "vi")

        self.assertFalse(initial_hit)
        self.assertTrue(trip_cache_hit)
        self.assertTrue(final_hit)
        self.assertEqual(len(initial["forecast"]), 4)
        self.assertEqual(len(trip_weather["forecast"]), 2)
        self.assertEqual(len(cached_again["forecast"]), 4)
        mock_fetch.assert_called_once()


class TripForecastLogicTestCase(unittest.TestCase):
    TODAY = date(2026, 7, 25)

    @staticmethod
    def trip(**overrides):
        values = {
            "destination": "Da Nang",
            "coordinates": None,
            "start_date": date(2026, 7, 25),
            "end_date": date(2026, 7, 27),
            "itinerary": [{"day": 1}],
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_weather_query_prefers_coordinates(self):
        trip = self.trip(
            destination="Ignored destination",
            coordinates={"lat": 16.0471234, "lng": 108.2068123},
        )
        self.assertEqual(get_trip_weather_query(trip), "16.04712,108.20681")

    def test_zero_coordinates_are_valid(self):
        trip = self.trip(coordinates={"lat": 0, "lng": 0})
        self.assertEqual(get_trip_weather_query(trip), "0,0")

    def test_weather_query_falls_back_to_stripped_destination(self):
        trip = self.trip(
            destination="  Da Nang, Vietnam  ",
            coordinates={"lat": 16.0},
        )
        self.assertEqual(get_trip_weather_query(trip), "Da Nang, Vietnam")

    def test_weather_query_requires_location(self):
        trip = self.trip(destination=" ", coordinates={"lat": None, "lng": None})
        with self.assertRaises(ValueError):
            get_trip_weather_query(trip)

    def test_past_trip_raises(self):
        with self.assertRaises(TripInPastError):
            calculate_forecast_coverage(
                self.TODAY - timedelta(days=5),
                self.TODAY - timedelta(days=1),
                today=self.TODAY,
            )

    def test_ongoing_trip_starts_coverage_today(self):
        coverage = calculate_forecast_coverage(
            self.TODAY - timedelta(days=2),
            self.TODAY + timedelta(days=2),
            today=self.TODAY,
        )
        self.assertEqual(coverage.requested_from, self.TODAY)
        self.assertEqual(coverage.requested_through, self.TODAY + timedelta(days=2))
        self.assertEqual(coverage.provider_days, 3)
        self.assertFalse(coverage.complete)

    def test_trip_fully_covered_within_fourteen_days(self):
        coverage = calculate_forecast_coverage(
            self.TODAY + timedelta(days=2),
            self.TODAY + timedelta(days=5),
            today=self.TODAY,
        )
        self.assertEqual(coverage.available_through, self.TODAY + timedelta(days=5))
        self.assertEqual(coverage.provider_days, 6)
        self.assertTrue(coverage.complete)

    def test_trip_is_partially_covered_at_provider_boundary(self):
        coverage = calculate_forecast_coverage(
            self.TODAY + timedelta(days=10),
            self.TODAY + timedelta(days=20),
            today=self.TODAY,
        )
        self.assertEqual(coverage.requested_from, self.TODAY + timedelta(days=10))
        self.assertEqual(coverage.available_through, self.TODAY + timedelta(days=13))
        self.assertEqual(coverage.provider_days, 14)
        self.assertFalse(coverage.complete)

    def test_trip_too_far_raises_with_availability_date(self):
        trip_start = self.TODAY + timedelta(days=14)
        with self.assertRaises(ForecastNotAvailableYetError) as context:
            calculate_forecast_coverage(
                trip_start,
                trip_start + timedelta(days=2),
                today=self.TODAY,
            )
        self.assertEqual(
            context.exception.forecast_available_from,
            self.TODAY + timedelta(days=1),
        )

    def test_provider_days_count_from_today_through_available_date(self):
        coverage = calculate_forecast_coverage(
            self.TODAY + timedelta(days=10),
            self.TODAY + timedelta(days=12),
            today=self.TODAY,
        )
        self.assertEqual(coverage.provider_days, 13)

    @patch("services.weather.get_cached_forecast")
    def test_get_forecast_for_trip_filters_days_without_mutating_trip(
        self, mock_get_cached
    ):
        trip = self.trip(
            coordinates={"lat": 0, "lng": 108.2},
            start_date=self.TODAY + timedelta(days=2),
            end_date=self.TODAY + timedelta(days=4),
        )
        original_itinerary = list(trip.itinerary)
        cached_weather = {
            "provider": "weatherapi",
            "forecast": [
                {"date": (self.TODAY + timedelta(days=1)).isoformat()},
                {"date": (self.TODAY + timedelta(days=2)).isoformat()},
                {"date": (self.TODAY + timedelta(days=4)).isoformat()},
                {"date": (self.TODAY + timedelta(days=5)).isoformat()},
            ],
        }
        mock_get_cached.return_value = (cached_weather, True)

        weather, coverage, cache_hit = get_forecast_for_trip(
            trip, today=self.TODAY
        )

        mock_get_cached.assert_called_once_with("0,108.2", 5)
        self.assertEqual(
            [item["date"] for item in weather["forecast"]],
            [
                (self.TODAY + timedelta(days=2)).isoformat(),
                (self.TODAY + timedelta(days=4)).isoformat(),
            ],
        )
        self.assertTrue(coverage.complete)
        self.assertTrue(cache_hit)
        self.assertEqual(trip.itinerary, original_itinerary)
        self.assertEqual(len(cached_weather["forecast"]), 4)


if __name__ == "__main__":
    unittest.main()
