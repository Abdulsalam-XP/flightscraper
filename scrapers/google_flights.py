import asyncio
import os
import re
from datetime import date, timedelta, datetime
from typing import List, Optional
from playwright.async_api import async_playwright, Page
from models import Flight, TripResult

USD_TO_AED = 3.67

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


async def _parse_results(page: Page, origin: str, destination: str, dep_date: date, ret_date: date) -> List[TripResult]:
    results = []
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
            if "$" not in text:
                continue

            lines = [l.strip() for l in text.splitlines() if l.strip()]

            price_usd = None
            for line in lines:
                p = _parse_price_usd(line)
                if p and p > 100:
                    price_usd = p
                    break
            if not price_usd:
                continue

            times = re.findall(r"\d{1,2}:\d{2}\s*(?:AM|PM)", text, re.IGNORECASE)
            dep_dt = _parse_time(times[0], dep_date) if times else datetime(dep_date.year, dep_date.month, dep_date.day, 12, 0)
            arr_dt = _parse_time(times[1], dep_date) if len(times) > 1 else datetime(dep_date.year, dep_date.month, dep_date.day, 18, 0)

            airline = None
            for line in lines:
                if not re.search(r"\d{1,2}:\d{2}|AM|PM|\$|stop|hr|min|nonstop|emissions|kg|CO2|ARN|DXB|AUH|SHJ|–", line, re.IGNORECASE) and len(line) > 2:
                    airline = line
                    break
            if not airline:
                continue  # skip cards where airline can't be parsed (duplicates)
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

            total_aed = round(price_usd * USD_TO_AED, 2)
            outbound = Flight(
                airline=airline, origin=origin, destination=destination,
                departure_datetime=dep_dt, arrival_datetime=arr_dt,
                price_aed=round(total_aed / 2, 2), currency_original="AED",
                stops=stops, layover_airports=layover, booking_url=current_url,
            )
            inbound = Flight(
                airline=airline, origin=destination, destination=origin,
                departure_datetime=datetime(ret_date.year, ret_date.month, ret_date.day, 12, 0),
                arrival_datetime=datetime(ret_date.year, ret_date.month, ret_date.day, 18, 0),
                price_aed=round(total_aed / 2, 2), currency_original="AED",
                stops=stops, layover_airports=layover, booking_url=current_url,
            )
            results.append(TripResult(outbound=outbound, inbound=inbound, total_price_aed=total_aed))

        except Exception as e:
            print(f"[google_flights] card error: {e}")

    print(f"[google_flights] {origin}->{destination} {dep_date}: {len(results)} results")
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
