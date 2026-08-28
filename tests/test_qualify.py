from __future__ import annotations

import unittest
from dataclasses import replace

from teramont_monitor.models import Evidence
from teramont_monitor.normalize import normalize_listing
from teramont_monitor.qualify import qualify


def matching_listing():
    return normalize_listing(
        "drom",
        "https://auto.drom.ru/moscow/volkswagen/teramont/1.html",
        "1",
        "Volkswagen Teramont Pro 2026 Peak, black on black, DCC, пробег 23 км, в наличии, Москва",
        {},
    )


class QualificationTests(unittest.TestCase):
    def test_all_eight_requirements_make_offer_relevant(self) -> None:
        self.assertEqual(qualify(matching_listing()), ("relevant", ()))

    def test_unknown_dcc_keeps_offer_candidate(self) -> None:
        listing = replace(matching_listing(), dcc=Evidence(None, None))

        status, reasons = qualify(listing)

        self.assertEqual(status, "candidate")
        self.assertIn("dcc", reasons)

    def test_explicit_non_black_exterior_is_irrelevant(self) -> None:
        listing = replace(matching_listing(), exterior_black=Evidence(False, "белый"))

        status, reasons = qualify(listing)

        self.assertEqual(status, "irrelevant")
        self.assertIn("exterior_black", reasons)

    def test_mileage_over_limit_is_irrelevant(self) -> None:
        listing = replace(matching_listing(), mileage_km=1_001)

        status, reasons = qualify(listing)

        self.assertEqual(status, "irrelevant")
        self.assertIn("mileage", reasons)

    def test_in_transit_is_not_physical_stock(self) -> None:
        listing = replace(matching_listing(), in_stock=Evidence(False, "в пути"))

        status, reasons = qualify(listing)

        self.assertEqual(status, "irrelevant")
        self.assertIn("in_stock", reasons)


if __name__ == "__main__":
    unittest.main()
