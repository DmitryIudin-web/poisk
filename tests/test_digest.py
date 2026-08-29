from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from teramont_monitor.digest import build_price_digest
from teramont_monitor.models import Evidence, SourceResult
from teramont_monitor.profiles import load_target_profile
from teramont_monitor.storage import load_price_digest, save_price_digest
from tests.test_qualify import matching_listing


ROOT = Path(__file__).resolve().parents[1]
PROFILE = load_target_profile(ROOT / "config/targets/teramont-pro-2026.json")


def offer(
    listing_id: str,
    price: int,
    *,
    vin: str | None = None,
    candidate: bool = False,
    **changes,
):
    listing = replace(
        matching_listing(),
        source="autoru",
        listing_id=listing_id,
        url=f"https://auto.ru/cars/used/sale/volkswagen/teramont/{listing_id}/",
        title=f"Teramont offer {listing_id}",
        cash_price=price,
        advertised_price=price,
        price_currency="RUB",
        region="russia",
        source_market="russia",
        location="Москва",
        vin=vin,
        dcc=Evidence(None, None) if candidate else matching_listing().dcc,
    )
    return replace(listing, **changes)


class PriceDigestTests(unittest.TestCase):
    def test_ranks_three_lowest_confirmed_russian_cash_prices_and_hides_candidates(self) -> None:
        listings = (
            offer("1", 6_300_000),
            offer("2", 5_700_000),
            offer("3", 5_900_000),
            offer("4", 5_800_000),
            offer("candidate", 5_400_000, candidate=True),
        )

        digest = build_price_digest(
            {"autoru": SourceResult("autoru", True, listings)},
            PROFILE,
            "2026-08-29T09:00:00Z",
        )

        self.assertEqual([item.listing.cash_price for item in digest.confirmed], [5_700_000, 5_800_000, 5_900_000])
        self.assertEqual(digest.candidates, ())

    def test_candidates_fill_remaining_cards_with_advertised_prices_and_clear_gaps(self) -> None:
        listings = (
            offer("confirmed", 5_900_000, vin="CONFIRMEDVIN00001"),
            offer("same-vin-candidate", 5_100_000, vin="CONFIRMEDVIN00001", candidate=True),
            offer(
                "candidate-1",
                5_300_000,
                candidate=True,
                cash_price=None,
                in_stock=Evidence(None, None),
            ),
            offer("candidate-2", 5_500_000, candidate=True, cash_price=None),
            offer("candidate-3", 5_700_000, candidate=True),
            offer("candidate-4", 5_800_000, candidate=True),
            offer("not-russia", 4_000_000, candidate=True, region="georgia"),
            offer("not-stock", 4_100_000, candidate=True, in_stock=Evidence(False, "в пути")),
            offer("not-rub", 50_000, candidate=True, price_currency="EUR"),
            offer("no-price", 4_200_000, candidate=True, cash_price=None, advertised_price=None),
        )

        digest = build_price_digest(
            {
                "autoru": SourceResult("autoru", True, listings),
                "blocked": SourceResult("blocked", False),
            },
            PROFILE,
            "2026-08-29T09:00:00Z",
        )

        self.assertEqual([item.listing.cash_price for item in digest.confirmed], [5_900_000])
        self.assertEqual([item.listing.advertised_price for item in digest.candidates], [5_300_000, 5_500_000])
        self.assertEqual(digest.candidates[0].missing, ("dcc", "in_stock", "cash_price"))
        self.assertEqual(digest.candidates[1].missing, ("dcc", "cash_price"))
        self.assertEqual(digest.successful_sources, 1)
        self.assertEqual(digest.failed_sources, 1)

    def test_digest_round_trips_as_json(self) -> None:
        digest = build_price_digest(
            {"autoru": SourceResult("autoru", True, (offer("1", 5_700_000),))},
            PROFILE,
            "2026-08-29T09:00:00Z",
        )

        with TemporaryDirectory() as directory:
            path = Path(directory) / "price-digest.json"
            save_price_digest(path, digest)

            self.assertEqual(load_price_digest(path), digest)


if __name__ == "__main__":
    unittest.main()
