import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["FLASK_ENV"] = "development"

from app import create_app
from config import DevelopmentConfig
from extensions import db, limiter
from services.weather import ForecastCoverage


class WeatherRateLimitTestCase(unittest.TestCase):
    def setUp(self):
        self.previous_enabled = DevelopmentConfig.RATELIMIT_ENABLED
        self.previous_storage_uri = DevelopmentConfig.RATELIMIT_STORAGE_URI
        self.previous_weather_limit = DevelopmentConfig.WEATHER_RATE_LIMIT
        self.previous_limiter_enabled = limiter.enabled
        DevelopmentConfig.RATELIMIT_ENABLED = (
            self._testMethodName != "test_weather_limit_can_be_disabled"
        )
        DevelopmentConfig.RATELIMIT_STORAGE_URI = "memory://"
        DevelopmentConfig.WEATHER_RATE_LIMIT = "2 per minute"

        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        limiter.storage.reset()

        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        limiter.storage.reset()
        DevelopmentConfig.RATELIMIT_ENABLED = self.previous_enabled
        DevelopmentConfig.RATELIMIT_STORAGE_URI = self.previous_storage_uri
        DevelopmentConfig.WEATHER_RATE_LIMIT = self.previous_weather_limit
        limiter._enabled = self.previous_limiter_enabled

    def register(self, email):
        response = self.client.post(
            "/auth/register",
            json={"email": email, "password": "test1234"},
        )
        return response.get_json()["access_token"]

    @staticmethod
    def auth(token):
        return {"Authorization": f"Bearer {token}"}

    def create_trip(self, token, destination):
        start_date = date.today() + timedelta(days=1)
        end_date = start_date + timedelta(days=1)
        response = self.client.post(
            "/trip",
            headers=self.auth(token),
            json={
                "destination": destination,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )
        return response.get_json()["trip"]

    @staticmethod
    def configure_weather_result(mock_get_weather, trip, cached=True):
        start_date = date.fromisoformat(trip["start_date"])
        end_date = date.fromisoformat(trip["end_date"])
        mock_get_weather.return_value = (
            {
                "provider": "weatherapi",
                "forecast": [{"date": trip["start_date"]}],
            },
            ForecastCoverage(
                requested_from=start_date,
                requested_through=end_date,
                available_through=end_date,
                provider_days=3,
                complete=True,
            ),
            cached,
        )

    @patch("routes.weather.get_forecast_for_trip")
    def test_one_user_receives_json_429_after_two_requests(self, mock_get_weather):
        token = self.register("rate-single@example.com")
        trip = self.create_trip(token, "Hanoi")
        self.configure_weather_result(mock_get_weather, trip, cached=False)

        responses = [
            self.client.get(
                f"/trip/{trip['id']}/weather",
                headers=self.auth(token),
            )
            for _ in range(3)
        ]

        self.assertEqual([response.status_code for response in responses], [200, 200, 429])
        limited_response = responses[2]
        self.assertTrue(limited_response.is_json)
        self.assertEqual(limited_response.mimetype, "application/json")
        self.assertEqual(
            limited_response.get_json()["error"]["code"],
            "RATE_LIMIT_EXCEEDED",
        )
        if "Retry-After" in limited_response.headers:
            self.assertGreaterEqual(int(limited_response.headers["Retry-After"]), 0)
        self.assertEqual(mock_get_weather.call_count, 2)

    @patch("routes.weather.get_forecast_for_trip")
    def test_two_users_have_independent_rate_limit_buckets(self, mock_get_weather):
        first_token = self.register("rate-first@example.com")
        second_token = self.register("rate-second@example.com")
        first_trip = self.create_trip(first_token, "Hanoi")
        second_trip = self.create_trip(second_token, "Hanoi")
        self.configure_weather_result(mock_get_weather, first_trip, cached=False)

        first_user_responses = [
            self.client.get(
                f"/trip/{first_trip['id']}/weather",
                headers=self.auth(first_token),
            )
            for _ in range(2)
        ]
        second_user_response = self.client.get(
            f"/trip/{second_trip['id']}/weather",
            headers=self.auth(second_token),
        )

        self.assertEqual(
            [response.status_code for response in first_user_responses],
            [200, 200],
        )
        self.assertEqual(second_user_response.status_code, 200)
        self.assertEqual(mock_get_weather.call_count, 3)

    @patch("routes.weather.get_forecast_for_trip")
    def test_cache_hits_are_still_rate_limited(self, mock_get_weather):
        token = self.register("rate-cache@example.com")
        trip = self.create_trip(token, "Hanoi")
        self.configure_weather_result(mock_get_weather, trip, cached=True)

        responses = [
            self.client.get(
                f"/trip/{trip['id']}/weather",
                headers=self.auth(token),
            )
            for _ in range(3)
        ]

        self.assertTrue(responses[0].get_json()["meta"]["cached"])
        self.assertTrue(responses[1].get_json()["meta"]["cached"])
        self.assertEqual(responses[2].status_code, 429)
        self.assertEqual(mock_get_weather.call_count, 2)

    @patch("routes.weather.get_forecast_for_trip")
    def test_weather_limit_can_be_disabled(self, mock_get_weather):
        token = self.register("rate-disabled@example.com")
        trip = self.create_trip(token, "Hanoi")
        self.configure_weather_result(mock_get_weather, trip, cached=True)

        responses = [
            self.client.get(
                f"/trip/{trip['id']}/weather",
                headers=self.auth(token),
            )
            for _ in range(5)
        ]

        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(mock_get_weather.call_count, 5)

    def test_weather_limit_still_requires_jwt_before_key_lookup(self):
        response = self.client.get("/trip/1/weather")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
