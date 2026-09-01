from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from .schema import Listing, SearchProfile

_OPEN_FX_URL = "https://open.er-api.com/v6/latest/USD"


@dataclass(frozen=True)
class FxSnapshot:
    rates: dict[str, float]
    updated_at: str

    def convert(self, amount: float, source: str, target: str) -> float:
        source = source.upper()
        target = target.upper()
        if source == target:
            return float(amount)
        if source == "USD":
            source_rate = 1.0
        else:
            source_rate = self.rates[source]
        if target == "USD":
            target_rate = 1.0
        else:
            target_rate = self.rates[target]
        return float(amount) / float(source_rate) * float(target_rate)


class FxProvider:
    """Small in-process cache around the public daily FX feed.

    FX is used for filtering/comparison only. The original listing price and currency
    stay untouched and remain the commercial source of truth.
    """

    def __init__(self, ttl_seconds: int = 21_600):
        self.ttl_seconds = ttl_seconds
        self._snapshot: FxSnapshot | None = None
        self._loaded_at = 0.0

    def get(self) -> FxSnapshot:
        now = time.time()
        if self._snapshot is not None and now - self._loaded_at < self.ttl_seconds:
            return self._snapshot
        req = Request(_OPEN_FX_URL, headers={"User-Agent": "UniversalVehicleSearch/1.0"})
        with urlopen(req, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if str(payload.get("result") or "").casefold() != "success":
            raise RuntimeError("FX provider returned non-success response")
        rates = {str(k).upper(): float(v) for k, v in (payload.get("rates") or {}).items() if v}
        if "EUR" not in rates or "AED" not in rates:
            raise RuntimeError("FX response misses required currencies")
        updated = str(payload.get("time_last_update_utc") or datetime.now(timezone.utc).isoformat())
        self._snapshot = FxSnapshot(rates=rates, updated_at=updated)
        self._loaded_at = now
        return self._snapshot


def effective_price(listing: Listing, profile: SearchProfile) -> tuple[float | None, str | None]:
    if profile.export_vat_required:
        amount = listing.export_price if listing.export_price is not None else listing.net_price
    else:
        amount = listing.price
        if amount is None:
            amount = listing.gross_price or listing.net_price or listing.export_price
    return amount, listing.currency


def normalize_listing_price(listing: Listing, profile: SearchProfile, snapshot: FxSnapshot) -> Listing:
    if not profile.max_price or not profile.price_currency:
        return listing
    amount, currency = effective_price(listing, profile)
    if amount is None or not currency:
        return listing
    try:
        normalized = snapshot.convert(float(amount), currency, profile.price_currency)
    except (KeyError, ValueError, ZeroDivisionError):
        return listing

    listing.normalized_price = normalized
    listing.normalized_currency = profile.price_currency.upper()
    listing.fx_updated_at = snapshot.updated_at
    listing.missing = [item for item in listing.missing if item != "price_currency"]

    if normalized > float(profile.max_price):
        listing.status = "irrelevant"
        return listing

    if listing.status != "irrelevant" and not listing.missing:
        listing.status = "relevant"
    elif listing.status != "irrelevant":
        listing.status = "candidate"
    return listing
