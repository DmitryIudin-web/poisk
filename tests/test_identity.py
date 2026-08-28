from __future__ import annotations

import unittest
from dataclasses import replace

from teramont_monitor.identity import canonical_url, listing_key, vehicle_key
from tests.test_qualify import matching_listing


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
