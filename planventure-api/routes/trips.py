from datetime import date

from flask import Blueprint, g, jsonify, request

from extensions import db
from middleware import auth_required
from models import Trip
from utils.itinerary import generate_default_itinerary


trips_bp = Blueprint("trips", __name__, url_prefix="/trip")


def parse_date(value, field_name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required.")

    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be in YYYY-MM-DD format.") from exc


def validate_trip_payload(data, partial=False):
    errors = {}
    trip_data = {}

    destination = data.get("destination")
    if destination is not None:
        if not isinstance(destination, str) or not destination.strip():
            errors["destination"] = "Destination must be a non-empty string."
        else:
            trip_data["destination"] = destination.strip()
    elif not partial:
        errors["destination"] = "Destination is required."

    for field_name in ("start_date", "end_date"):
        value = data.get(field_name)
        if value is not None:
            try:
                trip_data[field_name] = parse_date(value, field_name)
            except ValueError as exc:
                errors[field_name] = str(exc)
        elif not partial:
            errors[field_name] = f"{field_name} is required."

    coordinates = data.get("coordinates")
    if coordinates is not None:
        if not isinstance(coordinates, dict):
            errors["coordinates"] = "Coordinates must be an object."
        else:
            trip_data["coordinates"] = coordinates

    itinerary = data.get("itinerary")
    if itinerary is not None:
        if not isinstance(itinerary, (dict, list)):
            errors["itinerary"] = "Itinerary must be an object or an array."
        else:
            trip_data["itinerary"] = itinerary

    return trip_data, errors


def find_current_user_trip(trip_id):
    return db.session.execute(
        db.select(Trip).filter_by(id=trip_id, user_id=g.current_user.id)
    ).scalar_one_or_none()


@trips_bp.route("", methods=["GET"])
@auth_required
def list_trips():
    trips = db.session.execute(
        db.select(Trip)
        .filter_by(user_id=g.current_user.id)
        .order_by(Trip.start_date.asc(), Trip.id.asc())
    ).scalars()

    return jsonify({"trips": [trip.to_dict() for trip in trips]}), 200


@trips_bp.route("", methods=["POST"])
@auth_required
def create_trip():
    data = request.get_json(silent=True) or {}
    trip_data, errors = validate_trip_payload(data)

    if errors:
        return jsonify({"errors": errors}), 400

    if trip_data["start_date"] > trip_data["end_date"]:
        return jsonify({"error": "start_date must be before or equal to end_date."}), 400

    if "itinerary" not in trip_data:
        trip_data["itinerary"] = generate_default_itinerary(
            trip_data["start_date"],
            trip_data["end_date"],
            trip_data["destination"],
        )

    trip = Trip(user_id=g.current_user.id, **trip_data)

    db.session.add(trip)
    db.session.commit()

    return jsonify({"message": "Trip created successfully.", "trip": trip.to_dict()}), 201


@trips_bp.route("/<int:trip_id>", methods=["GET"])
@auth_required
def get_trip(trip_id):
    trip = find_current_user_trip(trip_id)
    if not trip:
        return jsonify({"error": "Trip not found."}), 404

    return jsonify({"trip": trip.to_dict()}), 200


@trips_bp.route("/<int:trip_id>", methods=["PUT", "PATCH"])
@auth_required
def update_trip(trip_id):
    trip = find_current_user_trip(trip_id)
    if not trip:
        return jsonify({"error": "Trip not found."}), 404

    data = request.get_json(silent=True) or {}
    trip_data, errors = validate_trip_payload(data, partial=True)

    if errors:
        return jsonify({"errors": errors}), 400

    if not trip_data:
        return jsonify({"error": "No valid trip fields were provided."}), 400

    start_date = trip_data.get("start_date", trip.start_date)
    end_date = trip_data.get("end_date", trip.end_date)
    if start_date > end_date:
        return jsonify({"error": "start_date must be before or equal to end_date."}), 400

    for field_name, value in trip_data.items():
        setattr(trip, field_name, value)

    db.session.commit()

    return jsonify({"message": "Trip updated successfully.", "trip": trip.to_dict()}), 200


@trips_bp.route("/<int:trip_id>", methods=["DELETE"])
@auth_required
def delete_trip(trip_id):
    trip = find_current_user_trip(trip_id)
    if not trip:
        return jsonify({"error": "Trip not found."}), 404

    db.session.delete(trip)
    db.session.commit()

    return jsonify({"message": "Trip deleted successfully."}), 200
