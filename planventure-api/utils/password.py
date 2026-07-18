import bcrypt


BCRYPT_ROUNDS = 12
BCRYPT_MAX_PASSWORD_BYTES = 72


def generate_salt(rounds=BCRYPT_ROUNDS):
    return bcrypt.gensalt(rounds=rounds).decode("utf-8")


def _password_to_bytes(password):
    if not isinstance(password, str) or not password:
        raise ValueError("Password is required.")

    password_bytes = password.encode("utf-8")
    if len(password_bytes) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError("Password must be 72 bytes or fewer.")

    return password_bytes


def hash_password(password, salt=None):
    password_bytes = _password_to_bytes(password)
    salt_bytes = salt.encode("utf-8") if salt else generate_salt().encode("utf-8")

    return bcrypt.hashpw(password_bytes, salt_bytes).decode("utf-8")


def verify_password(password, password_hash):
    if not password_hash:
        return False

    try:
        return bcrypt.checkpw(_password_to_bytes(password), password_hash.encode("utf-8"))
    except ValueError:
        return False
