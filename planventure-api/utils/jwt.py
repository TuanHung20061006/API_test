from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_jwt_identity,
    verify_jwt_in_request,
)
from flask_jwt_extended.exceptions import JWTExtendedException
from jwt import PyJWTError


def _identity_to_string(identity):
    if identity is None:
        raise ValueError("JWT identity is required.")

    return str(identity)


def generate_access_token(identity, additional_claims=None):
    return create_access_token(
        identity=_identity_to_string(identity),
        additional_claims=additional_claims or {},
    )


def generate_refresh_token(identity, additional_claims=None):
    return create_refresh_token(
        identity=_identity_to_string(identity),
        additional_claims=additional_claims or {},
    )


def generate_tokens(identity, additional_claims=None):
    return {
        "access_token": generate_access_token(identity, additional_claims),
        "refresh_token": generate_refresh_token(identity, additional_claims),
    }


def decode_jwt_token(token):
    try:
        return decode_token(token)
    except (JWTExtendedException, PyJWTError):
        return None


def validate_jwt_token(token):
    return decode_jwt_token(token) is not None


def get_current_user_id(optional=False):
    verify_jwt_in_request(optional=optional)

    identity = get_jwt_identity()
    if identity is None:
        return None

    try:
        return int(identity)
    except ValueError:
        return identity
