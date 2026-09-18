import asyncio
from datetime import date
from scrapers.serpapi_flights import scrape_serpapi

async def main():
    # 3 dates, 1 destination = 3 API calls
    test_dates = [date(2026, 9, 1), date(2026, 9, 9), date(2026, 9, 15)]

    results = await scrape_serpapi(
        departure_dates=test_dates,
        origins=["ARN"],
        destinations=["DXB"],
        trip_duration=4,
    )

    if not results:
        print("No results.")
        return

    print(f"\nTop 5 cheapest (out of {len(results)} options):\n")
    for trip in sorted(results, key=lambda t: t.total_price_aed)[:5]:
        out = trip.outbound
        print(
            f"{out.departure_datetime.strftime('%b %d')} | "
            f"{out.origin}->{out.destination} | "
            f"{out.airline} {out.flight_number or ''} | "
            f"stops: {out.stops} ({out.layover_airports or 'direct'}) | "
            f"{trip.total_price_aed:.0f} AED"
        )

asyncio.run(main())
