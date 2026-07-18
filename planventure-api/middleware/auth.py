from functools import wraps

from flask import g, jsonify
from flask_jwt_extended.exceptions import JWTExtendedException
from jwt import PyJWTError

from extensions import db
from models import User
from utils.jwt import get_current_user_id


def get_authenticated_user(optional=False):
    try:
        user_id = get_current_user_id(optional=optional)
    except (JWTExtendedException, PyJWTError):
        return None

    if user_id is None:
        return None

    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return None

    return db.session.get(User, user_id)


def auth_required(route_handler):
    @wraps(route_handler)
    def wrapped_route(*args, **kwargs):
        user = get_authenticated_user()
        if not user:
            return jsonify({"error": "Authentication required."}), 401

        g.current_user = user
        return route_handler(*args, **kwargs)

    return wrapped_route
