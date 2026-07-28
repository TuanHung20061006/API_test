import json
import unittest
from copy import deepcopy

from pydantic import BaseModel, ValidationError

from schemas import (
    AIAdviceIssue,
    AIAdvicePreferences,
    AIAdviceRecommendation,
    AIAdviceRequest,
    TripAIAdvice,
)


VALID_REQUEST = {
    "mode": "review",
    "language": "vi",
    "include_weather": True,
    "regenerate": False,
    "preferences": {
        "budget": "medium",
        "pace": "relaxed",
        "interests": ["food", "culture", "photography"],
        "transport": "motorbike",
        "dietary_requirements": [],
        "notes": "Không muốn di chuyển quá nhiều.",
    },
}

VALID_ADVICE = {
    "summary": "Kế hoạch nhìn chung hợp lý.",
    "overall_score": 8,
    "strengths": ["Các địa điểm trong ngày tương đối gần nhau."],
    "issues": [
        {
            "severity": "medium",
            "day": 2,
            "message": "Lịch trình ngày thứ hai hơi dày.",
        }
    ],
    "recommendations": [
        {
            "day": 2,
            "action": "Chuyển hoạt động ngoài trời sang buổi sáng.",
            "reason": "Buổi chiều có khả năng mưa.",
        }
    ],
    "weather_advice": ["Mang áo mưa nhẹ."],
    "packing_list": ["Áo mưa", "Giày đi bộ"],
    "disclaimer": "Đề xuất do AI tạo và cần được kiểm tra trước khi sử dụng.",
}


def collect_property_names(schema_fragment: object) -> set[str]:
    if isinstance(schema_fragment, dict):
        property_names = set(schema_fragment.get("properties", {}))
        for value in schema_fragment.values():
            property_names.update(collect_property_names(value))
        return property_names
    if isinstance(schema_fragment, list):
        property_names = set()
        for value in schema_fragment:
            property_names.update(collect_property_names(value))
        return property_names
    return set()


