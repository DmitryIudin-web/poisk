from __future__ import annotations

import unittest
from dataclasses import replace

from teramont_monitor.identity import canonical_url, listing_key, vehicle_key
from tests.test_qualify import matching_listing, matching_range_rover


class IdentityTests(unittest.TestCase):
    def test_listing_identity_uses_source_listing_id(self) -> None:
        listing = matching_listing()
        self.assertEqual(listing_key(listing), "drom:1")

    def test_vehicle_identity_groups_sellers_by_vin(self) -> None:
        vin = "WVGZZZCA1RC123456"
        first = replace(matching_listing(), vin=vin)
        second = replace(first, source="autoru", listing_id="2", url="https://auto.ru/2")

        self.assertNotEqual(listing_key(first), listing_key(second))
        self.assertEqual(vehicle_key(first), vehicle_key(second))

    def test_range_rover_identity_is_scoped_to_its_target(self) -> None:
        listing = replace(
            matching_range_rover(),
            source="drom",
            listing_id="123",
            vin="SALGA2BK0RA000001",
        )

        self.assertEqual(
            listing_key(listing),
            "range-rover-l460-d350-autobiography-2026:drom:123",
        )
        self.assertEqual(
            vehicle_key(listing),
            "range-rover-l460-d350-autobiography-2026:vin:SALGA2BK0RA000001",
        )

    def test_url_fallback_removes_tracking_and_fragment(self) -> None:
        listing = replace(
            matching_listing(),
            listing_id=None,
            url="HTTPS://Example.COM/lot/1/?utm_source=x#photo",
        )

        self.assertEqual(canonical_url(listing.url), "https://example.com/lot/1")
        self.assertTrue(listing_key(listing).startswith("drom:url:"))


if __name__ == "__main__":
    unittest.main()
