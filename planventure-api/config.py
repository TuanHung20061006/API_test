import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DEFAULT_CORS_ORIGINS = ",".join(
    [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
)


def parse_cors_origins():
    cors_origins = os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in cors_origins.split(",") if origin.strip()]


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_positive_int(name, default):
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc

    if value <= 0:
        raise ValueError(f"{name} must be a positive integer.")

    return value


def is_placeholder(value):
    normalized_value = value.strip().lower()
    return any(
        marker in normalized_value
        for marker in ("replace-with", "replace-me", "your-")
    )


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    JWT_SECRET_KEY = os.getenv(
        "JWT_SECRET_KEY",
        "dev-jwt-secret-key-change-me-32-bytes-minimum",
    )
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(
        minutes=int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES_MINUTES", "60"))
    )
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(
        days=int(os.getenv("JWT_REFRESH_TOKEN_EXPIRES_DAYS", "30"))
    )
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{(BASE_DIR / 'planventure.db').as_posix()}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    CORS_ORIGINS = parse_cors_origins()
    RATELIMIT_ENABLED = env_bool("RATELIMIT_ENABLED", True)
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH_BYTES", str(1024 * 1024)))
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "").strip()
    WEATHER_API_BASE_URL = os.getenv(
        "WEATHER_API_BASE_URL", "https://api.weatherapi.com/v1"
    ).rstrip("/")
    WEATHER_API_CONNECT_TIMEOUT_SECONDS = float(
        os.getenv("WEATHER_API_CONNECT_TIMEOUT_SECONDS", "3.05")
    )
    WEATHER_API_READ_TIMEOUT_SECONDS = float(
        os.getenv("WEATHER_API_READ_TIMEOUT_SECONDS", "8")
    )
    WEATHER_CACHE_TTL_SECONDS = int(os.getenv("WEATHER_CACHE_TTL_SECONDS", "900"))
    WEATHER_RATE_LIMIT = os.getenv(
        "WEATHER_RATE_LIMIT", "30 per minute;300 per day"
    )
    GEMINI_ENABLED = env_bool("GEMINI_ENABLED", False)
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
    GEMINI_TIMEOUT_SECONDS = env_positive_int("GEMINI_TIMEOUT_SECONDS", 30)
    GEMINI_ADVICE_CACHE_TTL_SECONDS = env_positive_int(
        "GEMINI_ADVICE_CACHE_TTL_SECONDS", 3600
    )
    GEMINI_ADVICE_RATE_LIMIT = os.getenv(
        "GEMINI_ADVICE_RATE_LIMIT", "5 per hour"
    ).strip()
    GEMINI_ADVICE_MAX_REQUEST_BYTES = env_positive_int(
        "GEMINI_ADVICE_MAX_REQUEST_BYTES", 32768
    )
    CACHE_TYPE = os.getenv("CACHE_TYPE", "SimpleCache")
    CACHE_DEFAULT_TIMEOUT = int(os.getenv("CACHE_DEFAULT_TIMEOUT", "300"))
    CACHE_KEY_PREFIX = os.getenv("CACHE_KEY_PREFIX", "planventure:")
    CACHE_REDIS_URL = os.getenv(
        "CACHE_REDIS_URL", "redis://localhost:6379/1"
    )
    JSON_SORT_KEYS = False


class DevelopmentConfig(Config):
    DEBUG = True


class DeploymentConfig(Config):
    DEBUG = False
    PREFERRED_URL_SCHEME = "https"
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    RATELIMIT_HEADERS_ENABLED = True
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    @classmethod
    def validate(cls):
        invalid = []
        secret_key = os.getenv("SECRET_KEY", "")
        jwt_secret_key = os.getenv("JWT_SECRET_KEY", "")
        weather_api_key = os.getenv("WEATHER_API_KEY", "").strip()
        database_url = os.getenv("DATABASE_URL", "").strip()
        cors_origins = os.getenv("CORS_ORIGINS", "").strip()
        rate_limit_storage_uri = os.getenv(
            "RATELIMIT_STORAGE_URI", "memory://"
        ).strip()
        cache_type = os.getenv("CACHE_TYPE", "SimpleCache").strip()
        cache_redis_url = os.getenv("CACHE_REDIS_URL", "").strip()

        if (
            len(secret_key) < 32
            or secret_key == "dev-secret-key"
            or is_placeholder(secret_key)
        ):
            invalid.append("SECRET_KEY")
        if (
            len(jwt_secret_key) < 32
            or jwt_secret_key.startswith("dev-jwt-secret")
            or is_placeholder(jwt_secret_key)
        ):
            invalid.append("JWT_SECRET_KEY")
        if not weather_api_key or is_placeholder(weather_api_key):
            invalid.append("WEATHER_API_KEY")
        if (
            not database_url.startswith(("postgresql://", "postgresql+psycopg://"))
            or is_placeholder(database_url)
        ):
            invalid.append("DATABASE_URL")
        if (
            not cors_origins
            or "localhost" in cors_origins.lower()
            or "127.0.0.1" in cors_origins
            or "example.com" in cors_origins.lower()
        ):
            invalid.append("CORS_ORIGINS")
        if not env_bool("RATELIMIT_ENABLED", True):
            invalid.append("RATELIMIT_ENABLED")
        if not rate_limit_storage_uri.lower().startswith(("redis://", "rediss://")):
            invalid.append("RATELIMIT_STORAGE_URI")
        if cache_type.lower() != "rediscache":
            invalid.append("CACHE_TYPE")
        if not cache_redis_url.lower().startswith(("redis://", "rediss://")):
            invalid.append("CACHE_REDIS_URL")

        if invalid:
            raise RuntimeError(
                "Missing or insecure deployment environment variables: "
                + ", ".join(invalid)
            )


class StagingConfig(DeploymentConfig):
    pass


class ProductionConfig(DeploymentConfig):
    pass


config_by_name = {
    "development": DevelopmentConfig,
    "staging": StagingConfig,
    "production": ProductionConfig,
}


def get_config():
    environment = os.getenv("FLASK_ENV", "development").lower()
    if environment not in config_by_name:
        raise RuntimeError(f"Unsupported FLASK_ENV: {environment}")

    config = config_by_name[environment]
    if issubclass(config, DeploymentConfig):
        config.validate()
    return config
