import asyncio
from datetime import date
from scrapers.google_flights import scrape_google_flights

async def main():
    results = await scrape_google_flights(
        departure_dates=[date(2026, 9, 1), date(2026, 9, 9)],
        origins=["ARN"],
        destinations=["DXB"],
        trip_duration=4,
    )
    if not results:
        print("No results.")
        return
    print(f"\n{len(results)} results. Top 5:\n")
    for t in sorted(results, key=lambda x: x.total_price_aed)[:5]:
        out = t.outbound
        print(f"{out.departure_datetime.strftime('%b %d')} | {out.airline} | stops:{out.stops} via {out.layover_airports or 'direct'} | {t.total_price_aed:.0f} AED")

asyncio.run(main())
