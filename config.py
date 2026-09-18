from dataclasses import dataclass, field
from typing import List

PRICE_CAP_AED = 1800

ORIGINS = ["ARN"]

# RKT excluded (no scheduled service from Sweden), NYO excluded (no UAE routes confirmed)
DESTINATIONS = ["DXB", "AUH", "SHJ"]

TRIP_DURATION_DAYS = 4

# Search window (inclusive): every trip that departs and returns between these dates
SEARCH_START = "2026-10-01"
SEARCH_END = "2026-11-30"

# AED to SEK rough rate (update if needed)
AED_TO_SEK = 3.1
