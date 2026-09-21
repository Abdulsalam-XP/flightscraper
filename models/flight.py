from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date


class Flight(BaseModel):
    airline: str
    flight_number: Optional[str] = None
    origin: str
    destination: str
    departure_datetime: datetime
    arrival_datetime: datetime
    price_aed: float
    price_sek: Optional[float] = None
    currency_original: str = "AED"
    stops: int = 0
    layover_airports: Optional[str] = None  # comma-separated e.g. "IST"
    booking_url: Optional[str] = None
    scraped_at: datetime = None

    def __init__(self, **data):
        if data.get("scraped_at") is None:
            data["scraped_at"] = datetime.utcnow()
        super().__init__(**data)


class TripResult(BaseModel):
    outbound: Flight
    inbound: Flight
    total_price_aed: float
    total_price_sek: Optional[float] = None
    total_price_usd: Optional[float] = None
    # False = inbound times are placeholders (the return leg was never looked up)
    return_times_known: bool = False

    @property
    def trip_duration_days(self) -> int:
        return (self.inbound.departure_datetime.date() - self.outbound.departure_datetime.date()).days
