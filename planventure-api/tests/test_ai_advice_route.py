import io
import json
import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["FLASK_ENV"] = "development"
os.environ["RATELIMIT_ENABLED"] = "false"
os.environ["RATELIMIT_STORAGE_URI"] = "memory://"
os.environ["CACHE_TYPE"] = "SimpleCache"

from flask_jwt_extended import create_access_token

from app import create_app
from config import DevelopmentConfig
from extensions import cache, db, limiter
from models import Trip, User
from schemas.ai_advice import AIAdviceRequest, TripAIAdvice
from services.ai_advice import (
    AIAdviceDisabledError,
    AIAdviceInputError,
    AIAdviceResult,
    AIAdviceWarning,
)
from services.gemini import (
    GeminiAuthenticationError,
    GeminiConfigurationError,
    GeminiInvalidResponseError,
    GeminiProviderError,
    GeminiRateLimitError,
    GeminiTimeoutError,
)
from utils.jwt import generate_access_token


TEST_API_KEY = "test-gemini-key-not-real"


def _advice():
    return TripAIAdvice.model_validate(
        {
            "summary": "Kế hoạch nhìn chung hợp lý.",
            "overall_score": 8,
            "strengths": ["Lịch trình cân bằng."],
            "issues": [],
            "recommendations": [],
            "weather_advice": ["Mang áo mưa nhẹ."],
            "packing_list": ["Giày đi bộ"],
            "disclaimer": "Đề xuất AI cần được kiểm tra.",
        }
    )


def _result(
    *,
    warnings=(),
    cache_hit=False,
    weather_cache_hit=True,
):
    return AIAdviceResult(
        advice=_advice(),
        warnings=warnings,
        cache_hit=cache_hit,
        weather_cache_hit=weather_cache_hit,
    )


def _seed_user_and_trip(app, email="owner@example.com"):
    with app.app_context():
        user = User(email=email, password_hash="not-used-for-token")
        db.session.add(user)
        db.session.flush()
        trip = Trip(
            user_id=user.id,
            destination="Da Nang",
            start_date=date.today() + timedelta(days=1),
            end_date=date.today() + timedelta(days=3),
            coordinates=None,
            itinerary=[{"day": 1, "title": "Khám phá thành phố"}],
        )
        db.session.add(trip)
        db.session.commit()
        token = generate_access_token(user.id, {"email": user.email})
        return user.id, trip.id, token


class AIAdviceRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.previous_enabled = DevelopmentConfig.RATELIMIT_ENABLED
        self.previous_storage_uri = DevelopmentConfig.RATELIMIT_STORAGE_URI
        self.previous_limit = DevelopmentConfig.GEMINI_ADVICE_RATE_LIMIT
        self.previous_limiter_enabled = limiter.enabled
        DevelopmentConfig.RATELIMIT_ENABLED = False
        DevelopmentConfig.RATELIMIT_STORAGE_URI = "memory://"
        DevelopmentConfig.GEMINI_ADVICE_RATE_LIMIT = "5 per hour"

        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            GEMINI_ENABLED=True,
            GEMINI_API_KEY=TEST_API_KEY,
            GEMINI_MODEL="gemini-3.6-flash",
            GEMINI_TIMEOUT_SECONDS=30,
            GEMINI_ADVICE_CACHE_TTL_SECONDS=3600,
            GEMINI_ADVICE_RATE_LIMIT="5 per hour",
            GEMINI_ADVICE_MAX_REQUEST_BYTES=32768,
        )
        self.client = self.app.test_client()
        if limiter._storage is not None:
            limiter.storage.reset()
        with self.app.app_context():
            db.create_all()
            cache.clear()
        self.user_id, self.trip_id, self.token = _seed_user_and_trip(self.app)

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
            cache.clear()
        if limiter._storage is not None:
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

    def post_json(self, payload=None, *, token=None, trip_id=None):
        return self.client.post(
            self.url(trip_id),
            headers=self.auth(token or self.token),
            json={} if payload is None else payload,
        )

    @patch(
        "routes.ai_advice.orchestrate_trip_ai_advice",
        return_value=_result(),
    )
    def test_route_registration_and_method(self, mock_service):
        rules = {
            rule.rule: rule.methods
            for rule in self.app.url_map.iter_rules()
        }
        self.assertIn("/trip/<int:trip_id>/ai-advice", rules)
        self.assertIn(
            "POST",
            rules["/trip/<int:trip_id>/ai-advice"],
        )
        self.assertNotIn(
            "GET",
            rules["/trip/<int:trip_id>/ai-advice"],
        )
        self.assertFalse(
            any(rule.startswith("/trips/") for rule in rules)
        )

        get_response = self.client.get(self.url(), headers=self.auth(self.token))
        self.assertEqual(get_response.status_code, 405)
        mock_service.assert_not_called()

    def test_missing_jwt_stops_before_ownership_and_service(self):
        with (
            patch(
                "routes.ai_advice.find_current_user_trip"
            ) as find_trip,
            patch(
                "routes.ai_advice.orchestrate_trip_ai_advice"
            ) as service,
        ):
            response = self.client.post(self.url(), json={})

        self.assertEqual(response.status_code, 401)
        find_trip.assert_not_called()
        service.assert_not_called()

    def test_invalid_jwt_stops_before_ownership_and_service(self):
        with (
            patch(
                "routes.ai_advice.find_current_user_trip"
            ) as find_trip,
            patch(
                "routes.ai_advice.orchestrate_trip_ai_advice"
            ) as service,
        ):
            response = self.client.post(
                self.url(),
                headers=self.auth("not-a-valid-token"),
                json={},
            )

        self.assertEqual(response.status_code, 401)
        find_trip.assert_not_called()
        service.assert_not_called()

    def test_expired_jwt_is_rejected(self):
        with self.app.app_context():
            expired_token = create_access_token(
                identity=str(self.user_id),
                expires_delta=timedelta(seconds=-1),
            )

        with patch(
            "routes.ai_advice.orchestrate_trip_ai_advice"
        ) as service:
            response = self.client.post(
                self.url(),
                headers=self.auth(expired_token),
                json={},
            )

        self.assertEqual(response.status_code, 401)
        service.assert_not_called()

    def test_jwt_user_must_still_exist(self):
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            db.session.delete(user)
            db.session.commit()

        with patch(
            "routes.ai_advice.orchestrate_trip_ai_advice"
        ) as service:
            response = self.post_json({})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.get_json(),
            {"error": "Authentication required."},
        )
        service.assert_not_called()

    @patch(
        "routes.ai_advice.orchestrate_trip_ai_advice",
        return_value=_result(),
    )
    def test_owner_can_request_advice(self, mock_service):
        response = self.post_json({})

        self.assertEqual(response.status_code, 200)
        mock_service.assert_called_once()

    def test_missing_and_other_users_trip_have_same_private_404(self):
        _, _, other_token = _seed_user_and_trip(
            self.app,
            email="other@example.com",
        )
        with patch(
            "routes.ai_advice.orchestrate_trip_ai_advice"
        ) as service:
            missing = self.post_json({}, trip_id=999999)
            other_user = self.post_json({}, token=other_token)

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(other_user.status_code, 404)
        self.assertEqual(
            missing.get_json(),
            {"error": "Trip not found"},
        )
        self.assertEqual(missing.get_json(), other_user.get_json())
        service.assert_not_called()

    @patch(
        "routes.ai_advice.orchestrate_trip_ai_advice",
        return_value=_result(),
    )
    def test_json_content_types_and_empty_body_use_defaults(
        self,
        mock_service,
    ):
        responses = [
            self.client.post(
                self.url(),
                headers=self.auth(self.token),
                data=b"",
                content_type="application/json",
            ),
            self.client.post(
                self.url(),
                headers=self.auth(self.token),
                data=b"{}",
                content_type="application/json; charset=utf-8",
            ),
        ]

        self.assertEqual(
            [response.status_code for response in responses],
            [200, 200],
        )
        for call in mock_service.call_args_list:
            validated_request = call.args[1]
            self.assertIsInstance(validated_request, AIAdviceRequest)
            self.assertEqual(validated_request.mode, "review")
            self.assertEqual(validated_request.language, "vi")

    def test_non_json_content_types_are_rejected(self):
        requests = [
            {
                "data": '{"mode":"review"}',
                "content_type": "text/plain",
            },
            {
                "data": {"mode": "review"},
                "content_type": "application/x-www-form-urlencoded",
            },
            {
                "data": {"upload": (io.BytesIO(b"test"), "test.txt")},
                "content_type": "multipart/form-data",
            },
        ]
        with patch(
            "routes.ai_advice.orchestrate_trip_ai_advice"
        ) as service:
            for request_data in requests:
                with self.subTest(content_type=request_data["content_type"]):
                    response = self.client.post(
                        self.url(),
                        headers=self.auth(self.token),
                        **request_data,
                    )
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(
                        response.get_json()["error"]["code"],
                        "AI_INVALID_REQUEST",
                    )
        service.assert_not_called()

    def test_invalid_json_and_non_object_roots_are_rejected(self):
        bodies = [
            b"{",
            b"[]",
            b'"text"',
            b"1",
            b"true",
            b"null",
        ]
        with patch(
            "routes.ai_advice.orchestrate_trip_ai_advice"
        ) as service:
            for body in bodies:
                with self.subTest(body=body):
                    response = self.client.post(
                        self.url(),
                        headers=self.auth(self.token),
                        data=body,
                        content_type="application/json",
                    )
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(
                        response.get_json()["error"]["code"],
                        "AI_INVALID_REQUEST",
                    )
        service.assert_not_called()

    @patch(
        "routes.ai_advice.orchestrate_trip_ai_advice",
        return_value=_result(),
    )
    def test_request_body_at_limit_is_allowed(self, mock_service):
        max_bytes = self.app.config["GEMINI_ADVICE_MAX_REQUEST_BYTES"]
        body = b"{}" + (b" " * (max_bytes - 2))

        response = self.client.post(
            self.url(),
            headers=self.auth(self.token),
            data=body,
            content_type="application/json",
        )

        self.assertEqual(len(body), 32768)
        self.assertEqual(response.status_code, 200)
        mock_service.assert_called_once()

    def test_request_body_over_limit_is_413_before_parse(self):
        max_bytes = self.app.config["GEMINI_ADVICE_MAX_REQUEST_BYTES"]
        body = b"{}" + (b" " * (max_bytes - 1))
        secret_body = body + b"raw-secret-body"

        with patch(
            "routes.ai_advice.orchestrate_trip_ai_advice"
        ) as service:
            response = self.client.post(
                self.url(),
                headers=self.auth(self.token),
                data=secret_body,
                content_type="application/json",
            )

        self.assertGreater(len(secret_body), 32768)
        self.assertEqual(response.status_code, 413)
        payload = response.get_json()
        self.assertEqual(
            payload["error"]["code"],
            "AI_REQUEST_TOO_LARGE",
        )
        self.assertNotIn("raw-secret-body", json.dumps(payload))
        service.assert_not_called()

    def test_declared_content_length_over_limit_is_rejected_early(self):
        with (
            patch(
                "flask.wrappers.Request.get_data"
            ) as get_data,
            patch(
                "routes.ai_advice.orchestrate_trip_ai_advice"
            ) as service,
        ):
            response = self.client.post(
                self.url(),
                headers={
                    **self.auth(self.token),
                    "Content-Type": "application/json",
                },
                data=b"{}",
                environ_overrides={"CONTENT_LENGTH": "32769"},
            )

        self.assertEqual(response.status_code, 413)
        get_data.assert_not_called()
        service.assert_not_called()

    def test_missing_content_length_still_checks_actual_body_size(self):
        max_bytes = self.app.config["GEMINI_ADVICE_MAX_REQUEST_BYTES"]
        body = b"{}" + (b" " * max_bytes)
        with patch(
            "routes.ai_advice.orchestrate_trip_ai_advice"
        ) as service:
            response = self.client.open(
                self.url(),
                method="POST",
                headers={
                    **self.auth(self.token),
                    "Content-Type": "application/json",
                },
                input_stream=io.BytesIO(body),
                environ_overrides={
                    "CONTENT_LENGTH": "",
                    "wsgi.input_terminated": True,
                },
            )

        self.assertEqual(response.status_code, 413)
        service.assert_not_called()

    def test_underreported_content_length_still_checks_actual_body_size(self):
        max_bytes = self.app.config["GEMINI_ADVICE_MAX_REQUEST_BYTES"]
        body = b"{}" + (b" " * max_bytes)
        with patch(
            "routes.ai_advice.orchestrate_trip_ai_advice"
        ) as service:
            response = self.client.open(
                self.url(),
                method="POST",
                headers={
                    **self.auth(self.token),
                    "Content-Type": "application/json",
                },
                input_stream=io.BytesIO(body),
                environ_overrides={
                    "CONTENT_LENGTH": "1",
                    "wsgi.input_terminated": True,
                },
            )

        self.assertEqual(response.status_code, 413)
        service.assert_not_called()

    def test_unsupported_mode_has_specific_error(self):
        invalid_modes = [
            "generate",
            "optimize",
            None,
            1,
            True,
            [],
            {},
        ]
        with patch(
            "routes.ai_advice.orchestrate_trip_ai_advice"
        ) as service:
            for mode in invalid_modes:
                with self.subTest(mode=mode):
                    response = self.post_json({"mode": mode})
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(
                        response.get_json()["error"],
                        {
                            "code": "AI_UNSUPPORTED_MODE",
                            "message": (
                                "Only review mode is currently supported."
                            ),
                        },
                    )
        service.assert_not_called()

    def test_pydantic_validation_errors_are_sanitized(self):
        secret_notes = "sensitive-notes-" + ("x" * 1001)
        invalid_payloads = [
            {"include_weather": "true"},
            {"regenerate": 1},
            {"language": "invalid-language"},
            {"preferences": {"notes": secret_notes}},
            {
                "preferences": {
                    "interests": [f"interest-{index}" for index in range(21)]
                }
            },
            {"unexpected": "value"},
            {"preferences": {"unexpected": "value"}},
            {"preferences": None},
        ]

        with patch(
            "routes.ai_advice.orchestrate_trip_ai_advice"
        ) as service:
            for payload in invalid_payloads:
                with self.subTest(payload=list(payload)):
                    response = self.post_json(payload)
                    self.assertEqual(response.status_code, 400)
                    body = response.get_json()
                    self.assertEqual(
                        body["error"]["code"],
                        "AI_INVALID_REQUEST",
                    )
                    fields = body["error"]["details"]["fields"]
                    self.assertGreaterEqual(len(fields), 1)
                    self.assertLessEqual(len(fields), 20)
                    preferences = payload.get("preferences")
                    if (
                        isinstance(preferences, dict)
                        and preferences.get("notes")
                    ):
                        self.assertEqual(
                            fields[0]["field"],
                            "preferences.notes",
                        )
                    for field in fields:
                        self.assertEqual(
                            set(field),
                            {"field", "type", "message"},
                        )
                    serialized = json.dumps(body, ensure_ascii=False)
                    self.assertNotIn(secret_notes, serialized)
                    self.assertNotIn(TEST_API_KEY, serialized)
                    self.assertNotIn("errors.pydantic.dev", serialized)
                    self.assertNotIn('"input"', serialized)
        service.assert_not_called()

    def test_validation_details_are_limited_to_twenty_errors(self):
        payload = {f"extra_{index}": index for index in range(30)}

        response = self.post_json(payload)

        self.assertEqual(response.status_code, 400)
        fields = response.get_json()["error"]["details"]["fields"]
        self.assertEqual(len(fields), 20)

    @patch(
        "routes.ai_advice.orchestrate_trip_ai_advice",
        return_value=_result(),
    )
    def test_success_response_and_service_configuration(self, mock_service):
        response = self.post_json(
            {
                "mode": "review",
                "language": "vi",
                "include_weather": True,
                "regenerate": False,
                "preferences": {
                    "budget": "medium",
                    "pace": "relaxed",
                    "interests": ["food"],
                    "transport": "motorbike",
                    "dietary_requirements": [],
                    "notes": "Di chuyển ít.",
                },
            }
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["trip_id"], self.trip_id)
        self.assertEqual(payload["mode"], "review")
        self.assertEqual(payload["advice"], _advice().model_dump(mode="json"))
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(
            payload["meta"],
            {"cache_hit": False, "weather_cache_hit": True},
        )

        trip, validated_request = mock_service.call_args.args
        self.assertEqual(trip.id, self.trip_id)
        self.assertIsInstance(validated_request, AIAdviceRequest)
        self.assertEqual(
            mock_service.call_args.kwargs,
            {
                "gemini_enabled": True,
                "api_key": TEST_API_KEY,
                "model": "gemini-3.6-flash",
                "timeout_seconds": 30,
                "cache_ttl_seconds": 3600,
            },
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in (
            TEST_API_KEY,
            "gemini-3.6-flash",
            "prompt",
            "cache_key",
            "user_id",
            "provider_response",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_success_metadata_and_warnings_are_plain_json(self):
        cases = [
            (
                _result(
                    cache_hit=True,
                    weather_cache_hit=None,
                ),
                [],
                {"cache_hit": True, "weather_cache_hit": None},
            ),
            (
                _result(
                    warnings=(
                        AIAdviceWarning(
                            code="WEATHER_UNAVAILABLE",
                            message=(
                                "Weather forecast is currently unavailable."
                            ),
                        ),
                    ),
                    weather_cache_hit=None,
                ),
                [
                    {
                        "code": "WEATHER_UNAVAILABLE",
                        "message": (
                            "Weather forecast is currently unavailable."
                        ),
                    }
                ],
                {"cache_hit": False, "weather_cache_hit": None},
            ),
            (
                _result(
                    warnings=(
                        AIAdviceWarning(
                            code="WEATHER_PARTIAL_COVERAGE",
                            message=(
                                "Weather forecast does not cover "
                                "the entire trip."
                            ),
                        ),
                    ),
                    weather_cache_hit=False,
                ),
                [
                    {
                        "code": "WEATHER_PARTIAL_COVERAGE",
                        "message": (
                            "Weather forecast does not cover "
                            "the entire trip."
                        ),
                    }
                ],
                {"cache_hit": False, "weather_cache_hit": False},
            ),
        ]

        for result, expected_warnings, expected_meta in cases:
            with (
                self.subTest(warnings=expected_warnings),
                patch(
                    "routes.ai_advice.orchestrate_trip_ai_advice",
                    return_value=result,
                ),
            ):
                response = self.post_json({})
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(payload["warnings"], expected_warnings)
                self.assertEqual(payload["meta"], expected_meta)

    def test_service_exception_mapping_is_safe(self):
        raw_detail = (
            "raw provider response with test-gemini-key-not-real and prompt"
        )
        cases = [
            (
                AIAdviceDisabledError(raw_detail),
                503,
                "AI_DISABLED",
                "AI advice is currently disabled.",
            ),
            (
                AIAdviceInputError(raw_detail),
                400,
                "AI_INVALID_REQUEST",
                "Trip data cannot be processed for AI advice.",
            ),
            (
                GeminiConfigurationError(raw_detail),
                503,
                "AI_NOT_CONFIGURED",
                "AI advice service is not configured.",
            ),
            (
                GeminiAuthenticationError(raw_detail),
                502,
                "AI_PROVIDER_AUTHENTICATION_FAILED",
                "AI advice provider authentication failed.",
            ),
            (
                GeminiTimeoutError(raw_detail),
                504,
                "AI_PROVIDER_TIMEOUT",
                "AI advice provider timed out.",
            ),
            (
                GeminiRateLimitError(raw_detail),
                503,
                "AI_PROVIDER_RATE_LIMITED",
                "AI advice provider is temporarily rate limited.",
            ),
            (
                GeminiProviderError(raw_detail),
                502,
                "AI_PROVIDER_UNAVAILABLE",
                "AI advice provider is temporarily unavailable.",
            ),
            (
                GeminiInvalidResponseError(raw_detail),
                502,
                "AI_INVALID_RESPONSE",
                "AI advice provider returned an invalid response.",
            ),
        ]

        for error, status, code, message in cases:
            with (
                self.subTest(error=type(error).__name__),
                patch(
                    "routes.ai_advice.orchestrate_trip_ai_advice",
                    side_effect=error,
                ),
            ):
                response = self.post_json({})
                self.assertEqual(response.status_code, status)
                self.assertEqual(
                    response.get_json(),
                    {"error": {"code": code, "message": message}},
                )
                serialized = json.dumps(response.get_json())
                self.assertNotIn(raw_detail, serialized)
                self.assertNotIn(TEST_API_KEY, serialized)
                self.assertNotIn("traceback", serialized.lower())

    def test_unexpected_programming_error_uses_global_safe_handler(self):
        with patch(
            "routes.ai_advice.orchestrate_trip_ai_advice",
            side_effect=RuntimeError("programming bug"),
        ):
            response = self.post_json({})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.get_json(),
            {"error": "An internal server error occurred."},
        )
        self.assertNotIn(
            "programming bug",
            json.dumps(response.get_json()),
        )

    def test_public_routes_are_not_broken_by_pre_auth_hook(self):
        health = self.client.get("/health")
        register = self.client.post(
            "/auth/register",
            json={
                "email": "public-route@example.com",
                "password": "test1234",
            },
        )
        login = self.client.post(
            "/auth/login",
            json={
                "email": "public-route@example.com",
                "password": "test1234",
            },
        )
        trip = self.client.post(
            "/trip",
            headers=self.auth(self.token),
            json={
                "destination": "Hue",
                "start_date": (
                    date.today() + timedelta(days=2)
                ).isoformat(),
                "end_date": (
                    date.today() + timedelta(days=3)
                ).isoformat(),
            },
        )

        self.assertEqual(health.status_code, 200)
        self.assertEqual(register.status_code, 201)
        self.assertEqual(login.status_code, 200)
        self.assertEqual(trip.status_code, 201)

    def test_disabled_route_returns_503_without_real_provider_call(self):
        self.app.config.update(
            GEMINI_ENABLED=False,
            GEMINI_API_KEY="",
        )

        response = self.post_json({})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "AI_DISABLED",
        )


