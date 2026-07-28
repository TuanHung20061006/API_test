import copy
import json
import os
import unittest
from contextlib import contextmanager
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["FLASK_ENV"] = "development"
os.environ["RATELIMIT_ENABLED"] = "false"
os.environ["RATELIMIT_STORAGE_URI"] = "memory://"
os.environ["CACHE_TYPE"] = "SimpleCache"

from google.genai import errors as genai_errors

from app import create_app
from config import DevelopmentConfig
from extensions import cache, db, limiter
from models import Trip, User
from schemas.ai_advice import TripAIAdvice
from services.ai_advice import build_ai_advice_cache_key
from services.weather import ForecastCoverage, WeatherServiceTimeout
from utils.jwt import generate_access_token


TEST_API_KEY = "test-gemini-key-not-real"
TEST_MODEL = "gemini-3.6-flash"
_TRIP_DATA_START = "<TRIP_DATA_JSON>\n"
_TRIP_DATA_END = "\n</TRIP_DATA_JSON>"


def _advice_data(
    *,
    score=8,
    summary="Kế hoạch phù hợp.",
):
    return {
        "summary": summary,
        "overall_score": score,
        "strengths": ["Lịch trình có nhịp độ hợp lý."],
        "issues": [],
        "recommendations": [],
        "weather_advice": [],
        "packing_list": ["Giày đi bộ"],
        "disclaimer": "Đề xuất do AI tạo và cần được kiểm tra.",
    }


