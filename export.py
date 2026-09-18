import csv
import os
from datetime import datetime
from typing import List
from models import TripResult


FIELDNAMES = [
    "departure_date",
    "return_date",
    "origin",
    "destination",
    "airline",
    "flight_no",
    "outbound_departs",
    "outbound_arrives",
    "stops",
    "via",
    "price_aed",
    "price_sek",
    "book",
]


def fmt_time(dt) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def export_trips(trips: List[TripResult], output_dir: str = "output") -> str:
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d")
    filepath = os.path.join(output_dir, f"flights_{timestamp}.csv")

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for trip in sorted(trips, key=lambda t: t.total_price_aed):
            out = trip.outbound
            writer.writerow({
                "departure_date": out.departure_datetime.strftime("%b %d, %Y"),
                "return_date": trip.inbound.departure_datetime.strftime("%b %d, %Y"),
                "origin": out.origin,
                "destination": out.destination,
                "airline": out.airline,
                "flight_no": out.flight_number or "",
                "outbound_departs": fmt_time(out.departure_datetime),
                "outbound_arrives": fmt_time(out.arrival_datetime),
                "stops": out.stops,
                "via": out.layover_airports or "Direct",
                "price_aed": round(trip.total_price_aed, 2),
                "price_sek": round(trip.total_price_sek, 2) if trip.total_price_sek else "",
                "book": out.booking_url or "",
            })

    return filepath
