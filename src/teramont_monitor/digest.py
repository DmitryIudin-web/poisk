from __future__ import annotations

from collections.abc import Mapping

from .identity import vehicle_key
from .models import PriceDigest, RankedOffer, SourceResult
from .profiles import TargetProfile
from .qualify import qualify


def _display_price(offer: RankedOffer) -> int | None:
    listing = offer.listing
    return listing.cash_price or listing.advertised_price


def _confirmed_eligible(offer: RankedOffer) -> bool:
    listing = offer.listing
    return (
        offer.status == "relevant"
        and listing.region == "russia"
        and listing.in_stock.value is True
        and listing.sold.value is not True
        and listing.cash_price is not None
        and listing.cash_price > 0
        and listing.price_currency == "RUB"
    )


def _candidate_eligible(offer: RankedOffer) -> bool:
    listing = offer.listing
    price = _display_price(offer)
    return (
        offer.status in {"relevant", "candidate"}
        and listing.region == "russia"
        and listing.in_stock.value is not False
        and listing.sold.value is not True
        and price is not None
        and price > 0
        and listing.price_currency == "RUB"
    )


def _with_commercial_gaps(offer: RankedOffer) -> RankedOffer:
    missing = list(offer.missing)
    if offer.listing.in_stock.value is None and "in_stock" not in missing:
        missing.append("in_stock")
    if offer.listing.cash_price is None and "cash_price" not in missing:
        missing.append("cash_price")
    return RankedOffer(offer.listing, offer.status, tuple(missing))


def _prefer(current: RankedOffer, incoming: RankedOffer) -> RankedOffer:
    def preference(offer: RankedOffer) -> tuple[int, int]:
        strictness = 0 if _confirmed_eligible(offer) else 1 if offer.status == "relevant" else 2
        return strictness, _display_price(offer) or 0

    return incoming if preference(incoming) < preference(current) else current


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
            offer = _with_commercial_gaps(RankedOffer(listing=listing, status=status, missing=missing))
            if not _candidate_eligible(offer):
                continue
            key = vehicle_key(listing)
            if key in by_vehicle:
                by_vehicle[key] = _prefer(by_vehicle[key], offer)
            else:
                by_vehicle[key] = offer

    def sort_key(item: RankedOffer) -> tuple[int, str, str]:
        return (_display_price(item) or 0, item.listing.title, item.listing.url)

    confirmed = tuple(sorted(
        (item for item in by_vehicle.values() if _confirmed_eligible(item)),
        key=sort_key,
    )[:limit])
    candidates = ()
    if len(confirmed) < limit:
        candidates = tuple(sorted(
            (item for item in by_vehicle.values() if not _confirmed_eligible(item)),
            key=sort_key,
        )[: limit - len(confirmed)])

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
