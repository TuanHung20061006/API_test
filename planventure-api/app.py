import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

db = SQLAlchemy()


def create_app():
    app = Flask(__name__)

    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")
    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "dev-jwt-secret-key")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{(BASE_DIR / 'planventure.db').as_posix()}",
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    cors_origins = os.getenv("CORS_ORIGINS", "*")
    CORS(app, origins=[origin.strip() for origin in cors_origins.split(",")])

    db.init_app(app)

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
