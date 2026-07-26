import logging

from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_jwt_extended import verify_jwt_in_request
from sqlalchemy import text
from werkzeug.exceptions import HTTPException

from config import get_config
from extensions import cache, db, jwt, limiter, migrate


def create_app():
    app = Flask(__name__)
    app.config.from_object(get_config())

    CORS(
        app,
        resources={r"/*": {"origins": app.config["CORS_ORIGINS"]}},
        supports_credentials=True,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    db.init_app(app)
    jwt.init_app(app)
    migrate.init_app(app, db)

    @app.before_request
    def authenticate_weather_request_before_rate_limit():
        if request.endpoint == "weather.get_trip_weather":
            verify_jwt_in_request()

    limiter.init_app(app)
    cache.init_app(app)

    @jwt.unauthorized_loader
    def handle_missing_token(reason):
        return jsonify({"error": "Authentication required."}), 401

    @jwt.invalid_token_loader
    def handle_invalid_token(reason):
        return jsonify({"error": "Invalid authentication token."}), 401

    @jwt.expired_token_loader
    def handle_expired_token(jwt_header, jwt_payload):
        return jsonify({"error": "Authentication token has expired."}), 401

    @jwt.revoked_token_loader
    def handle_revoked_token(jwt_header, jwt_payload):
        return jsonify({"error": "Authentication token has been revoked."}), 401

    logging.basicConfig(
        level=logging.DEBUG if app.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # WeatherAPI authenticates through a query parameter. Prevent the HTTP
    # transport logger from exposing request URLs (and therefore provider keys).
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    from routes import auth_bp, trips_bp, weather_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(trips_bp)
    app.register_blueprint(weather_bp)

    @app.route("/")
    def home():
        return jsonify({"message": "Welcome to PlanVenture API"})

    @app.route("/health")
    def health_check():
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            app.logger.exception("Database health check failed")
            return jsonify({"status": "unhealthy", "database": "unavailable"}), 503
        return jsonify({"status": "healthy", "database": "available"})

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        return jsonify({"error": error.description}), error.code

    @app.errorhandler(429)
    def handle_rate_limit_exceeded(error):
        response = jsonify(
            {
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": "Bạn đã gửi quá nhiều yêu cầu. Vui lòng thử lại sau.",
                }
            }
        )
        retry_after = error.get_response().headers.get("Retry-After")
        if retry_after:
            response.headers["Retry-After"] = retry_after
        return response, 429

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        app.logger.exception("Unhandled application error", exc_info=error)
        return jsonify({"error": "An internal server error occurred."}), 500

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
