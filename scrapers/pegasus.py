import asyncio
import json
from datetime import date, timedelta
from typing import List, Optional
from playwright.async_api import async_playwright, Page
from models import Flight, TripResult

EUR_TO_AED = 3.97  # update periodically

AVAILABILITY_URL = "https://web.flypgs.com/pegasus/availability"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "x-platform": "web",
    "x-version": "3.71.0",
    "origin": "https://web.flypgs.com",
    "referer": "https://web.flypgs.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
}


def _build_payload(dep_port: str, arr_port: str, dep_date: date, ret_date: date) -> dict:
    return {
        "flightSearchList": [{
            "departurePort": dep_port,
            "arrivalPort": arr_port,
            "departureDate": dep_date.strftime("%Y-%m-%d"),
            "returnDate": ret_date.strftime("%Y-%m-%d"),
        }],
        "adultCount": 1,
        "childCount": 0,
        "infantCount": 0,
        "currency": "EUR",
        "dateOption": 1,
        "ffRedemption": False,
        "totalPoints": None,
        "personnelFlightSearch": False,
        "expatPnr": False,
        "operationCode": "TK",
        "affiliate": {"id": None},
        "bookingType": "BOOKING",
    }


def _parse_flight(segment: dict, is_connecting: bool, next_segment: Optional[dict]) -> Flight:
    dep_loc = segment["departureLocation"]
    arr_loc = segment.get("finalArrivalLocation") or segment["arrivalLocation"]

    layovers = None
    stops = 0
    if is_connecting and next_segment:
        stops = 1
        layovers = segment["arrivalLocation"]["portCode"]

    price_eur = segment["fare"]["shownFare"]["amount"]
    price_aed = round(price_eur * EUR_TO_AED, 2)

    duration_vals = segment.get("flightDuration", {}).get("values", ["0", "0"])

    from datetime import datetime
    return Flight(
        airline="Pegasus",
        flight_number=f"PC{segment['flightNo']}",
        origin=dep_loc["portCode"],
        destination=arr_loc["portCode"],
        departure_datetime=datetime.fromisoformat(segment["departureDateTime"]),
        arrival_datetime=datetime.fromisoformat(
            next_segment["arrivalDateTime"] if next_segment else segment["arrivalDateTime"]
        ),
        price_aed=price_aed,
        currency_original="EUR",
        stops=stops,
        layover_airports=layovers,
        booking_url=(
            f"https://web.flypgs.com/booking?departurePort={dep_loc['portCode']}"
            f"&arrivalPort={arr_loc['portCode']}"
            f"&departureDate={segment['departureDateTime'][:10]}"
            f"&adultCount=1&currency=EUR&dateOption=1"
        ),
    )


def _parse_route(route_data: dict) -> List[Flight]:
    flights = []
    for day in route_data.get("dailyFlightList", []):
        flight_list = day.get("flightList", [])
        if not flight_list:
            continue

        # Group segments: consecutive segments sharing the same booking = one journey
        # Pegasus returns each leg separately; connecting flights appear as consecutive entries
        i = 0
        while i < len(flight_list):
            seg = flight_list[i]
            next_seg = None

            # Detect connection: arrival port of this seg != final destination
            route_dest = route_data["arrival"]["portCode"]
            if seg["arrivalLocation"]["portCode"] != route_dest and i + 1 < len(flight_list):
                next_seg = flight_list[i + 1]
                i += 2
            else:
                i += 1

            try:
                flight = _parse_flight(seg, next_seg is not None, next_seg)
                flights.append(flight)
            except Exception as e:
                print(f"[pegasus] failed to parse segment: {e}")

    return flights


async def _intercept_search(page, dep_port: str, arr_port: str, dep_date: date, ret_date: date) -> Optional[dict]:
    """Navigate to the Pegasus booking page and intercept the availability API response."""
    captured: dict = {}

    async def handle_response(response):
        if "availability" in response.url and response.status == 200:
            try:
                captured["data"] = await response.json()
            except Exception:
                pass

    page.on("response", handle_response)

    url = (
        f"https://web.flypgs.com/booking"
        f"?language=en&adultCount=1"
        f"&departurePort={dep_port}&arrivalPort={arr_port}"
        f"&departureDate={dep_date.strftime('%Y-%m-%d')}"
        f"&returnDate={ret_date.strftime('%Y-%m-%d')}"
        f"&currency=EUR&dateOption=1"
    )

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        # Wait for the availability call to complete (up to 15s)
        for _ in range(30):
            if "data" in captured:
                break
            await asyncio.sleep(0.5)
    except Exception as e:
        print(f"[pegasus] navigation error: {e}")
    finally:
        page.remove_listener("response", handle_response)

    if "data" not in captured:
        print(f"[pegasus] no availability response captured for {dep_port}->{arr_port} {dep_date}")
    return captured.get("data")


async def scrape_pegasus(
    departure_dates: List[date],
    origins: List[str],
    destinations: List[str],
    trip_duration: int,
) -> List[TripResult]:

    results: List[TripResult] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,  # visible mode bypasses most bot detection
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=HEADERS["user-agent"],
            locale="en-GB",
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = await context.new_page()

        for origin in origins:
            for destination in destinations:
                print(f"[pegasus] scraping {origin} -> {destination}")
                outbound_flights_by_date: dict[str, List[Flight]] = {}
                inbound_flights_by_date: dict[str, List[Flight]] = {}

                for dep_date in departure_dates:
                    ret_date = dep_date + timedelta(days=trip_duration)

                    data = await _intercept_search(page, origin, destination, dep_date, ret_date)
                    if not data:
                        await asyncio.sleep(2)
                        continue

                    dep_routes = data.get("departureRouteList", [])
                    ret_routes = data.get("returnRouteList", [])

                    for route in dep_routes:
                        flights = _parse_route(route)
                        for f in flights:
                            key = f.departure_datetime.date().isoformat()
                            outbound_flights_by_date.setdefault(key, []).append(f)

                    for route in ret_routes:
                        flights = _parse_route(route)
                        for f in flights:
                            key = f.departure_datetime.date().isoformat()
                            inbound_flights_by_date.setdefault(key, []).append(f)

                    await asyncio.sleep(1.5)

                # Pair outbound + inbound flights
                for dep_date in departure_dates:
                    ret_date = dep_date + timedelta(days=trip_duration)
                    outbounds = outbound_flights_by_date.get(dep_date.isoformat(), [])
                    inbounds = inbound_flights_by_date.get(ret_date.isoformat(), [])

                    for out in outbounds:
                        for inb in inbounds:
                            total_aed = out.price_aed + inb.price_aed
                            results.append(TripResult(
                                outbound=out,
                                inbound=inb,
                                total_price_aed=total_aed,
                            ))

        await browser.close()

    print(f"[pegasus] found {len(results)} trip combinations")
    return results
