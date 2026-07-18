import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify
from flask_cors import CORS

from extensions import db, jwt


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

def create_app():
    app = Flask(__name__)

    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")
    app.config["JWT_SECRET_KEY"] = os.getenv(
        "JWT_SECRET_KEY",
        "dev-jwt-secret-key-change-me-32-bytes-minimum",
    )
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(
        minutes=int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES_MINUTES", "60"))
    )
    app.config["JWT_REFRESH_TOKEN_EXPIRES"] = timedelta(
        days=int(os.getenv("JWT_REFRESH_TOKEN_EXPIRES_DAYS", "30"))
    )
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{(BASE_DIR / 'planventure.db').as_posix()}",
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    cors_origins = os.getenv("CORS_ORIGINS", "*")
    CORS(app, origins=[origin.strip() for origin in cors_origins.split(",")])

    db.init_app(app)
    jwt.init_app(app)

    from routes import auth_bp

    app.register_blueprint(auth_bp)

    @app.route("/")
    def home():
        return jsonify({"message": "Welcome to PlanVenture API"})

    @app.route("/health")
    def health_check():
        return jsonify({"status": "healthy"})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
