from datetime import datetime, timezone

from app import db


def timezone_now():
    return datetime.now(timezone.utc)


class Trip(db.Model):
    __tablename__ = "trips"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    destination = db.Column(db.String(255), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    coordinates = db.Column(db.JSON, nullable=True)
    itinerary = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=timezone_now, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=timezone_now,
        onupdate=timezone_now,
        nullable=False,
    )

    user = db.relationship("User", back_populates="trips")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "destination": self.destination,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "coordinates": self.coordinates,
            "itinerary": self.itinerary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<Trip {self.destination}>"