class AIAdviceRequestSchemaTestCase(unittest.TestCase):
    def test_full_request_is_valid_and_strings_are_stripped(self):
        payload = deepcopy(VALID_REQUEST)
        payload["preferences"]["budget"] = "  medium  "
        payload["preferences"]["interests"][0] = "  food  "

        request_model = AIAdviceRequest.model_validate(payload)

        self.assertEqual(request_model.mode, "review")
        self.assertEqual(request_model.language, "vi")
        self.assertIs(request_model.include_weather, True)
        self.assertIs(request_model.regenerate, False)
        self.assertEqual(request_model.preferences.budget, "medium")
        self.assertEqual(request_model.preferences.interests[0], "food")

    def test_request_defaults_and_mutable_lists_are_isolated(self):
        first = AIAdviceRequest()
        second = AIAdviceRequest()

        self.assertEqual(first.mode, "review")
        self.assertEqual(first.language, "vi")
        self.assertIs(first.include_weather, True)
        self.assertIs(first.regenerate, False)
        self.assertIsInstance(first.preferences, AIAdvicePreferences)
        self.assertEqual(first.preferences.interests, [])
        self.assertEqual(first.preferences.dietary_requirements, [])

        first.preferences.interests.append("food")
        self.assertEqual(second.preferences.interests, [])

    def test_extra_fields_are_rejected_at_root_and_preferences(self):
        invalid_payloads = (
            {"unexpected": True},
            {"preferences": {"unexpected": True}},
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    AIAdviceRequest.model_validate(payload)

    def test_request_types_are_strict(self):
        invalid_payloads = (
            {"include_weather": "true"},
            {"regenerate": 1},
            {"preferences": {"interests": ["food", 123]}},
            {"preferences": {"dietary_requirements": [True]}},
            {"preferences": None},
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    AIAdviceRequest.model_validate(payload)

    def test_unsupported_mode_is_rejected(self):
        with self.assertRaises(ValidationError):
            AIAdviceRequest.model_validate({"mode": "generate"})

    def test_supported_language_tags_are_accepted(self):
        for language in ("vi", "en", "en-US", "zh-CN"):
            with self.subTest(language=language):
                request_model = AIAdviceRequest.model_validate(
                    {"language": language}
                )
                self.assertEqual(request_model.language, language)

    def test_invalid_language_tags_are_rejected(self):
        for language in (
            "v",
            "english",
            "vi VN",
            "../../etc",
            "vi<script>",
        ):
            with self.subTest(language=language):
                with self.assertRaises(ValidationError):
                    AIAdviceRequest.model_validate({"language": language})

    def test_request_length_and_list_limits_are_enforced(self):
        invalid_preferences = (
            {"notes": "x" * 1001},
            {"interests": ["item"] * 21},
            {"dietary_requirements": ["item"] * 21},
            {"interests": ["x" * 101]},
            {"interests": ["   "]},
            {"dietary_requirements": ["   "]},
            {"budget": "   "},
        )

        for preferences in invalid_preferences:
            with self.subTest(preferences=preferences):
                with self.assertRaises(ValidationError):
                    AIAdviceRequest.model_validate(
                        {"preferences": preferences}
                    )


class TripAIAdviceSchemaTestCase(unittest.TestCase):
    def test_valid_structured_advice(self):
        advice = TripAIAdvice.model_validate(VALID_ADVICE)

        self.assertEqual(advice.overall_score, 8)
        self.assertEqual(advice.issues[0].severity, "medium")
        self.assertEqual(advice.recommendations[0].day, 2)

    def test_overall_score_is_strict_and_bounded(self):
        for score in (0, 11, "8", 8.0, True):
            payload = deepcopy(VALID_ADVICE)
            payload["overall_score"] = score
            with self.subTest(score=score):
                with self.assertRaises(ValidationError):
                    TripAIAdvice.model_validate(payload)

    def test_issue_severity_is_restricted(self):
        payload = deepcopy(VALID_ADVICE)
        payload["issues"][0]["severity"] = "critical"

        with self.assertRaises(ValidationError):
            TripAIAdvice.model_validate(payload)

    def test_issue_day_is_strict_and_bounded(self):
        for day in (0, 367, "2", True):
            payload = deepcopy(VALID_ADVICE)
            payload["issues"][0]["day"] = day
            with self.subTest(day=day):
                with self.assertRaises(ValidationError):
                    TripAIAdvice.model_validate(payload)

    def test_recommendation_day_is_strict_and_bounded(self):
        for day in (0, 367, "2", True):
            payload = deepcopy(VALID_ADVICE)
            payload["recommendations"][0]["day"] = day
            with self.subTest(day=day):
                with self.assertRaises(ValidationError):
                    TripAIAdvice.model_validate(payload)

    def test_empty_required_strings_are_rejected_after_stripping(self):
        invalid_fields = (
            "summary",
            "disclaimer",
            "issue_message",
            "recommendation_action",
            "recommendation_reason",
        )

        for field_name in invalid_fields:
            payload = deepcopy(VALID_ADVICE)
            if field_name == "issue_message":
                payload["issues"][0]["message"] = "   "
            elif field_name == "recommendation_action":
                payload["recommendations"][0]["action"] = "   "
            elif field_name == "recommendation_reason":
                payload["recommendations"][0]["reason"] = "   "
            else:
                payload[field_name] = "   "

            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    TripAIAdvice.model_validate(payload)

    def test_required_advice_fields_cannot_be_omitted(self):
        for field_name in ("summary", "overall_score", "disclaimer"):
            payload = deepcopy(VALID_ADVICE)
            payload.pop(field_name)
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    TripAIAdvice.model_validate(payload)

    def test_response_string_limits_are_enforced(self):
        invalid_payloads = []

        summary_too_long = deepcopy(VALID_ADVICE)
        summary_too_long["summary"] = "x" * 2001
        invalid_payloads.append(summary_too_long)

        disclaimer_too_long = deepcopy(VALID_ADVICE)
        disclaimer_too_long["disclaimer"] = "x" * 1001
        invalid_payloads.append(disclaimer_too_long)

        issue_message_too_long = deepcopy(VALID_ADVICE)
        issue_message_too_long["issues"][0]["message"] = "x" * 501
        invalid_payloads.append(issue_message_too_long)

        recommendation_action_too_long = deepcopy(VALID_ADVICE)
        recommendation_action_too_long["recommendations"][0]["action"] = (
            "x" * 501
        )
        invalid_payloads.append(recommendation_action_too_long)

        recommendation_reason_too_long = deepcopy(VALID_ADVICE)
        recommendation_reason_too_long["recommendations"][0]["reason"] = (
            "x" * 501
        )
        invalid_payloads.append(recommendation_reason_too_long)

        strength_too_long = deepcopy(VALID_ADVICE)
        strength_too_long["strengths"] = ["x" * 501]
        invalid_payloads.append(strength_too_long)

        weather_advice_too_long = deepcopy(VALID_ADVICE)
        weather_advice_too_long["weather_advice"] = ["x" * 501]
        invalid_payloads.append(weather_advice_too_long)

        packing_item_too_long = deepcopy(VALID_ADVICE)
        packing_item_too_long["packing_list"] = ["x" * 201]
        invalid_payloads.append(packing_item_too_long)

        whitespace_strength = deepcopy(VALID_ADVICE)
        whitespace_strength["strengths"] = ["   "]
        invalid_payloads.append(whitespace_strength)

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    TripAIAdvice.model_validate(payload)

    def test_extra_fields_are_rejected_at_every_response_level(self):
        invalid_payloads = []

        root_extra = deepcopy(VALID_ADVICE)
        root_extra["unexpected"] = True
        invalid_payloads.append(root_extra)

        issue_extra = deepcopy(VALID_ADVICE)
        issue_extra["issues"][0]["unexpected"] = True
        invalid_payloads.append(issue_extra)

        recommendation_extra = deepcopy(VALID_ADVICE)
        recommendation_extra["recommendations"][0]["unexpected"] = True
        invalid_payloads.append(recommendation_extra)

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    TripAIAdvice.model_validate(payload)

    def test_response_list_limits_are_enforced(self):
        invalid_lists = (
            ("strengths", ["item"] * 21),
            (
                "issues",
                [
                    {"severity": "low", "message": "Issue"}
                    for _ in range(21)
                ],
            ),
            (
                "recommendations",
                [
                    {"action": "Action", "reason": "Reason"}
                    for _ in range(21)
                ],
            ),
            ("weather_advice", ["item"] * 21),
            ("packing_list", ["item"] * 31),
        )

        for field_name, value in invalid_lists:
            payload = deepcopy(VALID_ADVICE)
            payload[field_name] = value
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    TripAIAdvice.model_validate(payload)

    def test_response_lists_cannot_be_null(self):
        for field_name in (
            "strengths",
            "issues",
            "recommendations",
            "weather_advice",
            "packing_list",
        ):
            payload = deepcopy(VALID_ADVICE)
            payload[field_name] = None
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    TripAIAdvice.model_validate(payload)

    def test_response_list_defaults_are_isolated(self):
        required_fields = {
            "summary": "Summary",
            "overall_score": 8,
            "disclaimer": "Disclaimer",
        }
        first = TripAIAdvice.model_validate(required_fields)
        second = TripAIAdvice.model_validate(required_fields)

        first.strengths.append("Strength")
        self.assertEqual(second.strengths, [])

    def test_model_dump_is_plain_json_compatible_data(self):
        advice = TripAIAdvice.model_validate(VALID_ADVICE)
        dumped = advice.model_dump(mode="json")

        json.dumps(dumped)
        self.assertIsInstance(dumped, dict)
        self.assertEqual(dumped["issues"][0]["severity"], "medium")
        self.assertFalse(
            any(isinstance(value, BaseModel) for value in dumped.values())
        )
        self.assertEqual(set(dumped), set(TripAIAdvice.model_fields))

    def test_json_schema_exposes_required_constraints_without_sensitive_fields(self):
        schema = TripAIAdvice.model_json_schema()
        properties = schema["properties"]
        score_schema = properties["overall_score"]
        issue_schema = schema["$defs"]["AIAdviceIssue"]

        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("summary", properties)
        self.assertIn("overall_score", properties)
        self.assertIn("issues", properties)
        self.assertIn("recommendations", properties)
        self.assertEqual(
            set(schema["required"]),
            {"summary", "overall_score", "disclaimer"},
        )
        self.assertEqual(score_schema["minimum"], 1)
        self.assertEqual(score_schema["maximum"], 10)
        self.assertEqual(
            issue_schema["properties"]["severity"]["enum"],
            ["low", "medium", "high"],
        )
        self.assertEqual(properties["strengths"]["maxItems"], 20)
        self.assertEqual(properties["issues"]["maxItems"], 20)
        self.assertEqual(properties["recommendations"]["maxItems"], 20)
        self.assertEqual(properties["packing_list"]["maxItems"], 30)
        self.assertEqual(properties["summary"]["maxLength"], 2000)
        self.assertEqual(
            properties["strengths"]["items"]["maxLength"],
            500,
        )

        sensitive_fields = {
            "user_id",
            "email",
            "password",
            "token",
            "api_key",
        }
        self.assertTrue(
            collect_property_names(schema).isdisjoint(sensitive_fields)
        )
        self.assertIn("$defs", schema)
        self.assertEqual(
            properties["issues"]["items"]["$ref"],
            "#/$defs/AIAdviceIssue",
        )


class NestedSchemaConfigurationTestCase(unittest.TestCase):
    def test_public_nested_models_forbid_extra_fields(self):
        with self.assertRaises(ValidationError):
            AIAdviceIssue.model_validate(
                {
                    "severity": "low",
                    "message": "Issue",
                    "unexpected": True,
                }
            )

        with self.assertRaises(ValidationError):
            AIAdviceRecommendation.model_validate(
                {
                    "action": "Action",
                    "reason": "Reason",
                    "unexpected": True,
                }
            )


if __name__ == "__main__":
    unittest.main()
