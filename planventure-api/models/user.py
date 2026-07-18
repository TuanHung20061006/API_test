from datetime import datetime, timezone

from extensions import db
from utils.password import hash_password, verify_password


def timezone_now():
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    trips = db.relationship(
        "Trip",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy=True,
    )
    created_at = db.Column(db.DateTime(timezone=True), default=timezone_now, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=timezone_now,
        onupdate=timezone_now,
        nullable=False,
    )

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def set_password(self, password):
        self.password_hash = hash_password(password)

    def check_password(self, password):
        return verify_password(password, self.password_hash)

    def __repr__(self):
        return f"<User {self.email}>"