class AIAdviceRateLimitTestCase(unittest.TestCase):
    def setUp(self):
        self.previous_enabled = DevelopmentConfig.RATELIMIT_ENABLED
        self.previous_storage_uri = DevelopmentConfig.RATELIMIT_STORAGE_URI
        self.previous_limit = DevelopmentConfig.GEMINI_ADVICE_RATE_LIMIT
        self.previous_limiter_enabled = limiter.enabled
        DevelopmentConfig.RATELIMIT_ENABLED = True
        DevelopmentConfig.RATELIMIT_STORAGE_URI = "memory://"
        DevelopmentConfig.GEMINI_ADVICE_RATE_LIMIT = "2 per minute"

        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            GEMINI_ENABLED=True,
            GEMINI_API_KEY=TEST_API_KEY,
            GEMINI_MODEL="gemini-3.6-flash",
            GEMINI_TIMEOUT_SECONDS=30,
            GEMINI_ADVICE_CACHE_TTL_SECONDS=3600,
            GEMINI_ADVICE_RATE_LIMIT="2 per minute",
            GEMINI_ADVICE_MAX_REQUEST_BYTES=32768,
        )
        self.client = self.app.test_client()
        limiter.storage.reset()
        with self.app.app_context():
            db.create_all()
            cache.clear()
        self.user_id, self.trip_id, self.token = _seed_user_and_trip(self.app)

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

    def post(self, token=None, trip_id=None):
        return self.client.post(
            f"/trip/{trip_id or self.trip_id}/ai-advice",
            headers=self.auth(token or self.token),
            json={},
        )

    @patch(
        "routes.ai_advice.orchestrate_trip_ai_advice",
        return_value=_result(),
    )
    def test_same_user_is_limited_after_two_requests(self, mock_service):
        responses = [self.post() for _ in range(3)]

        self.assertEqual(
            [response.status_code for response in responses],
            [200, 200, 429],
        )
        self.assertEqual(
            responses[2].get_json(),
            {
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": "Rate limit exceeded. Try again later.",
                }
            },
        )
        self.assertEqual(mock_service.call_count, 2)

    @patch(
        "routes.ai_advice.orchestrate_trip_ai_advice",
        return_value=_result(),
    )
    def test_two_users_have_independent_limit_buckets(self, mock_service):
        _, other_trip_id, other_token = _seed_user_and_trip(
            self.app,
            email="rate-other@example.com",
        )

        first_user = [self.post() for _ in range(3)]
        second_user = self.post(
            token=other_token,
            trip_id=other_trip_id,
        )

        self.assertEqual(
            [response.status_code for response in first_user],
            [200, 200, 429],
        )
        self.assertEqual(second_user.status_code, 200)
        self.assertEqual(mock_service.call_count, 3)

    @patch(
        "routes.ai_advice.orchestrate_trip_ai_advice",
        return_value=_result(cache_hit=True),
    )
    def test_ai_cache_hits_are_still_rate_limited(self, mock_service):
        responses = [self.post() for _ in range(3)]

        self.assertTrue(responses[0].get_json()["meta"]["cache_hit"])
        self.assertTrue(responses[1].get_json()["meta"]["cache_hit"])
        self.assertEqual(responses[2].status_code, 429)
        self.assertEqual(mock_service.call_count, 2)

    @patch("routes.ai_advice.orchestrate_trip_ai_advice")
    def test_invalid_requests_are_counted_by_limiter(self, mock_service):
        responses = [
            self.client.post(
                f"/trip/{self.trip_id}/ai-advice",
                headers=self.auth(self.token),
                json={"mode": "unsupported"},
            )
            for _ in range(3)
        ]

        self.assertEqual(
            [response.status_code for response in responses],
            [400, 400, 429],
        )
        mock_service.assert_not_called()

    @patch(
        "routes.ai_advice.orchestrate_trip_ai_advice",
        side_effect=GeminiRateLimitError("provider quota"),
    )
    def test_provider_rate_limit_is_503_not_client_429(self, mock_service):
        response = self.post()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "AI_PROVIDER_RATE_LIMITED",
        )
        mock_service.assert_called_once()


if __name__ == "__main__":
    unittest.main()
