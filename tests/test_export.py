import csv
from datetime import datetime

from export import export_trips, fmt_time
from models import Flight, TripResult


def make_trip(**overrides) -> TripResult:
    outbound = Flight(
        airline="Pegasus", origin="ARN", destination="DXB",
        departure_datetime=datetime(2026, 10, 14, 12, 10),
        arrival_datetime=datetime(2026, 10, 14, 3, 15),
        price_aed=900.0,
    )
    inbound = Flight(
        airline="Pegasus", origin="DXB", destination="ARN",
        departure_datetime=datetime(2026, 10, 18, 4, 25),
        arrival_datetime=datetime(2026, 10, 18, 15, 30),
        price_aed=900.0,
    )
    return TripResult(outbound=outbound, inbound=inbound, total_price_aed=1800.0, **overrides)


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_fmt_time_shows_24h_next_to_12h():
    assert fmt_time(datetime(2026, 10, 14, 18, 5)) == "6:05 PM (18:05)"


def test_fmt_time_pads_24h_morning_hours():
    assert fmt_time(datetime(2026, 10, 18, 4, 25)) == "4:25 AM (04:25)"


def test_export_writes_return_flight_times(tmp_path):
    trip = make_trip(return_times_known=True)

    row = read_rows(export_trips([trip], output_dir=str(tmp_path)))[0]

    assert row["return_departs (UAE time)"] == "4:25 AM (04:25)"
    assert row["return_arrives (Sweden time)"] == "3:30 PM (15:30)"


def test_export_leaves_return_times_blank_when_not_scraped(tmp_path):
    trip = make_trip()  # inbound holds placeholder times only

    row = read_rows(export_trips([trip], output_dir=str(tmp_path)))[0]

    assert row["return_departs (UAE time)"] == ""
    assert row["return_arrives (Sweden time)"] == ""


def test_export_writes_price_usd(tmp_path):
    trip = make_trip(total_price_usd=490.46)

    row = read_rows(export_trips([trip], output_dir=str(tmp_path)))[0]

    assert row["price_usd"] == "490.46"


def test_export_headers_say_which_local_time_each_column_is_in(tmp_path):
    path = export_trips([make_trip()], output_dir=str(tmp_path))

    with open(path, newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))

    assert "outbound_departs (Sweden time)" in header
    assert "outbound_arrives (UAE time)" in header
    assert "return_departs (UAE time)" in header
    assert "return_arrives (Sweden time)" in header
