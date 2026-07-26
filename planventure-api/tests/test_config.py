import os
import unittest
from pathlib import Path
from unittest.mock import patch

from config import ProductionConfig, StagingConfig, get_config


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
        project_dir = Path(__file__).resolve().parents[1]

        for file_name in (".env.staging.example", ".env.production.example"):
            values = {}
            for line in (project_dir / file_name).read_text(encoding="utf-8").splitlines():
                if line and not line.startswith("#"):
                    name, value = line.split("=", 1)
                    values[name] = value

            with self.subTest(file_name=file_name):
                with patch.dict(os.environ, values, clear=True):
                    with self.assertRaises(RuntimeError):
                        get_config()


if __name__ == "__main__":
    unittest.main()
