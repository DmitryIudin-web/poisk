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


if __name__ == "__main__":
    unittest.main()
