import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from config import ProductionConfig, StagingConfig, get_config


PROJECT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_DIR / "config.py"


def load_isolated_config(environment):
    spec = importlib.util.spec_from_file_location(
        "planventure_isolated_config",
        CONFIG_PATH,
    )
    config_module = importlib.util.module_from_spec(spec)
    with (
        patch.dict(os.environ, environment, clear=True),
        patch("dotenv.load_dotenv"),
    ):
        spec.loader.exec_module(config_module)
    return config_module


class DeploymentConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.environment = {
            "FLASK_ENV": "staging",
            "SECRET_KEY": "staging-secret-key-with-at-least-32-characters",
            "JWT_SECRET_KEY": "staging-jwt-secret-with-at-least-32-characters",
            "WEATHER_API_KEY": "test-weather-key",
            "DATABASE_URL": (
                "postgresql+psycopg://planventure:password@db/planventure"
            ),
            "CORS_ORIGINS": "https://staging.planventure.app",
            "RATELIMIT_ENABLED": "true",
            "RATELIMIT_STORAGE_URI": "redis://redis:6379/2",
            "CACHE_TYPE": "RedisCache",
            "CACHE_REDIS_URL": "redis://redis:6379/1",
        }
        self.environment_patch = patch.dict(
            os.environ,
            self.environment,
            clear=False,
        )
        self.environment_patch.start()

    def tearDown(self):
        self.environment_patch.stop()

    def test_staging_and_production_configs_are_selected(self):
        self.assertIs(get_config(), StagingConfig)

        os.environ["FLASK_ENV"] = "production"
        self.assertIs(get_config(), ProductionConfig)

    def test_unknown_environment_is_rejected(self):
        os.environ["FLASK_ENV"] = "prodution"

        with self.assertRaisesRegex(RuntimeError, "Unsupported FLASK_ENV"):
            get_config()

    def test_deployment_rejects_local_or_process_only_services(self):
        os.environ.update(
            {
                "DATABASE_URL": "sqlite:///planventure.db",
                "CORS_ORIGINS": "http://localhost:5173",
                "RATELIMIT_ENABLED": "false",
                "RATELIMIT_STORAGE_URI": "memory://",
                "CACHE_TYPE": "SimpleCache",
                "CACHE_REDIS_URL": "",
            }
        )

        with self.assertRaises(RuntimeError) as error:
            get_config()

        message = str(error.exception)
        self.assertIn("DATABASE_URL", message)
        self.assertIn("CORS_ORIGINS", message)
        self.assertIn("RATELIMIT_ENABLED", message)
        self.assertIn("RATELIMIT_STORAGE_URI", message)
        self.assertIn("CACHE_TYPE", message)
        self.assertIn("CACHE_REDIS_URL", message)

    def test_deployment_rejects_missing_secrets(self):
        os.environ.update(
            {
                "SECRET_KEY": "short",
                "JWT_SECRET_KEY": "short",
                "WEATHER_API_KEY": "",
            }
        )

        with self.assertRaises(RuntimeError) as error:
            get_config()

        message = str(error.exception)
        self.assertIn("SECRET_KEY", message)
        self.assertIn("JWT_SECRET_KEY", message)
        self.assertIn("WEATHER_API_KEY", message)

    def test_example_files_require_replacement_before_deployment(self):
        for file_name in (".env.staging.example", ".env.production.example"):
            values = {}
            example_lines = (PROJECT_DIR / file_name).read_text(
                encoding="utf-8"
            ).splitlines()
            for line in example_lines:
                if line and not line.startswith("#"):
                    name, value = line.split("=", 1)
                    values[name] = value

            with self.subTest(file_name=file_name):
                with patch.dict(os.environ, values, clear=True):
                    with self.assertRaises(RuntimeError):
                        get_config()


class GeminiConfigTestCase(unittest.TestCase):
    def test_gemini_defaults(self):
        config_module = load_isolated_config({})

        self.assertIs(config_module.Config.GEMINI_ENABLED, False)
        self.assertEqual(config_module.Config.GEMINI_API_KEY, "")
        self.assertEqual(config_module.Config.GEMINI_MODEL, "gemini-3.6-flash")
        self.assertEqual(config_module.Config.GEMINI_TIMEOUT_SECONDS, 30)
        self.assertEqual(
            config_module.Config.GEMINI_ADVICE_CACHE_TTL_SECONDS,
            3600,
        )
        self.assertEqual(
            config_module.Config.GEMINI_ADVICE_RATE_LIMIT,
            "5 per hour",
        )
        self.assertEqual(
            config_module.Config.GEMINI_ADVICE_MAX_REQUEST_BYTES,
            32768,
        )

    def test_gemini_environment_overrides(self):
        config_module = load_isolated_config(
            {
                "GEMINI_ENABLED": "1",
                "GEMINI_API_KEY": "test-gemini-key-not-real",
                "GEMINI_MODEL": "test-gemini-model",
                "GEMINI_TIMEOUT_SECONDS": "45",
                "GEMINI_ADVICE_CACHE_TTL_SECONDS": "7200",
                "GEMINI_ADVICE_RATE_LIMIT": "7 per hour",
                "GEMINI_ADVICE_MAX_REQUEST_BYTES": "16384",
            }
        )

        self.assertIs(config_module.Config.GEMINI_ENABLED, True)
        self.assertEqual(
            config_module.Config.GEMINI_API_KEY,
            "test-gemini-key-not-real",
        )
        self.assertEqual(config_module.Config.GEMINI_MODEL, "test-gemini-model")
        self.assertEqual(config_module.Config.GEMINI_TIMEOUT_SECONDS, 45)
        self.assertEqual(
            config_module.Config.GEMINI_ADVICE_CACHE_TTL_SECONDS,
            7200,
        )
        self.assertEqual(
            config_module.Config.GEMINI_ADVICE_RATE_LIMIT,
            "7 per hour",
        )
        self.assertEqual(
            config_module.Config.GEMINI_ADVICE_MAX_REQUEST_BYTES,
            16384,
        )

    def test_gemini_boolean_values_follow_existing_convention(self):
        for raw_value, expected in (
            ("true", True),
            ("1", True),
            ("false", False),
            ("0", False),
        ):
            with self.subTest(raw_value=raw_value):
                config_module = load_isolated_config(
                    {"GEMINI_ENABLED": raw_value}
                )
                self.assertIs(config_module.Config.GEMINI_ENABLED, expected)

    def test_gemini_positive_integer_settings_reject_invalid_values(self):
        setting_names = (
            "GEMINI_TIMEOUT_SECONDS",
            "GEMINI_ADVICE_CACHE_TTL_SECONDS",
            "GEMINI_ADVICE_MAX_REQUEST_BYTES",
        )

        for setting_name in setting_names:
            for invalid_value in ("0", "-1", "not-a-number"):
                with self.subTest(
                    setting_name=setting_name,
                    invalid_value=invalid_value,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        f"{setting_name} must be a positive integer",
                    ):
                        load_isolated_config({setting_name: invalid_value})


if __name__ == "__main__":
    unittest.main()
