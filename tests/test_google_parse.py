from datetime import date, datetime

import scrapers.google_flights as gf
from scrapers.google_flights import _parse_card, _pick_return, _return_lookup_order

# Card texts captured from the live Google Flights results page
OUT_PEGASUS = "12:10 PM\n – \n3:15 AM+1\nPegasus\n13 hr 5 min\nARN–DXB\n1 stop\n4 hr 25 min SAW\n400 kg CO2e\nAvg emissions\n$523\nround trip"
OUT_CODESHARE = "12:10 PM\n – \n1:55 AM+1\nAJetOperated by Turkish Airlines, Turkish Airlines\n11 hr 45 min\nARN–DXB\n1 stop\n2 hr 50 min SAW\n358 kg CO2e\n-11% emissions\n$542\nround trip"
RET_PEGASUS = "4:25 AM\n – \n3:30 PM\nPegasus\n13 hr 5 min\nDXB–ARN\n1 stop\n4 hr 10 min SAW\n395 kg CO2e\nAvg emissions\n$523\nround trip"


def test_parse_card_reads_outbound_card():
    card = _parse_card(OUT_PEGASUS, "ARN", "DXB", date(2026, 10, 14))

    assert card["price_usd"] == 523.0
    assert card["departs"] == datetime(2026, 10, 14, 12, 10)
    assert card["arrives"] == datetime(2026, 10, 14, 3, 15)
    assert card["airline"] == "Pegasus"
    assert card["stops"] == 1
    assert card["layover"] == "SAW"


def test_parse_card_reads_return_card():
    card = _parse_card(RET_PEGASUS, "DXB", "ARN", date(2026, 10, 18))

    assert card["departs"] == datetime(2026, 10, 18, 4, 25)
    assert card["arrives"] == datetime(2026, 10, 18, 15, 30)
    assert card["price_usd"] == 523.0


def test_parse_card_strips_codeshare_from_airline():
    card = _parse_card(OUT_CODESHARE, "ARN", "DXB", date(2026, 10, 14))

    assert card["airline"] == "AJet"


def test_parse_card_skips_card_without_price():
    assert _parse_card("12:10 PM\n – \n3:15 AM+1\nPegasus\nPrice unavailable", "ARN", "DXB", date(2026, 10, 14)) is None


def test_pick_return_prefers_option_matching_listed_price():
    options = [{"price_usd": 600.0, "airline": "A"}, {"price_usd": 523.0, "airline": "B"}]

    assert _pick_return(options, 523.0)["airline"] == "B"


def test_pick_return_falls_back_to_first_option():
    options = [{"price_usd": 600.0, "airline": "A"}, {"price_usd": 640.0, "airline": "B"}]

    assert _pick_return(options, 523.0)["airline"] == "A"


def test_pick_return_handles_no_options():
    assert _pick_return([], 523.0) is None


def test_return_lookup_order_is_cheapest_under_cap_first():
    # 1800 AED cap at 3.67 AED/USD = $490.46
    prices_usd = [480.0, 523.0, 450.0, 470.0, 460.0]

    assert _return_lookup_order(prices_usd, cap_aed=1800, limit=3) == [2, 4, 3]


def test_return_lookup_order_empty_when_everything_over_cap():
    assert _return_lookup_order([523.0, 542.0], cap_aed=1800, limit=3) == []


def test_return_lookups_switch_off_after_repeated_failures(monkeypatch):
    monkeypatch.setattr(gf, "_return_lookup_failures", 0)

    for _ in range(gf.MAX_RETURN_LOOKUP_FAILURES):
        assert gf._return_lookups_enabled()
        gf._record_return_lookup(False)

    assert not gf._return_lookups_enabled()


def test_successful_return_lookup_resets_the_failure_streak(monkeypatch):
    monkeypatch.setattr(gf, "_return_lookup_failures", 0)

    for _ in range(gf.MAX_RETURN_LOOKUP_FAILURES - 1):
        gf._record_return_lookup(False)
    gf._record_return_lookup(True)
    for _ in range(gf.MAX_RETURN_LOOKUP_FAILURES - 1):
        gf._record_return_lookup(False)

    assert gf._return_lookups_enabled()
