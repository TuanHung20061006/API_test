import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["FLASK_ENV"] = "development"
os.environ["RATELIMIT_ENABLED"] = "false"

from app import create_app
from extensions import db
from services.weather import (
    ForecastCoverage,
    ForecastNotAvailableYetError,
    TripInPastError,
    WeatherLocationNotFound,
    WeatherProviderQuotaExceeded,
    WeatherProviderResponseError,
    WeatherServiceError,
    WeatherServiceNotConfigured,
    WeatherServiceTimeout,
)


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def register(self, email="user@example.com", password="test1234"):
        return self.client.post(
            "/auth/register", json={"email": email, "password": password}
        )

    def create_trip(self, token, destination="Da Nang"):
        start_date = date.today() + timedelta(days=1)
        end_date = start_date + timedelta(days=2)
        return self.client.post(
            "/trip",
            headers=self.auth(token),
            json={
                "destination": destination,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )

    @staticmethod
    def auth(token):
        return {"Authorization": f"Bearer {token}"}

    def test_health_checks_database(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(), {"status": "healthy", "database": "available"}
        )

    def test_registration_login_me_and_refresh(self):
        response = self.register(email="User@Example.com")
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["user"]["email"], "user@example.com")

        me = self.client.get("/auth/me", headers=self.auth(payload["access_token"]))
        self.assertEqual(me.status_code, 200)

        refresh = self.client.post(
            "/auth/refresh", headers=self.auth(payload["refresh_token"])
        )
        self.assertEqual(refresh.status_code, 200)
        self.assertIn("access_token", refresh.get_json())

        wrong_token_type = self.client.post(
            "/auth/refresh", headers=self.auth(payload["access_token"])
        )
        self.assertEqual(wrong_token_type.status_code, 401)

        login = self.client.post(
            "/auth/login",
            json={"email": "USER@example.com", "password": "test1234"},
        )
        self.assertEqual(login.status_code, 200)

    def test_registration_validation_and_duplicate(self):
        self.assertEqual(
            self.register(email="invalid", password="short").status_code, 400
        )
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(self.register().status_code, 409)
        self.assertEqual(
            self.register(email="long@example.com", password="é" * 40).status_code,
            400,
        )

    def test_trip_crud_validation_and_itinerary_regeneration(self):
        token = self.register().get_json()["access_token"]
        headers = self.auth(token)

        invalid = self.client.post(
            "/trip",
            headers=headers,
            json={
                "destination": "Da Nang",
                "start_date": "2026-08-03",
                "end_date": "2026-08-01",
                "coordinates": {"lat": 200, "lng": 108},
            },
        )
        self.assertEqual(invalid.status_code, 400)

        created = self.client.post(
            "/trip",
            headers=headers,
            json={
                "destination": "Da Nang",
                "start_date": "2026-08-01",
                "end_date": "2026-08-03",
                "coordinates": {"lat": 16.0471, "lng": 108.2068},
            },
        )
        self.assertEqual(created.status_code, 201)
        trip = created.get_json()["trip"]
        self.assertEqual(len(trip["itinerary"]), 3)

        updated = self.client.patch(
            f"/trip/{trip['id']}",
            headers=headers,
            json={"destination": "Hoi An", "end_date": "2026-08-02"},
        )
        self.assertEqual(updated.status_code, 200)
        updated_trip = updated.get_json()["trip"]
        self.assertEqual(len(updated_trip["itinerary"]), 2)
        self.assertIn("Hoi An", updated_trip["itinerary"][0]["title"])

        incomplete_put = self.client.put(
            f"/trip/{trip['id']}", headers=headers, json={"destination": "Hue"}
        )
        self.assertEqual(incomplete_put.status_code, 400)

        listed = self.client.get("/trip", headers=headers)
        self.assertEqual(len(listed.get_json()["trips"]), 1)

        deleted = self.client.delete(f"/trip/{trip['id']}", headers=headers)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(
            self.client.get(f"/trip/{trip['id']}", headers=headers).status_code,
            404,
        )

    def test_trips_are_isolated_by_user(self):
        first = self.register(email="first@example.com").get_json()
        created = self.client.post(
            "/trip",
            headers=self.auth(first["access_token"]),
            json={
                "destination": "Hue",
                "start_date": "2026-09-01",
                "end_date": "2026-09-02",
            },
        ).get_json()["trip"]

        second = self.register(email="second@example.com").get_json()
        response = self.client.get(
            f"/trip/{created['id']}", headers=self.auth(second["access_token"])
        )
        self.assertEqual(response.status_code, 404)

    def test_protected_route_requires_token(self):
        response = self.client.get("/trip")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"error": "Authentication required."})

    @patch("routes.weather.get_forecast_for_trip")
    def test_trip_weather_returns_normalized_weather_and_coverage(
        self, mock_get_weather
    ):
        token = self.register().get_json()["access_token"]
        trip = self.create_trip(token).get_json()["trip"]
        trip_start = date.fromisoformat(trip["start_date"])
        trip_end = date.fromisoformat(trip["end_date"])
        mock_get_weather.return_value = (
            {
                "provider": "weatherapi",
                "location": {"name": "Da Nang"},
                "current": {"temperature_c": 31.0},
                "forecast": [{"date": trip["start_date"]}],
                "alerts": [],
            },
            ForecastCoverage(
                requested_from=trip_start,
                requested_through=trip_end,
                available_through=trip_end,
                provider_days=4,
                complete=True,
            ),
            False,
        )

        response = self.client.get(
            f"/trip/{trip['id']}/weather", headers=self.auth(token)
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            payload["trip"],
            {
                "id": trip["id"],
                "destination": "Da Nang",
                "start_date": trip["start_date"],
                "end_date": trip["end_date"],
            },
        )
        self.assertEqual(
            payload["coverage"],
            {
                "requested_from": trip["start_date"],
                "requested_through": trip["end_date"],
                "available_through": trip["end_date"],
                "complete": True,
            },
        )
        self.assertEqual(payload["weather"]["provider"], "weatherapi")
        self.assertFalse(payload["meta"]["cached"])
        mock_get_weather.assert_called_once()

    @patch("routes.weather.get_forecast_for_trip")
    def test_trip_weather_hides_other_users_trip(self, mock_get_weather):
        first_token = self.register(email="owner@example.com").get_json()[
            "access_token"
        ]
        trip = self.create_trip(first_token).get_json()["trip"]
        second_token = self.register(email="other@example.com").get_json()[
            "access_token"
        ]

        response = self.client.get(
            f"/trip/{trip['id']}/weather", headers=self.auth(second_token)
        )

        self.assertEqual(response.status_code, 404)
        mock_get_weather.assert_not_called()

    def test_trip_weather_requires_jwt(self):
        response = self.client.get("/trip/1/weather")
        self.assertEqual(response.status_code, 401)

    @patch("routes.weather.get_forecast_for_trip")
    def test_trip_weather_returns_404_for_missing_trip(self, mock_get_weather):
        token = self.register().get_json()["access_token"]
        response = self.client.get("/trip/999999/weather", headers=self.auth(token))
        self.assertEqual(response.status_code, 404)
        mock_get_weather.assert_not_called()

    def assert_trip_weather_error(self, error, expected_status, expected_code):
        token = self.register().get_json()["access_token"]
        trip = self.create_trip(token).get_json()["trip"]
        with patch("routes.weather.get_forecast_for_trip", side_effect=error):
            response = self.client.get(
                f"/trip/{trip['id']}/weather",
                headers=self.auth(token),
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, expected_status)
        self.assertEqual(payload["error"]["code"], expected_code)
        self.assertNotIn("provider detail", payload["error"]["message"])
        self.assertNotIn("internal detail", payload["error"]["message"])
        return payload

    def test_trip_weather_maps_past_trip(self):
        self.assert_trip_weather_error(
            TripInPastError(),
            422,
            "TRIP_IS_IN_THE_PAST",
        )

    def test_trip_weather_maps_forecast_not_available_yet(self):
        available_from = date.today() + timedelta(days=1)
        payload = self.assert_trip_weather_error(
            ForecastNotAvailableYetError(available_from),
            422,
            "FORECAST_NOT_AVAILABLE_YET",
        )
        self.assertEqual(
            payload["forecast_available_from"],
            available_from.isoformat(),
        )

    def test_trip_weather_maps_location_not_found(self):
        self.assert_trip_weather_error(
            WeatherLocationNotFound(),
            422,
            "WEATHER_LOCATION_NOT_FOUND",
        )

    def test_trip_weather_maps_timeout(self):
        self.assert_trip_weather_error(
            WeatherServiceTimeout(),
            504,
            "WEATHER_SERVICE_TIMEOUT",
        )

    def test_trip_weather_maps_not_configured(self):
        self.assert_trip_weather_error(
            WeatherServiceNotConfigured(),
            503,
            "WEATHER_SERVICE_NOT_CONFIGURED",
        )

    def test_trip_weather_maps_quota_exceeded(self):
        self.assert_trip_weather_error(
            WeatherProviderQuotaExceeded(),
            503,
            "WEATHER_PROVIDER_QUOTA_EXCEEDED",
        )

    def test_trip_weather_maps_provider_response_error(self):
        self.assert_trip_weather_error(
            WeatherProviderResponseError("provider detail"),
            503,
            "WEATHER_SERVICE_UNAVAILABLE",
        )

    @patch("routes.weather.get_forecast_for_trip")
    def test_trip_weather_returns_partial_coverage(self, mock_get_weather):
        token = self.register().get_json()["access_token"]
        trip = self.create_trip(token).get_json()["trip"]
        trip_start = date.fromisoformat(trip["start_date"])
        trip_end = date.fromisoformat(trip["end_date"])
        mock_get_weather.return_value = (
            {
                "provider": "weatherapi",
                "forecast": [{"date": trip["start_date"]}],
            },
            ForecastCoverage(
                requested_from=trip_start,
                requested_through=trip_end,
                available_through=trip_start,
                provider_days=2,
                complete=False,
            ),
            True,
        )

        response = self.client.get(
            f"/trip/{trip['id']}/weather",
            headers=self.auth(token),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["coverage"]["complete"])
        self.assertTrue(response.get_json()["meta"]["cached"])


if __name__ == "__main__":
    unittest.main()
