import asyncio
import os
import re
from datetime import date, timedelta, datetime
from typing import List, Optional
from playwright.async_api import async_playwright, Page
from config import PRICE_CAP_AED
from models import Flight, TripResult

USD_TO_AED = 3.67

# Return times cost a click-through per card (~5s each), so per search only the
# cheapest few cards under the price cap get one — those are the rows that reach the CSV
MAX_RETURN_LOOKUPS = 3

# A failing lookup burns ~45s in timeouts. If Google serves a page variant where they all fail,
# that would push the nightly run past the CI timeout, so give up on lookups after a failure streak
MAX_RETURN_LOOKUP_FAILURES = 5
_return_lookup_failures = 0


def _record_return_lookup(ok: bool):
    global _return_lookup_failures
    _return_lookup_failures = 0 if ok else _return_lookup_failures + 1
    if _return_lookup_failures == MAX_RETURN_LOOKUP_FAILURES:
        print(f"[google_flights] {MAX_RETURN_LOOKUP_FAILURES} return lookups failed in a row, skipping them for the rest of the run")


def _return_lookups_enabled() -> bool:
    return _return_lookup_failures < MAX_RETURN_LOOKUP_FAILURES

DEBUG_DIR = os.path.join("output", "debug")
MAX_DEBUG_DUMPS = 5
_debug_dumps = 0


async def _dump_debug(page: Page, tag: str):
    """Record which page we were actually on when a search failed (URL, title, screenshot, HTML)."""
    global _debug_dumps
    try:
        print(f"[google_flights] failed on url={page.url} title={await page.title()!r}")
        if _debug_dumps >= MAX_DEBUG_DUMPS:
            return
        _debug_dumps += 1
        os.makedirs(DEBUG_DIR, exist_ok=True)
        await page.screenshot(path=os.path.join(DEBUG_DIR, f"{tag}.png"), full_page=True)
        with open(os.path.join(DEBUG_DIR, f"{tag}.html"), "w", encoding="utf-8") as f:
            f.write(await page.content())
    except Exception as e:
        print(f"[google_flights] debug dump failed: {e}")


def _parse_price_usd(text: str) -> Optional[float]:
    m = re.search(r"\$(\d[\d,]*)", text)
    return float(m.group(1).replace(",", "")) if m else None


def _parse_time(text: str, base_date: date) -> datetime:
    for fmt in ("%I:%M %p", "%I:%M%p"):
        try:
            t = datetime.strptime(text.strip().upper(), fmt)
            return datetime(base_date.year, base_date.month, base_date.day, t.hour, t.minute)
        except ValueError:
            continue
    return datetime(base_date.year, base_date.month, base_date.day, 12, 0)


async def _dismiss_consent(page: Page):
    for sel in ["button:has-text('Accept all')", "[aria-label='Accept all']"]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await asyncio.sleep(1)
                return
        except Exception:
            pass


