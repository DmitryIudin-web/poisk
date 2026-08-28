from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import teramont_monitor.cli as cli_module
from teramont_monitor.html import extract_detail
from teramont_monitor.normalize import normalize_listing
from teramont_monitor.profiles import load_target_profile
from teramont_monitor.qualify import qualify
from teramont_monitor.storage import load_pending, load_state
from teramont_monitor.telegram import send_pending
from tests.test_cli import CONFIG, fixture_fetcher


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
WORKFLOW = ROOT / ".github" / "workflows" / "monitor.yml"
README = ROOT / "README.md"
TERAMONT_PROFILE_PATH = ROOT / "config" / "targets" / "teramont-pro-2026.json"
RANGE_ROVER_PROFILE_PATH = (
    ROOT / "config" / "targets" / "range-rover-l460-d350-autobiography-2026.json"
)
TERAMONT_PROFILE = load_target_profile(TERAMONT_PROFILE_PATH)
RANGE_ROVER_PROFILE = load_target_profile(RANGE_ROVER_PROFILE_PATH)


def range_rover_text(extra: str = "") -> str:
    return (
        "Range Rover L460 D350 Autobiography. Model Year 2026. "
        "Exterior: Santorini Black. Interior: Ebony Black. Mileage 19 km. "
        "Rear Seat Entertainment. Left hand drive. "
        f"{extra}"
    )


