from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from teramont_monitor.models import Evidence
from teramont_monitor.normalize import normalize_listing
from teramont_monitor.profiles import load_target_profile
from teramont_monitor.qualify import qualify


ROOT = Path(__file__).resolve().parents[1]
TERAMONT_PROFILE = load_target_profile(ROOT / "config/targets/teramont-pro-2026.json")
RANGE_ROVER_PROFILE = load_target_profile(
    ROOT / "config/targets/range-rover-l460-d350-autobiography-2026.json"
)


def matching_listing():
    return normalize_listing(
        "drom",
        "https://auto.drom.ru/moscow/volkswagen/teramont/1.html",
        "1",
        "Volkswagen Teramont Pro 2026 Peak, black on black, DCC, пробег 23 км, в наличии, Москва",
        {},
        TERAMONT_PROFILE,
    )


def matching_range_rover(**changes):
    listing = normalize_listing(
        "autobridge",
        "https://autobridge.ge/en/listings/range-rover-1",
        "1",
        (
            "Range Rover L460 D350 2026 Autobiography. Exterior: Santorini Black. "
            "Interior: Ebony Black. Mileage 19 km. In stock. Left hand drive. "
            "Factory Rear Seat Entertainment with two rear screens."
        ),
        {},
        RANGE_ROVER_PROFILE,
        market="georgia",
    )
    return replace(listing, **changes)


class QualificationTests(unittest.TestCase):
    def test_range_rover_qualification_matrix(self) -> None:
        cases = (
            (matching_range_rover(), RANGE_ROVER_PROFILE, ("relevant", ())),
            (matching_range_rover(mileage_km=1_000), RANGE_ROVER_PROFILE, ("relevant", ())),
            (matching_range_rover(mileage_km=1_001), RANGE_ROVER_PROFILE, None),
            (
                replace(matching_range_rover(), rear_seat_entertainment=Evidence(None)),
                RANGE_ROVER_PROFILE,
                ("candidate", ("rear_seat_entertainment",)),
            ),
            (replace(matching_range_rover(), rear_seat_entertainment=Evidence(False)), RANGE_ROVER_PROFILE, None),
            (
                replace(matching_range_rover(), region="europe", steering_left=Evidence(None)),
                RANGE_ROVER_PROFILE,
                ("candidate", ("steering_left",)),
            ),
            (replace(matching_range_rover(), region="europe", steering_left=Evidence(False)), RANGE_ROVER_PROFILE, None),
            (replace(matching_range_rover(), region="georgia", steering_left=Evidence(None)), RANGE_ROVER_PROFILE, ("relevant", ())),
        )
        for listing, profile, expected in cases:
            with self.subTest(listing=listing, expected=expected):
                result = qualify(listing, profile)
                if expected is None:
                    self.assertEqual(result[0], "irrelevant")
                else:
                    self.assertEqual(result, expected)

    def test_all_eight_requirements_make_offer_relevant(self) -> None:
        self.assertEqual(qualify(matching_listing(), TERAMONT_PROFILE), ("relevant", ()))

    def test_unknown_dcc_keeps_offer_candidate(self) -> None:
        listing = replace(matching_listing(), dcc=Evidence(None, None))

        status, reasons = qualify(listing, TERAMONT_PROFILE)

        self.assertEqual(status, "candidate")
        self.assertIn("dcc", reasons)

    def test_explicit_non_black_exterior_is_irrelevant(self) -> None:
        listing = replace(matching_listing(), exterior_black=Evidence(False, "белый"))

        status, reasons = qualify(listing, TERAMONT_PROFILE)

        self.assertEqual(status, "irrelevant")
        self.assertIn("exterior_black", reasons)

    def test_mileage_over_limit_is_irrelevant(self) -> None:
        listing = replace(matching_listing(), mileage_km=1_001)

        status, reasons = qualify(listing, TERAMONT_PROFILE)

        self.assertEqual(status, "irrelevant")
        self.assertIn("mileage", reasons)

    def test_mileage_at_limit_remains_relevant_for_teramont(self) -> None:
        listing = replace(matching_listing(), mileage_km=1_000)

        self.assertEqual(qualify(listing, TERAMONT_PROFILE), ("relevant", ()))

    def test_in_transit_is_not_physical_stock(self) -> None:
        listing = replace(matching_listing(), in_stock=Evidence(False, "в пути"))

        status, reasons = qualify(listing, TERAMONT_PROFILE)

        self.assertEqual(status, "irrelevant")
        self.assertIn("in_stock", reasons)


if __name__ == "__main__":
    unittest.main()
