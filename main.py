import asyncio
from datetime import date, timedelta
from typing import List

import os
from dotenv import load_dotenv
load_dotenv()

from config import PRICE_CAP_AED, ORIGINS, DESTINATIONS, TRIP_DURATION_DAYS, SEARCH_START, SEARCH_END, AED_TO_SEK
from models import TripResult
from export import export_trips
from scrapers.serpapi_flights import scrape_serpapi
from scrapers.google_flights import scrape_google_flights
from notify import notify

# Switch between scrapers: "serpapi" or "google" (can also be set via SCRAPER env var)
SCRAPER = os.getenv("SCRAPER", "google")


def get_departure_dates() -> List[date]:
    start = date.fromisoformat(SEARCH_START)
    end = date.fromisoformat(SEARCH_END)
    # Past dates can't be picked in the Google Flights calendar, so never start before tomorrow
    d = max(start, date.today() + timedelta(days=1))
    dates = []
    # Only include dates where the return date still falls within the search window
    while d + timedelta(days=TRIP_DURATION_DAYS) <= end:
        dates.append(d)
        d += timedelta(days=1)
    return dates


def window_label() -> str:
    start = date.fromisoformat(SEARCH_START)
    end = date.fromisoformat(SEARCH_END)
    if (start.year, start.month) == (end.year, end.month):
        return start.strftime("%B %Y")
    return f"{start.strftime('%b')}-{end.strftime('%b %Y')}"


def build_summary(trips: List[TripResult], total_found: int) -> str:
    if total_found == 0:
        # Zero flights at ANY price means the scrape itself broke, not that everything is over the cap
        return "STK->UAE {} | SCRAPE FAILED: 0 flights found at any price. See the scrape-output artifact on the Actions run".format(window_label())

    if not trips:
        return "STK->UAE {} | No results found under {:,} AED".format(window_label(), PRICE_CAP_AED)

    cheapest = trips[0]
    lines = [
        f"STK->UAE | {window_label()} ({TRIP_DURATION_DAYS} days)",
        f"{len(trips)} results under {PRICE_CAP_AED:,} AED",
        f"Cheapest: {cheapest.outbound.origin}->{cheapest.outbound.destination} "
        f"{cheapest.outbound.departure_datetime.strftime('%b %d')} | "
        f"{cheapest.outbound.airline} | "
        f"{cheapest.total_price_aed:,.0f} AED",
        "See attached CSV for full list.",
    ]
    return "\n".join(lines)


async def run():
    departure_dates = get_departure_dates()
    if os.getenv("TEST_RUN", "false").lower() == "true":
        departure_dates = departure_dates[:2]
        print("TEST MODE: limiting to first 2 dates")
    print(f"Scanning {len(departure_dates)} departure dates across {len(ORIGINS)} origins x {len(DESTINATIONS)} destinations...")

    scrape_fn = scrape_google_flights if SCRAPER == "google" else scrape_serpapi
    all_trips = await scrape_fn(
        departure_dates=departure_dates,
        origins=ORIGINS,
        destinations=DESTINATIONS,
        trip_duration=TRIP_DURATION_DAYS,
    )

    # Attach SEK equivalent and filter by price cap
    filtered = []
    for trip in all_trips:
        trip.total_price_sek = round(trip.total_price_aed * AED_TO_SEK, 2)
        if trip.total_price_aed <= PRICE_CAP_AED:
            filtered.append(trip)

    filtered.sort(key=lambda t: t.total_price_aed)

    print(f"\n{len(filtered)} trips under {PRICE_CAP_AED} AED (out of {len(all_trips)} total found)")

    if not filtered:
        print("Nothing under the price cap. Sending notification anyway.")

    csv_path = export_trips(filtered if filtered else all_trips[:50])
    print(f"Saved to {csv_path}")

    summary = build_summary(filtered, len(all_trips))
    print(f"\nSummary:\n{summary}")

    try:
        notify(summary, csv_path)
    except Exception as e:
        print(f"Telegram notification failed: {e}\nCSV is saved at: {csv_path}")


if __name__ == "__main__":
    asyncio.run(run())