class FinalRegressionTests(unittest.TestCase):
    def test_physical_region_never_uses_source_market_over_conflicting_location(self) -> None:
        listing = normalize_listing(
            "autoru",
            "https://auto.ru/cars/new/sale/land_rover/range_rover/1-a/",
            "1-a",
            range_rover_text("In stock."),
            {"location": "Dubai"},
            RANGE_ROVER_PROFILE,
            market="russia",
        )

        status, reasons = qualify(listing, RANGE_ROVER_PROFILE)

        self.assertEqual(listing.to_dict().get("source_market"), "russia")
        self.assertNotEqual(listing.region, "russia")
        self.assertEqual(status, "irrelevant")
        self.assertIn("region", reasons)

    def test_unknown_physical_region_stays_candidate(self) -> None:
        listing = normalize_listing(
            "unmapped_source",
            "https://example.test/range-rover/unknown-region",
            "unknown-region",
            range_rover_text("In stock."),
            {},
            RANGE_ROVER_PROFILE,
        )

        status, reasons = qualify(listing, RANGE_ROVER_PROFILE)

        self.assertEqual(listing.region, "unknown")
        self.assertEqual(status, "candidate")
        self.assertIn("region", reasons)

    def test_non_stock_language_patterns_win_before_positive_substrings(self) -> None:
        cases = (
            "Not in stock; available to order.",
            "Not physically in stock; arriving soon.",
            "Vehicle is on order and arriving next month.",
            "Nicht auf Lager; auf Bestellung.",
            "Физически не в наличии; ожидается поступление.",
        )
        for wording in cases:
            with self.subTest(wording=wording):
                listing = normalize_listing(
                    "mobile_de",
                    "https://example.test/stock",
                    "stock",
                    range_rover_text(wording),
                    {},
                    RANGE_ROVER_PROFILE,
                    market="europe",
                )
                self.assertIs(listing.in_stock.value, False)

    def test_aftermarket_removable_and_headrest_rse_wording_wins(self) -> None:
        cases = (
            "Aftermarket removable tablet screens mounted to the headrests.",
            "Headrest screen retrofit kit fitted by the dealer.",
            "RSE with tablet displays for the rear passengers.",
            "Fond Entertainment mit abnehmbaren Tablet-Bildschirmen an den Kopfstützen.",
        )
        for wording in cases:
            with self.subTest(wording=wording):
                listing = normalize_listing(
                    "mobile_de",
                    "https://example.test/rse",
                    "rse",
                    range_rover_text(f"In stock. {wording}"),
                    {},
                    RANGE_ROVER_PROFILE,
                    market="europe",
                )
                self.assertIs(listing.rear_seat_entertainment.value, False)

    def test_copyright_year_and_brand_new_do_not_supply_rr_year_or_mileage(self) -> None:
        copyright_only = normalize_listing(
            "autobridge",
            "https://example.test/copyright",
            "copyright",
            (
                "Range Rover L460 D350 Autobiography. Copyright 2026. "
                "Exterior: Black. Interior: Ebony. Mileage 19 km. In stock. "
                "Rear Seat Entertainment."
            ),
            {},
            RANGE_ROVER_PROFILE,
            market="georgia",
        )
        copyright_year_only = normalize_listing(
            "autobridge",
            "https://example.test/copyright-year",
            "copyright-year",
            (
                "Range Rover L460 D350 Autobiography. Copyright year 2026. "
                "Exterior: Black. Interior: Ebony. Mileage 19 km. In stock. "
                "Rear Seat Entertainment."
            ),
            {},
            RANGE_ROVER_PROFILE,
            market="georgia",
        )
        brand_new_only = normalize_listing(
            "autobridge",
            "https://example.test/brand-new",
            "brand-new",
            (
                "Range Rover L460 D350 Autobiography. Model Year 2026. "
                "Exterior: Black. Interior: Ebony. Brand new. In stock. "
                "Rear Seat Entertainment."
            ),
            {},
            RANGE_ROVER_PROFILE,
            market="georgia",
        )
        legacy_teramont = normalize_listing(
            "drom",
            "https://example.test/teramont-brand-new",
            "teramont-brand-new",
            (
                "Volkswagen Teramont Pro 2026 Peak. Black on black. DCC. "
                "Brand new. In stock. Москва."
            ),
            {},
            TERAMONT_PROFILE,
        )

        self.assertIsNone(copyright_only.year)
        self.assertIsNone(copyright_year_only.year)
        self.assertIsNone(brand_new_only.mileage_km)
        self.assertEqual(legacy_teramont.mileage_km, 0)

    def test_german_fixture_parses_fields_and_qualifies_relevant(self) -> None:
        page = (FIXTURES / "range_rover_detail_de_positive.html").read_text(encoding="utf-8")
        text, metadata = extract_detail(page)
        listing = normalize_listing(
            "mobile_de",
            "https://suchen.mobile.de/fahrzeuge/details.html?id=123",
            "123",
            text,
            metadata,
            RANGE_ROVER_PROFILE,
            market="europe",
        )

        self.assertEqual(listing.year, 2026)
        self.assertIs(listing.exterior_black.value, True)
        self.assertIs(listing.interior_black.value, True)
        self.assertEqual(listing.mileage_km, 19)
        self.assertIs(listing.in_stock.value, True)
        self.assertIs(listing.steering_left.value, True)
        self.assertEqual(qualify(listing, RANGE_ROVER_PROFILE), ("relevant", ()))

    def test_checked_detail_fixtures_drive_normalization_contract(self) -> None:
        cases = (
            ("range_rover_detail_en.html", "europe", "relevant"),
            ("range_rover_detail_ru.html", "russia", "relevant"),
            ("range_rover_detail_de_negative.html", "europe", "irrelevant"),
        )
        for filename, market, expected_status in cases:
            with self.subTest(filename=filename):
                page = (FIXTURES / filename).read_text(encoding="utf-8")
                text, metadata = extract_detail(page)
                listing = normalize_listing(
                    "fixture",
                    f"https://example.test/{filename}",
                    filename,
                    text,
                    metadata,
                    RANGE_ROVER_PROFILE,
                    market=market,
                )
                self.assertEqual(qualify(listing, RANGE_ROVER_PROFILE)[0], expected_status)

    def test_teramont_pro_positive_wins_over_generic_breadcrumb_only(self) -> None:
        mixed = normalize_listing(
            "drom",
            "https://example.test/teramont-pro",
            "teramont-pro",
            (
                "Breadcrumb: Volkswagen Teramont. Volkswagen Teramont Pro 2026 Peak. "
                "Black on black. DCC. Mileage 19 km. In stock. Москва."
            ),
            {},
            TERAMONT_PROFILE,
        )
        bare = normalize_listing(
            "drom",
            "https://example.test/teramont",
            "teramont",
            (
                "Volkswagen Teramont 2026 Peak. Black on black. DCC. "
                "Mileage 19 km. In stock. Москва."
            ),
            {},
            TERAMONT_PROFILE,
        )

        self.assertIs(mixed.model_match.value, True)
        self.assertEqual(qualify(mixed, TERAMONT_PROFILE), ("relevant", ()))
        self.assertIs(bare.model_match.value, False)

    def test_workflow_preserves_established_concurrency_group(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        match = re.search(r"concurrency:\s*\n\s*group:\s*([^\s]+)", text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "teramont-monitor")

    def test_workflow_timeout_covers_all_sequential_requests_with_headroom(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        match = re.search(r"timeout-minutes:\s*(\d+)", text)
        self.assertIsNotNone(match)
        timeout_seconds = int(match.group(1)) * 60
        configured_sources = []
        for filename in ("config/sources.json", "config/range-rover-sources.json"):
            payload = json.loads((ROOT / filename).read_text(encoding="utf-8"))
            request_timeout = float(payload["timeout_seconds"])
            for source in payload["sources"]:
                configured_sources.append((source, request_timeout))
        worst_case_seconds = sum(
            (1 + int(source["max_details"])) * request_timeout
            + max(0, int(source["max_details"]) - 1) * float(source["delay_seconds"])
            for source, request_timeout in configured_sources
        )

        self.assertEqual(len(configured_sources), 14)
        self.assertEqual(sum(int(source["max_details"]) for source, _ in configured_sources), 290)
        self.assertGreaterEqual(timeout_seconds, worst_case_seconds + 10 * 60)
        readme = README.read_text(encoding="utf-8")
        self.assertIn("14 последовательных источников", readme)
        self.assertIn("до 290 карточек", readme)

    def test_collect_recovers_once_when_state_save_fails_after_pending_save(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("teramont_monitor.cli.save_state", side_effect=OSError("state write failed")):
                with self.assertRaisesRegex(OSError, "state write failed"):
                    cli_module.collect(
                        CONFIG,
                        root,
                        fetcher=fixture_fetcher,
                        observed_at="2026-08-28T12:00:00Z",
                    )

            first_pending = load_pending(root / "pending-events.json")
            self.assertEqual(len(first_pending), 5)
            self.assertEqual(load_state(root / "state.json").listings, {})

            cli_module.collect(
                CONFIG,
                root,
                fetcher=fixture_fetcher,
                observed_at="2026-08-28T12:00:00Z",
            )
            retried_pending = load_pending(root / "pending-events.json")
            self.assertEqual([event.id for event in retried_pending], [event.id for event in first_pending])
            self.assertEqual(len(load_state(root / "state.json").listings), 5)

            deliveries: list[str] = []
            self.assertEqual(
                send_pending(
                    root,
                    "bot-token",
                    "chat-id",
                    transport=lambda _token, _chat, text: deliveries.append(text),
                ),
                5,
            )
            self.assertEqual(len(deliveries), 5)
            cli_module.collect(
                CONFIG,
                root,
                fetcher=fixture_fetcher,
                observed_at="2026-08-28T13:00:00Z",
            )
            self.assertEqual(load_pending(root / "pending-events.json"), [])

    def test_pending_save_failure_cannot_advance_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("teramont_monitor.cli.save_pending", side_effect=OSError("pending write failed")):
                with self.assertRaisesRegex(OSError, "pending write failed"):
                    cli_module.collect(
                        CONFIG,
                        root,
                        fetcher=fixture_fetcher,
                        observed_at="2026-08-28T12:00:00Z",
                    )

            self.assertEqual(load_state(root / "state.json").listings, {})
            self.assertEqual(load_pending(root / "pending-events.json"), [])


if __name__ == "__main__":
    unittest.main()
