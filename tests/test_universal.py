import tempfile
import unittest

from universal_search.evidence import parse_listing
from universal_search.schema import SearchProfile
from universal_search.store import Store
from universal_search.wizard import next_questions


class UniversalSearchTests(unittest.TestCase):
    def test_wizard_starts_with_make_and_model(self):
        keys = [q.key for q in next_questions(SearchProfile())]
        self.assertEqual(keys, ["make", "model"])

    def test_optional_blank_answers_do_not_loop(self):
        p = SearchProfile(
            make="BMW", model="X5", year_from=2025, max_mileage_km=1000,
            condition="either", markets=["Европа"],
            answered_fields=["condition", "colors", "required_features", "max_price", "export_vat_required"],
        )
        self.assertEqual(next_questions(p), [])

    def test_required_features_gate_relevance(self):
        profile = SearchProfile(
            make="Cadillac", model="Escalade", year_from=2026, max_mileage_km=1000,
            markets=["Европа"], colors=["чёрный"],
            required_features=["панорамная крыша", "задние экраны", "широкая цифровая торпеда"],
        )
        text = "2026 Cadillac Escalade black 10 km panoramic roof rear seat entertainment dual 12.6 screens 55-inch curved OLED €112,437"
        listing = parse_listing("https://example.com/a", "example.com", "Cadillac Escalade", text, profile)
        self.assertEqual(listing.status, "relevant")
        self.assertEqual(listing.mileage_km, 10)
        self.assertEqual(listing.currency, "EUR")

    def test_year_is_not_mistaken_for_price(self):
        profile = SearchProfile(make="Cadillac", model="Escalade", year_from=2026, max_mileage_km=1000, markets=["Европа"])
        listing = parse_listing("https://example.com/a", "example.com", "2026 Cadillac", "2026 Cadillac Escalade 10 km", profile)
        self.assertIsNone(listing.price)

    def test_missing_feature_is_candidate_not_relevant(self):
        profile = SearchProfile(make="Cadillac", model="Escalade", year_from=2026, max_mileage_km=1000, markets=["Европа"], required_features=["задние экраны"])
        listing = parse_listing("https://example.com/a", "example.com", "Cadillac Escalade", "2026 10 km panoramic roof", profile)
        self.assertEqual(listing.status, "candidate")
        self.assertIn("задние экраны", listing.missing)

    def test_store_deduplicates_same_listing(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(f"{td}/db.sqlite")
            p = SearchProfile(make="BMW", model="X5", markets=["Европа"])
            sid, _, _ = store.create_search(p)
            item = parse_listing("https://example.com/x?utm=1", "example.com", "BMW X5", "2026 15 km", p)
            first, _ = store.save_listing(sid, item)
            second, _ = store.save_listing(sid, item)
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(len(store.list_results(sid)), 1)


if __name__ == "__main__":
    unittest.main()
