import re

from flask import Blueprint, g, jsonify, request
from sqlalchemy.exc import IntegrityError

from extensions import db
from middleware import auth_required
from models import User
from utils.jwt import generate_tokens


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
MIN_PASSWORD_LENGTH = 8


def is_valid_email(email):
    return bool(email and EMAIL_PATTERN.fullmatch(email))


def normalize_email(email):
    return email.strip().lower() if isinstance(email, str) else ""


def find_user_by_email(email):
    return db.session.execute(db.select(User).filter_by(email=email)).scalar_one_or_none()


@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get("email"))
    password = data.get("password")

    if not is_valid_email(email):
        return jsonify({"error": "A valid email address is required."}), 400

    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        return jsonify({"error": "Password must be at least 8 characters long."}), 400

    existing_user = find_user_by_email(email)
    if existing_user:
        return jsonify({"error": "A user with this email already exists."}), 409

    user = User(email=email, password_hash="")
    user.set_password(password)

    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "A user with this email already exists."}), 409

    tokens = generate_tokens(user.id, {"email": user.email})

    return jsonify({"message": "User registered successfully.", "user": user.to_dict(), **tokens}), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get("email"))
    password = data.get("password")

    if not is_valid_email(email) or not isinstance(password, str):
        return jsonify({"error": "Invalid email or password."}), 401

    user = find_user_by_email(email)
    if not user or not user.check_password(password):
        return jsonify({"error": "Invalid email or password."}), 401

    tokens = generate_tokens(user.id, {"email": user.email})

    return jsonify({"message": "Login successful.", "user": user.to_dict(), **tokens}), 200


@auth_bp.route("/me", methods=["GET"])
@auth_required
def me():
    return jsonify({"user": g.current_user.to_dict()}), 200
