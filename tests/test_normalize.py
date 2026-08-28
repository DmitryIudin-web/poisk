from __future__ import annotations

import unittest

from teramont_monitor.models import Listing
from teramont_monitor.normalize import normalize_listing


class NormalizeListingTests(unittest.TestCase):
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
        )

        self.assertNotIn("999", listing.title)
        self.assertNotIn("@", listing.title)


if __name__ == "__main__":
    unittest.main()