def _advice_json(**overrides):
    return json.dumps(
        _advice_data(**overrides),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _provider_payload(interactions_call):
    provider_input = interactions_call.kwargs["input"]
    serialized = provider_input.split(_TRIP_DATA_START, 1)[1].split(
        _TRIP_DATA_END,
        1,
    )[0]
    return json.loads(serialized)


def _nested_keys(value):
    if isinstance(value, dict):
        keys = set(value)
        for nested in value.values():
            keys.update(_nested_keys(nested))
        return keys
    if isinstance(value, list):
        keys = set()
        for nested in value:
            keys.update(_nested_keys(nested))
        return keys
    return set()


class AIAdviceIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.previous_enabled = DevelopmentConfig.RATELIMIT_ENABLED
        self.previous_storage_uri = DevelopmentConfig.RATELIMIT_STORAGE_URI
        self.previous_limit = DevelopmentConfig.GEMINI_ADVICE_RATE_LIMIT
        self.previous_limiter_enabled = limiter.enabled
        DevelopmentConfig.RATELIMIT_ENABLED = True
        DevelopmentConfig.RATELIMIT_STORAGE_URI = "memory://"
        DevelopmentConfig.GEMINI_ADVICE_RATE_LIMIT = "1000 per hour"

        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            GEMINI_ENABLED=True,
            GEMINI_API_KEY=TEST_API_KEY,
            GEMINI_MODEL=TEST_MODEL,
            GEMINI_TIMEOUT_SECONDS=30,
            GEMINI_ADVICE_CACHE_TTL_SECONDS=3600,
            GEMINI_ADVICE_RATE_LIMIT="1000 per hour",
            GEMINI_ADVICE_MAX_REQUEST_BYTES=32768,
        )
        self.client = self.app.test_client()
        limiter.storage.reset()

        self.start_date = date.today() + timedelta(days=5)
        self.end_date = self.start_date + timedelta(days=2)
        with self.app.app_context():
            db.create_all()
            cache.clear()
            owner = User(
                email="integration-owner@example.com",
                password_hash="integration-password-hash",
            )
            db.session.add(owner)
            db.session.flush()
            trip = Trip(
                user_id=owner.id,
                destination="Da Nang",
                start_date=self.start_date,
                end_date=self.end_date,
                coordinates={"lat": 16.0471, "lng": 108.2068},
                itinerary=[
                    {
                        "day": 1,
                        "title": "Khám phá trung tâm",
                        "activities": ["Ăn sáng", "Tham quan bảo tàng"],
                    },
                    {
                        "day": 2,
                        "title": "Văn hóa và ẩm thực",
                        "activities": ["Chợ địa phương", "Phố cổ"],
                    },
                    {
                        "day": 3,
                        "title": "Nghỉ ngơi",
                        "activities": ["Cà phê", "Mua quà"],
                    },
                ],
            )
            db.session.add(trip)
            db.session.commit()
            self.user_id = owner.id
            self.trip_id = trip.id
            self.token = generate_access_token(
                owner.id,
                {"email": owner.email},
            )

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
            cache.clear()
        limiter.storage.reset()
        DevelopmentConfig.RATELIMIT_ENABLED = self.previous_enabled
        DevelopmentConfig.RATELIMIT_STORAGE_URI = self.previous_storage_uri
        DevelopmentConfig.GEMINI_ADVICE_RATE_LIMIT = self.previous_limit
        limiter._enabled = self.previous_limiter_enabled

    @staticmethod
    def auth(token):
        return {"Authorization": f"Bearer {token}"}

    def url(self, trip_id=None):
        return f"/trip/{trip_id or self.trip_id}/ai-advice"

    def request_body(self, **overrides):
        body = {
            "mode": "review",
            "language": "vi",
            "include_weather": True,
            "regenerate": False,
            "preferences": {
                "budget": "medium",
                "pace": "relaxed",
                "interests": ["food", "culture"],
                "transport": "motorbike",
                "dietary_requirements": [],
                "notes": "Không muốn di chuyển quá nhiều.",
            },
        }
        body.update(overrides)
        return body

    def post(self, body=None, *, token=None, trip_id=None):
        return self.client.post(
            self.url(trip_id),
            headers=self.auth(token or self.token),
            json=self.request_body() if body is None else body,
        )

    def normalized_weather(self, *, marker="first"):
        return {
            "provider": "weatherapi",
            "location": {
                "name": "Da Nang",
                "country": "Vietnam",
            },
            "current": {
                "temperature_c": 30.0,
                "marker": marker,
            },
            "forecast": [
                {
                    "date": (
                        self.start_date + timedelta(days=offset)
                    ).isoformat(),
                    "temperature": {
                        "min_c": 25.0 + offset,
                        "max_c": 31.0 + offset,
                    },
                }
                for offset in range(3)
            ],
            "alerts": [],
        }

    def coverage(self, *, complete=True):
        return ForecastCoverage(
            requested_from=self.start_date,
            requested_through=self.end_date,
            available_through=(
                self.end_date
                if complete
                else self.start_date + timedelta(days=1)
            ),
            provider_days=3,
            complete=complete,
        )

    @contextmanager
    def boundaries(self, *, output_text=None):
        client = MagicMock()
        client.interactions.create.return_value = SimpleNamespace(
            output_text=output_text or _advice_json()
        )
        with (
            patch(
                "services.ai_advice.get_forecast_for_trip",
                return_value=(
                    self.normalized_weather(),
                    self.coverage(),
                    False,
                ),
            ) as weather_mock,
            patch(
                "services.gemini.genai.Client",
                return_value=client,
            ) as client_class,
        ):
            yield SimpleNamespace(
                client=client,
                client_class=client_class,
                weather=weather_mock,
            )

    def create_other_user_token(self):
        with self.app.app_context():
            user = User(
                email="integration-other@example.com",
                password_hash="other-password-hash",
            )
            db.session.add(user)
            db.session.commit()
            return generate_access_token(
                user.id,
                {"email": user.email},
            )

    def assert_response_is_safe(self, response, *, raw_detail=None):
        serialized = response.get_data(as_text=True)
        for forbidden in (
            TEST_API_KEY,
            "Bearer",
            "SYSTEM_INSTRUCTION",
            "TRIP_DATA_JSON",
            "ai-advice:v1:",
            "password_hash",
        ):
            self.assertNotIn(forbidden, serialized)
        for field in (
            "user_id",
            "email",
            "token",
            "api_key",
            "cache_key",
            "provider_response",
            "raw_weather",
        ):
            self.assertNotIn(f'"{field}"', serialized)
        if raw_detail is not None:
            self.assertNotIn(raw_detail, serialized)

    def test_full_success_runs_every_layer_and_sends_safe_provider_payload(
        self,
    ):
        body = self.request_body()
        destination = "Da Nang"
        notes = body["preferences"]["notes"]

        with self.boundaries() as boundary:
            response = self.post(body)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["trip_id"], self.trip_id)
        self.assertEqual(payload["mode"], "review")
        self.assertEqual(payload["advice"], _advice_data())
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(
            payload["meta"],
            {"cache_hit": False, "weather_cache_hit": False},
        )
        boundary.weather.assert_called_once()
        weather_trip = boundary.weather.call_args.args[0]
        self.assertEqual(weather_trip.id, self.trip_id)

        boundary.client_class.assert_called_once()
        client_kwargs = boundary.client_class.call_args.kwargs
        self.assertEqual(client_kwargs["api_key"], TEST_API_KEY)
        boundary.client.interactions.create.assert_called_once()
        interaction_kwargs = (
            boundary.client.interactions.create.call_args.kwargs
        )
        self.assertEqual(interaction_kwargs["model"], TEST_MODEL)
        self.assertIs(interaction_kwargs["store"], False)
        self.assertTrue(interaction_kwargs["system_instruction"])
        self.assertNotIn(
            destination,
            interaction_kwargs["system_instruction"],
        )
        self.assertNotIn(notes, interaction_kwargs["system_instruction"])
        self.assertEqual(
            interaction_kwargs["response_format"]["schema"],
            TripAIAdvice.model_json_schema(),
        )
        self.assertEqual(
            interaction_kwargs["response_format"]["mime_type"],
            "application/json",
        )

        provider_payload = _provider_payload(
            boundary.client.interactions.create.call_args
        )
        self.assertEqual(provider_payload["language"], "vi")
        self.assertEqual(
            provider_payload["trip"],
            {
                "destination": destination,
                "start_date": self.start_date.isoformat(),
                "end_date": self.end_date.isoformat(),
            },
        )
        self.assertEqual(len(provider_payload["itinerary"]), 3)
        self.assertEqual(
            provider_payload["weather"]["status"],
            "available",
        )
        self.assertEqual(
            provider_payload["weather"]["coverage"][
                "requested_from"
            ],
            self.start_date.isoformat(),
        )
        self.assertEqual(
            provider_payload["preferences"],
            body["preferences"],
        )
        forbidden_keys = {
            "user_id",
            "email",
            "password",
            "password_hash",
            "jwt",
            "api_key",
            "trip_id",
            "created_at",
            "updated_at",
            "coordinates",
            "regenerate",
            "mode",
        }
        self.assertTrue(
            forbidden_keys.isdisjoint(_nested_keys(provider_payload))
        )
        serialized_provider = json.dumps(
            provider_payload,
            ensure_ascii=False,
        )
        self.assertNotIn(TEST_API_KEY, serialized_provider)
        self.assertNotIn(self.token, serialized_provider)
        boundary.client.close.assert_called_once_with()
        self.assert_response_is_safe(response)

    def test_weather_not_requested_skips_boundary_and_returns_null_meta(self):
        with self.boundaries() as boundary:
            response = self.post(
                self.request_body(include_weather=False)
            )

        self.assertEqual(response.status_code, 200)
        boundary.weather.assert_not_called()
        boundary.client.interactions.create.assert_called_once()
        provider_payload = _provider_payload(
            boundary.client.interactions.create.call_args
        )
        self.assertEqual(
            provider_payload["weather"],
            {
                "status": "not_requested",
                "data": None,
                "coverage": None,
            },
        )
        self.assertEqual(response.get_json()["warnings"], [])
        self.assertIsNone(
            response.get_json()["meta"]["weather_cache_hit"]
        )

    def test_partial_weather_remains_available_and_returns_warning(self):
        with self.boundaries() as boundary:
            boundary.weather.return_value = (
                self.normalized_weather(),
                self.coverage(complete=False),
                True,
            )
            response = self.post()

        self.assertEqual(response.status_code, 200)
        provider_payload = _provider_payload(
            boundary.client.interactions.create.call_args
        )
        self.assertEqual(
            provider_payload["weather"]["status"],
            "available",
        )
        self.assertIs(
            provider_payload["weather"]["coverage"]["complete"],
            False,
        )
        self.assertEqual(
            response.get_json()["warnings"],
            [
                {
                    "code": "WEATHER_PARTIAL_COVERAGE",
                    "message": (
                        "Weather forecast does not cover the entire trip."
                    ),
                }
            ],
        )
        self.assertIs(
            response.get_json()["meta"]["weather_cache_hit"],
            True,
        )

    def test_expected_weather_failure_falls_back_but_gemini_still_runs(self):
        raw_detail = "raw-weather-provider-detail"
        with self.boundaries() as boundary:
            boundary.weather.side_effect = WeatherServiceTimeout(raw_detail)
            response = self.post()

        self.assertEqual(response.status_code, 200)
        boundary.client.interactions.create.assert_called_once()
        provider_payload = _provider_payload(
            boundary.client.interactions.create.call_args
        )
        self.assertEqual(
            provider_payload["weather"],
            {
                "status": "unavailable",
                "data": None,
                "coverage": None,
            },
        )
        self.assertEqual(
            response.get_json()["warnings"][0]["code"],
            "WEATHER_UNAVAILABLE",
        )
        self.assertIsNone(
            response.get_json()["meta"]["weather_cache_hit"]
        )
        self.assert_response_is_safe(response, raw_detail=raw_detail)

    def test_unexpected_weather_programming_error_is_not_fallback(self):
        raw_detail = "weather-programming-bug"
        with self.boundaries() as boundary:
            boundary.weather.side_effect = AttributeError(raw_detail)
            response = self.post()

        self.assertEqual(response.status_code, 500)
        boundary.client_class.assert_not_called()
        boundary.client.interactions.create.assert_not_called()
        self.assertEqual(
            response.get_json(),
            {"error": "An internal server error occurred."},
        )
        self.assert_response_is_safe(response, raw_detail=raw_detail)

    def test_second_identical_http_request_hits_real_ai_cache(self):
        with self.boundaries() as boundary:
            first = self.post()
            second = self.post()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertIs(first.get_json()["meta"]["cache_hit"], False)
        self.assertIs(second.get_json()["meta"]["cache_hit"], True)
        self.assertEqual(
            second.get_json()["advice"],
            first.get_json()["advice"],
        )
        self.assertEqual(boundary.weather.call_count, 2)
        boundary.client_class.assert_called_once()
        boundary.client.interactions.create.assert_called_once()
        boundary.client.close.assert_called_once()
        self.assert_response_is_safe(second)

    def test_regenerate_calls_provider_again_and_overwrites_cache(self):
        first_interaction = SimpleNamespace(
            output_text=_advice_json(score=8, summary="Phiên bản đầu.")
        )
        regenerated_interaction = SimpleNamespace(
            output_text=_advice_json(score=9, summary="Phiên bản mới.")
        )
        with self.boundaries() as boundary:
            boundary.client.interactions.create.side_effect = [
                first_interaction,
                regenerated_interaction,
            ]
            first = self.post()
            regenerated = self.post(
                self.request_body(regenerate=True)
            )
            cached_new = self.post()

        self.assertEqual(first.get_json()["advice"]["overall_score"], 8)
        self.assertEqual(
            regenerated.get_json()["advice"]["overall_score"],
            9,
        )
        self.assertIs(regenerated.get_json()["meta"]["cache_hit"], False)
        self.assertEqual(
            cached_new.get_json()["advice"]["overall_score"],
            9,
        )
        self.assertIs(cached_new.get_json()["meta"]["cache_hit"], True)
        self.assertEqual(boundary.client_class.call_count, 2)
        self.assertEqual(boundary.client.interactions.create.call_count, 2)
        self.assertEqual(boundary.client.close.call_count, 2)
        self.assertEqual(boundary.weather.call_count, 3)

    def test_failed_regenerate_preserves_previous_cached_advice(self):
        timeout_detail = "raw-regenerate-timeout"
        timeout_error = genai_errors.APIError(
            504,
            {"error": {"message": timeout_detail}},
        )
        with self.boundaries() as boundary:
            boundary.client.interactions.create.side_effect = [
                SimpleNamespace(output_text=_advice_json()),
                timeout_error,
            ]
            first = self.post()
            failed = self.post(
                self.request_body(regenerate=True)
            )
            cached = self.post()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(failed.status_code, 504)
        self.assertEqual(
            failed.get_json()["error"]["code"],
            "AI_PROVIDER_TIMEOUT",
        )
        self.assertEqual(cached.status_code, 200)
        self.assertIs(cached.get_json()["meta"]["cache_hit"], True)
        self.assertEqual(cached.get_json()["advice"], first.get_json()["advice"])
        self.assertEqual(boundary.client_class.call_count, 2)
        self.assertEqual(boundary.client.interactions.create.call_count, 2)
        self.assertEqual(boundary.client.close.call_count, 2)
        self.assert_response_is_safe(failed, raw_detail=timeout_detail)

    def test_trip_content_changes_invalidate_cache_without_updated_at(self):
        with self.boundaries() as boundary:
            first = self.post()
            with self.app.app_context():
                trip = db.session.get(Trip, self.trip_id)
                trip.itinerary = [
                    {
                        "day": 1,
                        "title": "Itinerary đã thay đổi",
                        "activities": ["Hoạt động mới"],
                    }
                ]
                db.session.commit()
            itinerary_changed = self.post()

            with self.app.app_context():
                trip = db.session.get(Trip, self.trip_id)
                trip.destination = "Hoi An"
                db.session.commit()
            destination_changed = self.post()

        for response in (first, itinerary_changed, destination_changed):
            self.assertEqual(response.status_code, 200)
            self.assertIs(response.get_json()["meta"]["cache_hit"], False)
        self.assertEqual(boundary.client_class.call_count, 3)
        calls = boundary.client.interactions.create.call_args_list
        first_payload = _provider_payload(calls[0])
        second_payload = _provider_payload(calls[1])
        third_payload = _provider_payload(calls[2])
        self.assertNotEqual(
            first_payload["itinerary"],
            second_payload["itinerary"],
        )
        self.assertEqual(
            second_payload["itinerary"][0]["title"],
            "Itinerary đã thay đổi",
        )
        self.assertEqual(third_payload["trip"]["destination"], "Hoi An")

    def test_request_weather_and_model_changes_each_miss_cache(self):
        with self.boundaries() as boundary:
            responses = [self.post()]

            language = self.request_body(language="en")
            responses.append(self.post(language))

            pace = self.request_body()
            pace["preferences"]["pace"] = "fast"
            responses.append(self.post(pace))

            interests = self.request_body()
            interests["preferences"]["interests"] = ["photography"]
            responses.append(self.post(interests))

            responses.append(
                self.post(self.request_body(include_weather=False))
            )

            boundary.weather.return_value = (
                self.normalized_weather(marker="changed"),
                self.coverage(),
                False,
            )
            responses.append(self.post())

            self.app.config["GEMINI_MODEL"] = "gemini-3.6-flash-alt"
            responses.append(self.post())

        self.assertEqual(len(responses), 7)
        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertIs(response.get_json()["meta"]["cache_hit"], False)
        self.assertEqual(boundary.client_class.call_count, 7)
        self.assertEqual(boundary.client.interactions.create.call_count, 7)
        models = [
            call.kwargs["model"]
            for call in boundary.client.interactions.create.call_args_list
        ]
        self.assertEqual(models[-1], "gemini-3.6-flash-alt")
        self.assertEqual(boundary.weather.call_count, 6)

    def test_semantically_equal_json_key_order_hits_cache(self):
        first_body = (
            '{"language":"vi","include_weather":false,'
            '"preferences":{"pace":"relaxed","budget":"medium"}}'
        )
        second_body = (
            '{"preferences":{"budget":"medium","pace":"relaxed"},'
            '"include_weather":false,"language":"vi"}'
        )

        with self.boundaries() as boundary:
            first = self.client.post(
                self.url(),
                headers=self.auth(self.token),
                data=first_body,
                content_type="application/json",
            )
            second = self.client.post(
                self.url(),
                headers=self.auth(self.token),
                data=second_body,
                content_type="application/json",
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertIs(first.get_json()["meta"]["cache_hit"], False)
        self.assertIs(second.get_json()["meta"]["cache_hit"], True)
        boundary.weather.assert_not_called()
        boundary.client.interactions.create.assert_called_once()

    def test_corrupted_real_cache_is_rejected_and_replaced(self):
        with self.boundaries() as boundary:
            first = self.post()
            provider_payload = _provider_payload(
                boundary.client.interactions.create.call_args
            )
            cache_key = build_ai_advice_cache_key(
                trip_id=self.trip_id,
                model=TEST_MODEL,
                payload=provider_payload,
            )
            with self.app.app_context():
                cache.set(
                    cache_key,
                    {"overall_score": "8"},
                    timeout=3600,
                )

            second = self.post()
            with self.app.app_context():
                repaired = cache.get(cache_key)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertIs(second.get_json()["meta"]["cache_hit"], False)
        self.assertEqual(boundary.client.interactions.create.call_count, 2)
        self.assertIsInstance(repaired, dict)
        self.assertEqual(repaired["overall_score"], 8)
        self.assertIn("summary", repaired)
        serialized = second.get_data(as_text=True)
        self.assertNotIn("validation", serialized.lower())

    def test_cache_backend_read_and_write_failures_do_not_break_success(self):
        with self.boundaries() as boundary:
            with patch(
                "services.ai_advice.cache.get",
                side_effect=RuntimeError("cache-read-detail"),
            ):
                read_failed = self.post()

            with self.app.app_context():
                cache.clear()
            changed_body = self.request_body(language="en")
            with patch(
                "services.ai_advice.cache.set",
                side_effect=RuntimeError("cache-write-detail"),
            ):
                write_failed = self.post(changed_body)

        for response in (read_failed, write_failed):
            self.assertEqual(response.status_code, 200)
            self.assertIs(response.get_json()["meta"]["cache_hit"], False)
            self.assert_response_is_safe(response)
        self.assertEqual(boundary.client.interactions.create.call_count, 2)

    def test_invalid_gemini_outputs_map_to_502_and_are_not_cached(self):
        invalid_outputs = [
            '{"malformed-output-marker":',
            json.dumps({"overall_score": 8}),
            _advice_json(score="8"),
            f"```json\n{_advice_json()}\n```",
        ]
        with self.boundaries() as boundary:
            boundary.client.interactions.create.side_effect = [
                SimpleNamespace(output_text=output)
                for output in invalid_outputs
            ]
            responses = [self.post() for _ in invalid_outputs]

            first_payload = _provider_payload(
                boundary.client.interactions.create.call_args_list[0]
            )
            cache_key = build_ai_advice_cache_key(
                trip_id=self.trip_id,
                model=TEST_MODEL,
                payload=first_payload,
            )
            with self.app.app_context():
                cached_value = cache.get(cache_key)

        for response, raw_output in zip(responses, invalid_outputs):
            with self.subTest(raw_output=raw_output[:20]):
                self.assertEqual(response.status_code, 502)
                self.assertEqual(
                    response.get_json()["error"]["code"],
                    "AI_INVALID_RESPONSE",
                )
                self.assertNotIn(
                    raw_output,
                    response.get_data(as_text=True),
                )
        self.assertIsNone(cached_value)
        self.assertEqual(boundary.client_class.call_count, 4)
        self.assertEqual(boundary.client.close.call_count, 4)

    def test_provider_status_errors_map_safely_through_http(self):
        raw_detail = "raw-provider-body"
        cases = [
            (
                genai_errors.APIError(
                    504,
                    {"error": {"message": raw_detail}},
                ),
                504,
                "AI_PROVIDER_TIMEOUT",
            ),
            (
                genai_errors.APIError(
                    429,
                    {"error": {"message": raw_detail}},
                ),
                503,
                "AI_PROVIDER_RATE_LIMITED",
            ),
            (
                genai_errors.APIError(
                    503,
                    {"error": {"message": raw_detail}},
                ),
                502,
                "AI_PROVIDER_UNAVAILABLE",
            ),
            (
                genai_errors.APIError(
                    401,
                    {"error": {"message": raw_detail}},
                ),
                502,
                "AI_PROVIDER_AUTHENTICATION_FAILED",
            ),
            (
                genai_errors.APIError(
                    403,
                    {"error": {"message": raw_detail}},
                ),
                502,
                "AI_PROVIDER_AUTHENTICATION_FAILED",
            ),
        ]
        with self.boundaries() as boundary:
            boundary.client.interactions.create.side_effect = [
                case[0] for case in cases
            ]
            responses = [self.post() for _ in cases]

        for response, (_, status, code) in zip(responses, cases):
            with self.subTest(code=code):
                self.assertEqual(response.status_code, status)
                self.assertEqual(
                    response.get_json()["error"]["code"],
                    code,
                )
                self.assert_response_is_safe(
                    response,
                    raw_detail=raw_detail,
                )
        self.assertEqual(boundary.client_class.call_count, len(cases))
        self.assertEqual(boundary.client.close.call_count, len(cases))

    def test_disabled_and_missing_key_stop_before_weather_cache_provider(self):
        with self.boundaries() as boundary:
            self.app.config.update(
                GEMINI_ENABLED=False,
                GEMINI_API_KEY="",
            )
            disabled = self.post()

            self.app.config.update(
                GEMINI_ENABLED=True,
                GEMINI_API_KEY="",
            )
            missing_key = self.post()

        self.assertEqual(disabled.status_code, 503)
        self.assertEqual(
            disabled.get_json()["error"]["code"],
            "AI_DISABLED",
        )
        self.assertEqual(missing_key.status_code, 503)
        self.assertEqual(
            missing_key.get_json()["error"]["code"],
            "AI_NOT_CONFIGURED",
        )
        boundary.weather.assert_not_called()
        boundary.client_class.assert_not_called()
        self.assert_response_is_safe(disabled)
        self.assert_response_is_safe(missing_key)

    def test_authentication_and_ownership_stop_before_network_boundaries(self):
        other_token = self.create_other_user_token()
        with self.boundaries() as boundary:
            missing_jwt = self.client.post(self.url(), json=self.request_body())
            other_user = self.post(token=other_token)
            missing_trip = self.post(trip_id=999999)

        self.assertEqual(missing_jwt.status_code, 401)
        self.assertEqual(other_user.status_code, 404)
        self.assertEqual(missing_trip.status_code, 404)
        self.assertEqual(other_user.get_json(), missing_trip.get_json())
        boundary.weather.assert_not_called()
        boundary.client_class.assert_not_called()

    def test_request_validation_stops_before_weather_and_gemini(self):
        secret_notes = "integration-secret-notes-" + ("x" * 1001)
        requests = [
            (
                "mode",
                lambda: self.post({"mode": "unsupported"}),
                400,
            ),
            (
                "strict_boolean",
                lambda: self.post({"include_weather": "true"}),
                400,
            ),
            (
                "notes",
                lambda: self.post(
                    {"preferences": {"notes": secret_notes}}
                ),
                400,
            ),
            (
                "oversize",
                lambda: self.client.post(
                    self.url(),
                    headers=self.auth(self.token),
                    data=b"{}" + (b" " * 32768),
                    content_type="application/json",
                ),
                413,
            ),
        ]

        with self.boundaries() as boundary:
            responses = [
                (name, request_call(), status)
                for name, request_call, status in requests
            ]

        for name, response, status in responses:
            with self.subTest(name=name):
                self.assertEqual(response.status_code, status)
                self.assertNotIn(
                    secret_notes,
                    response.get_data(as_text=True),
                )
        boundary.weather.assert_not_called()
        boundary.client_class.assert_not_called()

    def test_authenticated_client_rate_limit_blocks_third_full_request(self):
        self.app.config["GEMINI_ADVICE_RATE_LIMIT"] = "2 per minute"
        limiter.storage.reset()
        body = self.request_body(regenerate=True)

        with self.boundaries() as boundary:
            responses = [self.post(body) for _ in range(3)]

        self.assertEqual(
            [response.status_code for response in responses],
            [200, 200, 429],
        )
        self.assertEqual(
            responses[2].get_json()["error"]["code"],
            "RATE_LIMIT_EXCEEDED",
        )
        self.assertEqual(boundary.weather.call_count, 2)
        self.assertEqual(boundary.client_class.call_count, 2)
        self.assertEqual(boundary.client.interactions.create.call_count, 2)


if __name__ == "__main__":
    unittest.main()
