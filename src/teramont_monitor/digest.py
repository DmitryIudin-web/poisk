from __future__ import annotations

from collections.abc import Mapping

from .identity import vehicle_key
from .models import PriceDigest, RankedOffer, SourceResult
from .profiles import TargetProfile
from .qualify import qualify


def _eligible(offer: RankedOffer) -> bool:
    listing = offer.listing
    return (
        offer.status in {"relevant", "candidate"}
        and listing.region == "russia"
        and listing.in_stock.value is True
        and listing.sold.value is not True
        and listing.cash_price is not None
        and listing.cash_price > 0
        and listing.price_currency == "RUB"
    )


def _prefer(current: RankedOffer, incoming: RankedOffer) -> RankedOffer:
    if current.status != incoming.status:
        return current if current.status == "relevant" else incoming
    current_price = current.listing.cash_price or 0
    incoming_price = incoming.listing.cash_price or 0
    return incoming if incoming_price < current_price else current


def build_price_digest(
    results: Mapping[str, SourceResult],
    profile: TargetProfile,
    observed_at: str,
    *,
    limit: int = 3,
) -> PriceDigest:
    if limit <= 0:
        raise ValueError("price digest limit must be positive")

    by_vehicle: dict[str, RankedOffer] = {}
    for result in results.values():
        if not result.ok:
            continue
        for listing in result.listings:
            status, missing = qualify(listing, profile)
            offer = RankedOffer(listing=listing, status=status, missing=missing)
            if not _eligible(offer):
                continue
            key = vehicle_key(listing)
            if key in by_vehicle:
                by_vehicle[key] = _prefer(by_vehicle[key], offer)
            else:
                by_vehicle[key] = offer

    def sort_key(item: RankedOffer) -> tuple[int, str, str]:
        return (item.listing.cash_price or 0, item.listing.title, item.listing.url)

    confirmed = tuple(sorted(
        (item for item in by_vehicle.values() if item.status == "relevant"),
        key=sort_key,
    )[:limit])
    candidates = ()
    if len(confirmed) < limit:
        candidates = tuple(sorted(
            (item for item in by_vehicle.values() if item.status == "candidate"),
            key=sort_key,
        )[:limit])

    successful = sum(result.ok for result in results.values())
    return PriceDigest(
        target_id=profile.target_id,
        target_name=profile.display_name,
        observed_at=observed_at,
        successful_sources=successful,
        failed_sources=len(results) - successful,
        confirmed=confirmed,
        candidates=candidates,
    )