async def _search_one(page: Page, origin: str, destination: str, dep_date: date, ret_date: date) -> bool:
    """Fill origin, destination, and dates then wait for results. Returns True on success."""
    try:
        # Origin. The label is exactly "Where from?" only when Google pre-fills the origin from
        # the IP's location; when it can't, the label is "Where from? " — so match by prefix.
        await page.click('input[aria-label^="Where from?"]', timeout=10000)
        await asyncio.sleep(0.4)
        await page.keyboard.press("Control+A")
        await page.keyboard.type(origin, delay=80)
        await asyncio.sleep(1.5)
        await page.keyboard.press("ArrowDown")
        await asyncio.sleep(0.3)
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.8)

        # Click destination input using stable class selector (second .cQnuXe.k0gFV input)
        await page.locator('div.cQnuXe.k0gFV input').nth(1).click(timeout=8000)
        await asyncio.sleep(0.4)
        await page.keyboard.type(destination, delay=80)
        await asyncio.sleep(1.5)
        await page.keyboard.press("ArrowDown")
        await asyncio.sleep(0.3)
        await page.keyboard.press("Enter")
        await asyncio.sleep(1.5)  # calendar opens after destination selected

        # Click the departure date field to ensure calendar is open
        await page.click('[aria-label="Departure"], [placeholder="Departure"]', timeout=8000)
        await asyncio.sleep(1.5)

        # Click departure date cell directly in the calendar
        dep_aria = f"{dep_date.strftime('%A')}, {dep_date.strftime('%B')} {dep_date.day}, {dep_date.year}"
        await page.click(f'[aria-label="{dep_aria}"]', timeout=8000)
        await asyncio.sleep(0.8)

        # Click return date cell
        ret_aria = f"{ret_date.strftime('%A')}, {ret_date.strftime('%B')} {ret_date.day}, {ret_date.year}"
        await page.click(f'[aria-label="{ret_aria}"]', timeout=8000)
        await asyncio.sleep(0.8)

        # Click Done via JS (button is off-screen)
        await page.evaluate('document.querySelector(\'button[jsname="McfNlf"]\').click()')
        await asyncio.sleep(1)

        # Click Search button
        await page.click('button:has-text("Search")', timeout=6000)
        await asyncio.sleep(5)

        # If Google shows an error page, reload the page
        try:
            await page.wait_for_selector('button:has-text("Reload")', timeout=4000)
            print("[google_flights] error page detected — reloading")
            await page.reload(wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(8)
        except Exception:
            pass  # no error page, results loaded fine

        return True

    except Exception as e:
        print(f"[google_flights] search form error: {e}")
        await _dump_debug(page, f"form-{origin}-{destination}-{dep_date}")
        return False


def _parse_card(text: str, origin: str, destination: str, base_date: date) -> Optional[dict]:
    """Parse one result card's text. The outbound and "Returning flights" lists share this layout."""
    if "$" not in text:
        return None

    lines = [l.strip() for l in text.splitlines() if l.strip()]

    price_usd = None
    for line in lines:
        p = _parse_price_usd(line)
        if p and p > 100:
            price_usd = p
            break
    if not price_usd:
        return None

    times = re.findall(r"\d{1,2}:\d{2}\s*(?:AM|PM)", text, re.IGNORECASE)
    dep_dt = _parse_time(times[0], base_date) if times else datetime(base_date.year, base_date.month, base_date.day, 12, 0)
    arr_dt = _parse_time(times[1], base_date) if len(times) > 1 else datetime(base_date.year, base_date.month, base_date.day, 18, 0)

    airline = None
    for line in lines:
        if not re.search(r"\d{1,2}:\d{2}|AM|PM|\$|stop|hr|min|nonstop|emissions|kg|CO2|ARN|DXB|AUH|SHJ|–", line, re.IGNORECASE) and len(line) > 2:
            airline = line
            break
    if not airline:
        return None  # skip cards where airline can't be parsed (duplicates)
    # Clean up codeshare names e.g. "SAS, Air France" or "AJetOperated by Turkish Airlines"
    airline = re.split(r',|Operated by', airline)[0].strip()

    stops, layover = 0, None
    for i, line in enumerate(lines):
        if "nonstop" in line.lower():
            stops = 0
            break
        m = re.search(r"(\d+)\s*stop", line, re.IGNORECASE)
        if m:
            stops = int(m.group(1))
            # Layover airport is on the next line e.g. "4 hr 15 min SAW"
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            airports = re.findall(r"\b([A-Z]{3})\b", next_line)
            airports = [a for a in airports if a not in (origin.upper(), destination.upper())]
            layover = ",".join(airports) if airports else None
            break

    return {
        "price_usd": price_usd, "departs": dep_dt, "arrives": arr_dt,
        "airline": airline, "stops": stops, "layover": layover,
    }


def _pick_return(options: List[dict], listed_price_usd: float) -> Optional[dict]:
    """The outbound list prices each card with its cheapest return, so prefer the return option at that same price."""
    for option in options:
        if option["price_usd"] == listed_price_usd:
            return option
    return options[0] if options else None


def _return_lookup_order(prices_usd: List[float], cap_aed: float, limit: int) -> List[int]:
    """Indexes of the trips worth a return lookup: under the price cap, cheapest first, at most `limit`."""
    under_cap = [i for i, p in enumerate(prices_usd) if p * USD_TO_AED <= cap_aed]
    return sorted(under_cap, key=lambda i: prices_usd[i])[:limit]


def _returning_heading(page: Page):
    # One return option: "Returning flights". Several: "Top returning flights" + "Other returning flights"
    return page.get_by_role("heading", name="returning flights").first


async def _lookup_return(page: Page, trip: TripResult, card_text: str, results_url: str, ret_date: date):
    """Click the trip's outbound card, read the return flight off the "Returning flights" list, then go back."""
    try:
        # Card handles go stale after every navigation, so find the card again by its text
        card = None
        for c in await page.query_selector_all('li.pIav2d'):
            if await c.inner_text() == card_text:
                card = c
                break
        if not card:
            print(f"[google_flights] return lookup: outbound card not found again ({trip.outbound.airline})")
            return

        await card.click()
        await _returning_heading(page).wait_for(timeout=10000)
        await page.wait_for_selector('li.pIav2d', timeout=10000)
        await asyncio.sleep(1)

        options = []
        for c in (await page.query_selector_all('li.pIav2d'))[:12]:
            option = _parse_card(await c.inner_text(), trip.inbound.origin, trip.inbound.destination, ret_date)
            if option:
                options.append(option)

        ret = _pick_return(options, trip.total_price_usd)
        if ret:
            trip.inbound.airline = ret["airline"]
            trip.inbound.departure_datetime = ret["departs"]
            trip.inbound.arrival_datetime = ret["arrives"]
            trip.inbound.stops = ret["stops"]
            trip.inbound.layover_airports = ret["layover"]
            trip.return_times_known = True
    except Exception as e:
        print(f"[google_flights] return lookup error: {e}")
    finally:
        _record_return_lookup(trip.return_times_known)
        if page.url != results_url:
            try:
                await page.go_back(wait_until="domcontentloaded", timeout=15000)
                await _returning_heading(page).wait_for(state="hidden", timeout=10000)
                await page.wait_for_selector('li.pIav2d', timeout=12000)
                await asyncio.sleep(1)
            except Exception as e:
                print(f"[google_flights] could not get back to the outbound list: {e}")


async def _parse_results(page: Page, origin: str, destination: str, dep_date: date, ret_date: date) -> List[TripResult]:
    results = []
    card_texts = []  # parallel to results, to find each trip's card again for the return lookup
    current_url = page.url

    try:
        await page.wait_for_selector('li.pIav2d', timeout=12000)
    except Exception:
        print(f"[google_flights] no results for {origin}->{destination} {dep_date}")
        await _dump_debug(page, f"noresults-{origin}-{destination}-{dep_date}")
        return results

    cards = await page.query_selector_all('li.pIav2d')

    for card in cards[:12]:
        try:
            text = await card.inner_text()
            parsed = _parse_card(text, origin, destination, dep_date)
            if not parsed:
                continue

            total_aed = round(parsed["price_usd"] * USD_TO_AED, 2)
            outbound = Flight(
                airline=parsed["airline"], origin=origin, destination=destination,
                departure_datetime=parsed["departs"], arrival_datetime=parsed["arrives"],
                price_aed=round(total_aed / 2, 2), currency_original="AED",
                stops=parsed["stops"], layover_airports=parsed["layover"], booking_url=current_url,
            )
            # Placeholder times until/unless _lookup_return fills in the real return flight
            inbound = Flight(
                airline=parsed["airline"], origin=destination, destination=origin,
                departure_datetime=datetime(ret_date.year, ret_date.month, ret_date.day, 12, 0),
                arrival_datetime=datetime(ret_date.year, ret_date.month, ret_date.day, 18, 0),
                price_aed=round(total_aed / 2, 2), currency_original="AED",
                stops=parsed["stops"], layover_airports=parsed["layover"], booking_url=current_url,
            )
            results.append(TripResult(outbound=outbound, inbound=inbound, total_price_aed=total_aed, total_price_usd=parsed["price_usd"]))
            card_texts.append(text)

        except Exception as e:
            print(f"[google_flights] card error: {e}")

    lookups = _return_lookup_order([t.total_price_usd for t in results], PRICE_CAP_AED, MAX_RETURN_LOOKUPS)
    for i in lookups:
        if _return_lookups_enabled():
            await _lookup_return(page, results[i], card_texts[i], current_url, ret_date)

    found = sum(1 for t in results if t.return_times_known)
    print(f"[google_flights] {origin}->{destination} {dep_date}: {len(results)} results, {found}/{len(lookups)} return times")
    return results


async def scrape_google_flights(
    departure_dates: List[date],
    origins: List[str],
    destinations: List[str],
    trip_duration: int,
) -> List[TripResult]:

    all_results: List[TripResult] = []

    async with async_playwright() as pw:
        headless = os.getenv("HEADLESS", "false").lower() == "true"
        browser = await pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            locale="en-US", viewport={"width": 1280, "height": 900},
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = await context.new_page()

        await page.goto("https://www.google.com/travel/flights?hl=en&curr=USD", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
        await _dismiss_consent(page)

        # An empty pre-fill is the page variant that used to break every search in the run
        try:
            prefilled = await page.locator('input[aria-label^="Where from?"]').first.input_value(timeout=5000)
            print(f"[google_flights] origin pre-filled by Google: {prefilled!r}")
        except Exception as e:
            print(f"[google_flights] origin box not found on landing page: {e}")

        for origin in origins:
            for destination in destinations:
                for dep_date in departure_dates:
                    ret_date = dep_date + timedelta(days=trip_duration)
                    print(f"[google_flights] {origin}->{destination} {dep_date}")

                    # Fresh page for each search to avoid stale state
                    await page.goto("https://www.google.com/travel/flights?hl=en&curr=USD", wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(1.5)

                    ok = await _search_one(page, origin, destination, dep_date, ret_date)
                    if ok:
                        trips = await _parse_results(page, origin, destination, dep_date, ret_date)
                        all_results.extend(trips)

                    await asyncio.sleep(1)

        await browser.close()

    print(f"[google_flights] total: {len(all_results)} options")
    return all_results
