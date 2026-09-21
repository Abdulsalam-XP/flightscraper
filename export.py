import csv
import os
from datetime import datetime
from typing import List
from config import ORIGIN_TZ_LABEL, DESTINATION_TZ_LABEL
from models import TripResult


# Times are local to each airport, so the headers say which: e.g. "outbound_departs (Sweden time)"
OUTBOUND_DEPARTS = f"outbound_departs ({ORIGIN_TZ_LABEL})"
OUTBOUND_ARRIVES = f"outbound_arrives ({DESTINATION_TZ_LABEL})"
RETURN_DEPARTS = f"return_departs ({DESTINATION_TZ_LABEL})"
RETURN_ARRIVES = f"return_arrives ({ORIGIN_TZ_LABEL})"

FIELDNAMES = [
    "departure_date",
    "return_date",
    "origin",
    "destination",
    "airline",
    "flight_no",
    OUTBOUND_DEPARTS,
    OUTBOUND_ARRIVES,
    RETURN_DEPARTS,
    RETURN_ARRIVES,
    "stops",
    "via",
    "price_aed",
    "price_sek",
    "price_usd",
    "book",
]


def fmt_time(dt) -> str:
    # 12-hour with the 24-hour equivalent next to it, e.g. "6:05 PM (18:05)"
    return f"{dt.strftime('%I:%M %p').lstrip('0')} ({dt.strftime('%H:%M')})"


def export_trips(trips: List[TripResult], output_dir: str = "output") -> str:
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d")
    filepath = os.path.join(output_dir, f"flights_{timestamp}.csv")

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for trip in sorted(trips, key=lambda t: t.total_price_aed):
            out = trip.outbound
            ret = trip.inbound
            writer.writerow({
                "departure_date": out.departure_datetime.strftime("%b %d, %Y"),
                "return_date": trip.inbound.departure_datetime.strftime("%b %d, %Y"),
                "origin": out.origin,
                "destination": out.destination,
                "airline": out.airline,
                "flight_no": out.flight_number or "",
                OUTBOUND_DEPARTS: fmt_time(out.departure_datetime),
                OUTBOUND_ARRIVES: fmt_time(out.arrival_datetime),
                RETURN_DEPARTS: fmt_time(ret.departure_datetime) if trip.return_times_known else "",
                RETURN_ARRIVES: fmt_time(ret.arrival_datetime) if trip.return_times_known else "",
                "stops": out.stops,
                "via": out.layover_airports or "Direct",
                "price_aed": round(trip.total_price_aed, 2),
                "price_sek": round(trip.total_price_sek, 2) if trip.total_price_sek else "",
                "price_usd": round(trip.total_price_usd, 2) if trip.total_price_usd else "",
                "book": out.booking_url or "",
            })

    return filepath
