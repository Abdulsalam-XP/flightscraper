import asyncio
import httpx
import os
from datetime import date, timedelta, datetime
from typing import List, Optional
from dotenv import load_dotenv
from models import Flight, TripResult

load_dotenv()

SERPAPI_KEY = os.getenv("SERPAPI_KEY")
SERPAPI_URL = "https://serpapi.com/search"

# 1 EUR ≈ 3.97 AED, 1 USD ≈ 3.67 AED — SerpAPI returns USD by default
USD_TO_AED = 3.67


def _parse_leg(leg: dict, price_aed: float, total_legs: int) -> Flight:
    dep = leg["departure_airport"]
    arr = leg["arrival_airport"]

    dep_dt = datetime.strptime(dep["time"], "%Y-%m-%d %H:%M")
    arr_dt = datetime.strptime(arr["time"], "%Y-%m-%d %H:%M")

    return Flight(
        airline=leg.get("airline", "Unknown"),
        flight_number=leg.get("flight_number", "").replace(" ", ""),
        origin=dep["id"],
        destination=arr["id"],
        departure_datetime=dep_dt,
        arrival_datetime=arr_dt,
        price_aed=price_aed,
        currency_original="AED",
        stops=total_legs - 1,
    )


def _parse_option(option: dict, origin: str, destination: str, dep_date: date, ret_date: date) -> Optional[TripResult]:
    try:
        price_usd = option.get("price", 0)
        total_aed = round(price_usd * USD_TO_AED, 2)

        legs = option.get("flights", [])
        if not legs:
            return None

        # Build outbound flight from first leg (use final arrival for destination)
        first_leg = legs[0]
        last_leg = legs[-1]

        dep_dt = datetime.strptime(first_leg["departure_airport"]["time"], "%Y-%m-%d %H:%M")
        arr_dt = datetime.strptime(last_leg["arrival_airport"]["time"], "%Y-%m-%d %H:%M")

        layovers = option.get("layovers", [])
        layover_codes = ",".join(l["id"] for l in layovers) if layovers else None

        outbound = Flight(
            airline=first_leg.get("airline", "Unknown"),
            flight_number=first_leg.get("flight_number", "").replace(" ", ""),
            origin=first_leg["departure_airport"]["id"],
            destination=last_leg["arrival_airport"]["id"],
            departure_datetime=dep_dt,
            arrival_datetime=arr_dt,
            price_aed=round(total_aed / 2, 2),  # split evenly; refined by return leg if available
            currency_original="AED",
            stops=len(layovers),
            layover_airports=layover_codes,
            booking_url=f"https://www.google.com/flights#search;f={origin};t={destination};d={dep_date};r={ret_date};tt=r",
        )

        # Build a placeholder inbound flight — SerpAPI round-trip returns total price
        # We create a symmetric inbound with same airline and swapped airports
        inbound = Flight(
            airline=first_leg.get("airline", "Unknown"),
            flight_number=None,
            origin=last_leg["arrival_airport"]["id"],
            destination=first_leg["departure_airport"]["id"],
            departure_datetime=datetime(ret_date.year, ret_date.month, ret_date.day, 12, 0),
            arrival_datetime=datetime(ret_date.year, ret_date.month, ret_date.day, 18, 0),
            price_aed=round(total_aed / 2, 2),
            currency_original="AED",
            stops=len(layovers),
            layover_airports=layover_codes,
            booking_url=outbound.booking_url,
        )

        return TripResult(
            outbound=outbound,
            inbound=inbound,
            total_price_aed=total_aed,
        )

    except Exception as e:
        print(f"[serpapi] parse error: {e}")
        return None


async def _search_route(
    client: httpx.AsyncClient,
    origin: str,
    destination: str,
    dep_date: date,
    ret_date: date,
) -> List[TripResult]:

    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": destination,
        "outbound_date": dep_date.strftime("%Y-%m-%d"),
        "return_date": ret_date.strftime("%Y-%m-%d"),
        "currency": "USD",
        "hl": "en",
        "adults": "1",
        "type": "1",  # round trip
        "api_key": SERPAPI_KEY,
    }

    try:
        resp = await client.get(SERPAPI_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[serpapi] request error {origin}->{destination} {dep_date}: {e}")
        return []

    results = []
    for option in data.get("best_flights", []) + data.get("other_flights", []):
        trip = _parse_option(option, origin, destination, dep_date, ret_date)
        if trip:
            results.append(trip)

    return results


async def scrape_serpapi(
    departure_dates: List[date],
    origins: List[str],
    destinations: List[str],
    trip_duration: int,
) -> List[TripResult]:

    all_results: List[TripResult] = []

    async with httpx.AsyncClient() as client:
        for origin in origins:
            for destination in destinations:
                for dep_date in departure_dates:
                    ret_date = dep_date + timedelta(days=trip_duration)
                    print(f"[serpapi] {origin}->{destination} {dep_date} / return {ret_date}")

                    trips = await _search_route(client, origin, destination, dep_date, ret_date)
                    all_results.extend(trips)

                    # Respect rate limits
                    await asyncio.sleep(0.5)

    print(f"[serpapi] found {len(all_results)} total trip options")
    return all_results
