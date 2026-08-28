from __future__ import annotations

import unittest
from pathlib import Path

from teramont_monitor.models import Listing
from teramont_monitor.normalize import normalize_listing
from teramont_monitor.profiles import load_target_profile


ROOT = Path(__file__).resolve().parents[1]
TERAMONT_PROFILE = load_target_profile(ROOT / "config/targets/teramont-pro-2026.json")
RANGE_ROVER_PROFILE = load_target_profile(
    ROOT / "config/targets/range-rover-l460-d350-autobiography-2026.json"
)


class NormalizeListingTests(unittest.TestCase):
    def test_legacy_signature_preserves_teramont_evidence_and_target(self) -> None:
        listing = normalize_listing(
            "drom",
            "https://auto.drom.ru/legacy-signature.html",
            "legacy-signature",
            "Volkswagen Teramont Pro 2026 Peak, black on black, DCC, в наличии",
            {},
        )

        self.assertEqual(listing.target_id, "teramont-pro-2026")
        self.assertEqual(listing.target_name, "Volkswagen Teramont Pro 2026")
        self.assertIs(listing.model_match.value, True)
        self.assertIs(listing.top_trim.value, True)
        self.assertIs(listing.dcc.value, True)

    def test_normalizes_explicit_target_and_commercial_fields(self) -> None:
        listing = normalize_listing(
            source="drom",
            url="https://auto.drom.ru/moscow/volkswagen/teramont/1.html",
            listing_id="1",
            text=(
                "Volkswagen Teramont Pro 2026 Peak. Цвет кузова: черный. "
                "Цвет салона: черный. Адаптивная подвеска DCC. "
                "Пробег 23 км. Автомобиль физически в наличии в Москве. "
                "Цена за наличные 5 999 000 ₽. VIN WVGZZZCA1RC123456. "
                "ЭПТС действующий. Коммерческий утильсбор уплачен."
            ),
            metadata={},
            profile=TERAMONT_PROFILE,
        )

        self.assertIsInstance(listing, Listing)
        self.assertTrue(listing.model_match.value)
        self.assertEqual(listing.year, 2026)
        self.assertTrue(listing.exterior_black.value)
        self.assertTrue(listing.interior_black.value)
        self.assertTrue(listing.top_trim.value)
        self.assertTrue(listing.dcc.value)
        self.assertEqual(listing.mileage_km, 23)
        self.assertTrue(listing.in_stock.value)
        self.assertEqual(listing.cash_price, 5_999_000)
        self.assertEqual(listing.price_currency, "RUB")
        self.assertEqual(listing.vin, "WVGZZZCA1RC123456")
        self.assertEqual(listing.region, "russia")
        self.assertEqual(listing.epts_status, "valid")
        self.assertEqual(listing.commercial_recycling_fee_status, "paid")

    def test_conditional_advertised_price_is_not_cash_price(self) -> None:
        listing = normalize_listing(
            "avito",
            "https://www.avito.ru/item/2",
            "2",
            "Volkswagen Teramont Pro 2026 Summit, цена 5 400 000 ₽ только при кредите и trade-in",
            {},
            TERAMONT_PROFILE,
        )

        self.assertIsNone(listing.cash_price)
        self.assertEqual(listing.advertised_price, 5_400_000)
        self.assertEqual(listing.price_qualifier, "conditional")

    def test_explicit_cash_price_is_captured_separately_from_credit_price(self) -> None:
        listing = normalize_listing(
            "avito",
            "https://www.avito.ru/item/20",
            "20",
            (
                "Volkswagen Teramont Pro 2026 Summit. Цена 5 400 000 ₽ только при кредите. "
                "Полная цена за наличные 5 999 000 ₽."
            ),
            {},
            TERAMONT_PROFILE,
        )

        self.assertEqual(listing.advertised_price, 5_400_000)
        self.assertEqual(listing.cash_price, 5_999_000)

    def test_eaeu_cash_price_keeps_source_currency(self) -> None:
        listing = normalize_listing(
            "mashina",
            "https://m.mashina.kg/details/21",
            "21",
            "Volkswagen Teramont Pro 2026 Summit. За наличные 6 500 000 сом.",
            {},
            TERAMONT_PROFILE,
        )

        self.assertEqual(listing.cash_price, 6_500_000)
        self.assertEqual(listing.price_currency, "KGS")

    def test_missing_critical_facts_stay_unknown(self) -> None:
        listing = normalize_listing(
            "mashina",
            "https://m.mashina.kg/details/3",
            "3",
            "Volkswagen Teramont Pro 2026, Бишкек, 6 500 000 сом",
            {},
            TERAMONT_PROFILE,
        )

        self.assertIsNone(listing.exterior_black.value)
        self.assertIsNone(listing.interior_black.value)
        self.assertIsNone(listing.dcc.value)
        self.assertIsNone(listing.in_stock.value)
        self.assertIsNone(listing.mileage_km)
        self.assertEqual(listing.region, "bishkek")
        self.assertEqual(listing.price_currency, "KGS")

    def test_black_on_black_and_new_are_supported(self) -> None:
        listing = normalize_listing(
            "kolesa",
            "https://kolesa.kz/a/show/4",
            "4",
            "Volkswagen Teramont Pro 2026, black on black, Summit, DCC, новый, в наличии, Алматы",
            {},
            TERAMONT_PROFILE,
        )

        self.assertTrue(listing.exterior_black.value)
        self.assertTrue(listing.interior_black.value)
        self.assertEqual(listing.mileage_km, 0)
        self.assertEqual(listing.region, "eaeu_other")

    def test_metadata_is_used_without_overwriting_explicit_text(self) -> None:
        listing = normalize_listing(
            "autoru",
            "https://auto.ru/cars/new/sale/volkswagen/teramont/5-abc/",
            "5-abc",
            "Volkswagen Teramont Pro 2026 Peak, цвет кузова белый",
            {"price": 6_100_000, "price_currency": "RUB", "location": "Москва"},
            TERAMONT_PROFILE,
        )

        self.assertFalse(listing.exterior_black.value)
        self.assertEqual(listing.advertised_price, 6_100_000)
        self.assertIsNone(listing.cash_price)

    def test_negative_epts_and_recycling_fee_wording_is_not_misread_as_paid(self) -> None:
        listing = normalize_listing(
            "autoru",
            "https://auto.ru/cars/new/sale/volkswagen/teramont/9/",
            "9",
            (
                "Volkswagen Teramont Pro 2026 Peak, black on black, DCC, "
                "пробег 10 км, в наличии. ЭПТС не оформлен. "
                "Коммерческий утильсбор не уплачен."
            ),
            {},
            TERAMONT_PROFILE,
        )

        self.assertEqual(listing.epts_status, "missing")
        self.assertEqual(listing.commercial_recycling_fee_status, "unpaid")

    def test_in_dealership_phrase_is_not_an_interior_color(self) -> None:
        listing = normalize_listing(
            "drom",
            "https://auto.drom.ru/6.html",
            "6",
            "Volkswagen Teramont Pro 2026 Peak, цвет кузова: черный, DCC, автомобиль в салоне",
            {},
            TERAMONT_PROFILE,
        )
        self.assertIsNone(listing.interior_black.value)

    def test_title_never_falls_back_to_phone_or_email_from_page_text(self) -> None:
        listing = normalize_listing(
            "drom",
            "https://auto.drom.ru/7.html",
            "7",
            (
                "Volkswagen Teramont Pro 2026 Peak, black on black, DCC, "
                "пробег 10 км, в наличии. Телефон +7 999 123-45-67, seller@example.com"
            ),
            {},
            TERAMONT_PROFILE,
        )

        self.assertIn("Volkswagen Teramont Pro", listing.title)
        self.assertNotIn("999", listing.title)
        self.assertNotIn("@", listing.title)

    def test_structured_title_has_contact_details_removed(self) -> None:
        listing = normalize_listing(
            "drom",
            "https://auto.drom.ru/8.html",
            "8",
            "Volkswagen Teramont Pro 2026 Peak, black on black, DCC, пробег 10 км, в наличии",
            {"title": "Teramont Pro +7 (999) 123-45-67 sales@example.com"},
            TERAMONT_PROFILE,
        )

        self.assertNotIn("999", listing.title)
        self.assertNotIn("@", listing.title)

    def test_normalizes_range_rover_profile_evidence_and_georgian_market(self) -> None:
        listing = normalize_listing(
            "autobridge",
            "https://autobridge.ge/en/listings/range-rover-abc123",
            "abc123",
            "Range Rover L460 D350 2026 Autobiography. Exterior: Santorini Black. "
            "Interior: Ebony Black. Mileage 19 km. In stock. Left hand drive. "
            "Factory Rear Seat Entertainment with two rear screens.",
            {"price": 167_000, "price_currency": "EUR", "location": "Tbilisi"},
            RANGE_ROVER_PROFILE,
            market="georgia",
        )

        self.assertEqual(listing.target_id, "range-rover-l460-d350-autobiography-2026")
        self.assertIs(listing.powertrain_match.value, True)
        self.assertIs(listing.rear_seat_entertainment.value, True)
        self.assertIs(listing.steering_left.value, True)
        self.assertEqual(listing.price_currency, "EUR")
        self.assertEqual(listing.region, "georgia")

    def test_profile_negative_evidence_wins_for_range_rover_variant(self) -> None:
        listing = normalize_listing(
            "mobile",
            "https://suchen.mobile.de/fahrzeuge/details.html?id=1",
            "1",
            "Range Rover Sport D350 Autobiography 2026. Schwarz. "
            "Entfall Multimediasystem im Fond. Rechtslenker. 10 km. Sofort verfügbar.",
            {},
            RANGE_ROVER_PROFILE,
            market="europe",
        )

        self.assertIs(listing.model_match.value, False)
        self.assertIs(listing.rear_seat_entertainment.value, False)
        self.assertIs(listing.steering_left.value, False)

    def test_legacy_state_defaults_missing_target_identity_to_teramont(self) -> None:
        current = normalize_listing(
            "drom",
            "https://auto.drom.ru/legacy.html",
            "legacy",
            "Volkswagen Teramont Pro 2026 Peak, black on black, DCC, в наличии",
            {},
            TERAMONT_PROFILE,
        )
        legacy = Listing.from_dict(current.to_dict() | {"target_id": None, "target_name": None})

        self.assertEqual(legacy.target_id, "teramont-pro-2026")
        self.assertEqual(legacy.target_name, "Volkswagen Teramont Pro 2026")

    def test_gel_eur_and_usd_prices_keep_explicit_source_currency(self) -> None:
        cases = (
            ("Цена 450 000 ₾", "GEL"),
            ("Price €167 000", "EUR"),
            ("Price $167 000", "USD"),
        )
        for text, expected_currency in cases:
            with self.subTest(text=text):
                listing = normalize_listing(
                    "autobridge",
                    "https://example.test/currency",
                    "currency",
                    f"Range Rover L460 D350 2026 Autobiography. {text}",
                    {},
                    RANGE_ROVER_PROFILE,
                )
                self.assertEqual(listing.price_currency, expected_currency)

    def test_sold_status_overrides_in_stock(self) -> None:
        listing = normalize_listing(
            "autobridge",
            "https://example.test/sold",
            "sold",
            "Range Rover L460 D350 2026 Autobiography. In stock. Sold.",
            {},
            RANGE_ROVER_PROFILE,
        )

        self.assertIs(listing.sold.value, True)
        self.assertIs(listing.in_stock.value, False)

    def test_model_year_is_preferred_to_first_registration_year(self) -> None:
        listing = normalize_listing(
            "autobridge",
            "https://example.test/year",
            "year",
            "Range Rover L460 D350. First registration 2025. Model Year 2026.",
            {},
            RANGE_ROVER_PROFILE,
        )

        self.assertEqual(listing.year, 2026)

    def test_first_registration_without_model_year_leaves_year_unknown(self) -> None:
        listing = normalize_listing(
            "autobridge",
            "https://example.test/year-unknown",
            "year-unknown",
            "Range Rover L460 D350. First registration 2025.",
            {},
            RANGE_ROVER_PROFILE,
        )

        self.assertIsNone(listing.year)

    def test_ebony_is_black_but_secondary_ebony_is_not_primary_black_interior(self) -> None:
        ebony = normalize_listing(
            "autobridge",
            "https://example.test/ebony",
            "ebony",
            "Range Rover L460 D350. Interior: Ebony.",
            {},
            RANGE_ROVER_PROFILE,
        )
        light_cloud = normalize_listing(
            "autobridge",
            "https://example.test/light-cloud",
            "light-cloud",
            "Range Rover L460 D350. Interior: Light Cloud/ebony.",
            {},
            RANGE_ROVER_PROFILE,
        )

        self.assertIs(ebony.interior_black.value, True)
        self.assertIs(light_cloud.interior_black.value, False)


if __name__ == "__main__":
    unittest.main()
