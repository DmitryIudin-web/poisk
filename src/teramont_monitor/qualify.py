from __future__ import annotations

from .models import Listing


def qualify(listing: Listing) -> tuple[str, tuple[str, ...]]:
    checks: tuple[tuple[str, bool | None], ...] = (
        ("model", listing.model_match.value),
        ("year", None if listing.year is None else listing.year == 2026),
        ("exterior_black", listing.exterior_black.value),
        ("interior_black", listing.interior_black.value),
        ("top_trim", listing.top_trim.value),
        ("dcc", listing.dcc.value),
        ("mileage", None if listing.mileage_km is None else 0 <= listing.mileage_km <= 1_000),
        ("in_stock", listing.in_stock.value),
    )
    failed = tuple(name for name, value in checks if value is False)
    if failed:
        return "irrelevant", failed
    unknown = tuple(name for name, value in checks if value is None)
    if unknown:
        return "candidate", unknown
    return "relevant", ()
