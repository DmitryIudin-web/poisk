from __future__ import annotations

from .models import Listing
from .profiles import TargetProfile


def qualify(listing: Listing, profile: TargetProfile | None = None) -> tuple[str, tuple[str, ...]]:
    # Keep the pre-profile call form usable for persisted Teramont workflows.
    if profile is None:
        checks: list[tuple[str, bool | None]] = [
            ("model", listing.model_match.value),
            ("year", None if listing.year is None else listing.year == 2026),
            ("exterior_black", listing.exterior_black.value),
            ("interior_black", listing.interior_black.value),
            ("top_trim", listing.top_trim.value),
            ("dcc", listing.dcc.value),
            ("mileage", None if listing.mileage_km is None else 0 <= listing.mileage_km <= 1_000),
            ("in_stock", listing.in_stock.value),
            (
                "region",
                None
                if listing.region == "unknown"
                else listing.region in {"russia", "bishkek", "eaeu_other"},
            ),
        ]
    else:
        checks = [
            (name, getattr(listing, name).value)
            for name in profile.required_evidence
        ]
        checks.extend(
            (
                ("year", None if listing.year is None else listing.year == profile.year),
                ("exterior_black", listing.exterior_black.value),
                ("interior_black", listing.interior_black.value),
                (
                    "mileage",
                    None
                    if listing.mileage_km is None
                    else 0 <= listing.mileage_km <= profile.max_mileage_km,
                ),
                ("in_stock", listing.in_stock.value),
                (
                    "region",
                    None
                    if listing.region == "unknown"
                    else listing.region in profile.allowed_regions,
                ),
            )
        )
        if listing.region in profile.lhd_required_regions:
            checks.append(("steering_left", listing.steering_left.value))
    failed = tuple(name for name, value in checks if value is False)
    if failed:
        return "irrelevant", failed
    unknown = tuple(name for name, value in checks if value is None)
    if unknown:
        return "candidate", unknown
    return "relevant", ()
