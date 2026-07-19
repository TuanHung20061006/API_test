from datetime import timedelta


def generate_default_itinerary(start_date, end_date, destination):
    total_days = (end_date - start_date).days + 1

    return [
        {
            "day": day_number,
            "date": (start_date + timedelta(days=day_number - 1)).isoformat(),
            "title": f"Day {day_number} in {destination}",
            "activities": [],
            "notes": "",
        }
        for day_number in range(1, total_days + 1)
    ]
