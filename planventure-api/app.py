from flask import Flask, jsonify
from flask_cors import CORS

from config import get_config
from extensions import db, jwt


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

    from routes import auth_bp, trips_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(trips_bp)

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
