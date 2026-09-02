import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from universal_search.auth import AccessAuthenticator
from universal_search.adapters import enrich_detail_page
from universal_search.evidence import apply_page_enrichment, parse_listing
from universal_search.fx import FxSnapshot, normalize_listing_price
from universal_search.market_adapters import apply_site_adapter, enrich_myauto_payload, myauto_api_url
from universal_search.ratelimit import SlidingWindowLimiter
from universal_search.schema import SearchProfile
from universal_search.store import ActiveSearchLimitReached, Store
from universal_search.vision import VisionOutcome, VisionVerifier, apply_vision_confirmations
from universal_search.wizard import next_questions
from universal_search.worker import run_search


class UniversalSearchTests(unittest.TestCase):
    def test_wizard_starts_with_make_and_model(self):
        keys = [q.key for q in next_questions(SearchProfile())]
        self.assertEqual(keys, ["make", "model"])

    def test_wizard_asks_trim_and_body_after_model(self):
        p = SearchProfile(make="Cadillac", model="Escalade")
        keys = [q.key for q in next_questions(p)]
        self.assertEqual(keys, ["trim", "body_variants"])

    def test_optional_blank_answers_do_not_loop(self):
        p = SearchProfile(
            make="BMW", model="X5", year_from=2025, max_mileage_km=1000,
            condition="either", markets=["Европа"],
            answered_fields=["trim", "body_variants", "condition", "colors", "required_features", "max_price", "export_vat_required"],
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

    def test_vision_can_confirm_photo_only_feature(self):
        profile = SearchProfile(make="Cadillac", model="Escalade", year_from=2026, max_mileage_km=1000, markets=["Европа"], required_features=["задние экраны"])
        listing = parse_listing("https://example.com/a", "example.com", "Cadillac Escalade", "2026 Cadillac Escalade 10 km", profile)
        self.assertEqual(listing.status, "candidate")
        apply_vision_confirmations(listing, {
            "задние экраны": {"value": True, "source": "vision", "source_text": "vision image 2: two factory screens", "confidence": 0.96}
        })
        self.assertEqual(listing.status, "relevant")
        self.assertEqual(listing.evidence["задние экраны"]["source"], "vision")

    def test_mobile_adapter_extracts_nearest_net_and_export_price(self):
        profile = SearchProfile(
            make="Cadillac", model="Escalade", trim="Sport Platinum", year_from=2026,
            max_mileage_km=1000, condition="new", markets=["Европа"], colors=["чёрный"],
            body_variants=["ESV"], required_features=["панорамная крыша", "задние экраны", "широкая цифровая торпеда"],
            max_price=111000, price_currency="EUR", export_vat_required=True,
        )
        html = '''
        <html><head>
          <meta property="og:image" content="/photo1.jpg">
          <script type="application/ld+json">{
            "@type":"Vehicle", "name":"Cadillac Escalade ESV Sport Platinum",
            "vehicleModelDate":"2026", "color":"Black", "vehicleIdentificationNumber":"1GYS97KL0TR167970",
            "mileageFromOdometer":{"value":10,"unitCode":"KMT"},
            "offers":{"price":"133800","priceCurrency":"EUR"}
          }</script>
        </head><body>
          Neuwagen Cadillac Escalade ESV Sport Platinum. Black. 55-inch curved OLED.
          Panoramic roof. Rear Seat Entertainment. €133 800 brutto / €112 437 netto.
          NON-EU Exportpreis €110 000. MwSt. ausweisbar.
        </body></html>'''
        enrichment = enrich_detail_page("https://suchen.mobile.de/fahrzeuge/details.html?id=123", html)
        self.assertEqual(enrichment.net_price, 112437)
        self.assertEqual(enrichment.gross_price, 133800)
        self.assertEqual(enrichment.export_price, 110000)
        self.assertTrue(enrichment.export_vat)
        self.assertEqual(enrichment.body_variant, "ESV")
        self.assertTrue(enrichment.image_urls[0].endswith("/photo1.jpg"))
        listing = parse_listing("https://suchen.mobile.de/fahrzeuge/details.html?id=123", "mobile.de", "Cadillac Escalade ESV Sport Platinum 2026", enrichment.text, profile)
        apply_page_enrichment(listing, profile, enrichment)
        self.assertEqual(listing.status, "relevant")
        self.assertEqual(listing.export_price, 110000)
        self.assertEqual(listing.vin, "1GYS97KL0TR167970")

    def test_dubicars_adapter_marks_export_and_regional_spec(self):
        html = '''<html><body>
        2026 Cadillac Escalade Sport Platinum, GCC Specs, Dubai, Can be exported,
        AED 445000, 120 km, panoramic roof, rear entertainment screens, 55-inch display.
        </body></html>'''
        enrichment = enrich_detail_page("https://www.dubicars.com/2026-cadillac-escalade-1.html", html)
        self.assertTrue(enrichment.export_status)
        self.assertEqual(enrichment.regional_spec, "GCC")
        self.assertEqual(enrichment.location, "Dubai, UAE")

    def test_autoscout_adapter_marks_vat_and_export(self):
        html = '''<html><body>Cadillac Escalade Sport Platinum. MwSt. ausweisbar. NON-EU export only.</body></html>'''
        enrichment = enrich_detail_page("https://www.autoscout24.de/angebote/x", html)
        enrichment = apply_site_adapter("https://www.autoscout24.de/angebote/x", enrichment)
        self.assertEqual(enrichment.vat_status, "VAT deductible")
        self.assertTrue(enrichment.export_status)

    def test_myauto_api_url_and_payload_enrichment(self):
        url = "https://www.myauto.ge/en/pr/89476234/for-sale-cadillac-escalade"
        self.assertEqual(myauto_api_url(url), "https://api2.myauto.ge/ka/products/89476234")
        payload = {
            "data": {
                "info": {
                    "make": "Cadillac", "model": "Escalade ESV Sport Platinum", "prod_year": 2026,
                    "mileage": 42, "price": 119000, "currency": "USD", "city": "Tbilisi",
                    "photo": "https://static.myauto.ge/photos/89476234-1.jpg",
                }
            }
        }
        enrichment = enrich_myauto_payload(url, payload)
        self.assertEqual(enrichment.location, "Tbilisi")
        self.assertEqual(enrichment.body_variant, "ESV")
        self.assertEqual(enrichment.price_currency, "USD")
        self.assertIn("42 km", enrichment.text)
        self.assertIn("119000 USD", enrichment.text)
        self.assertEqual(len(enrichment.image_urls), 1)

    def test_fx_normalization_can_resolve_cross_currency_limit(self):
        profile = SearchProfile(
            make="Cadillac", model="Escalade", markets=["ОАЭ"], max_price=115000,
            price_currency="EUR",
        )
        listing = parse_listing(
            "https://example.com/a", "example.com", "Cadillac Escalade",
            "Cadillac Escalade 2026 10 km AED 445000", profile,
        )
        self.assertIn("price_currency", listing.missing)
        snapshot = FxSnapshot(rates={"AED": 3.6725, "EUR": 0.85}, updated_at="test")
        normalize_listing_price(listing, profile, snapshot)
        self.assertEqual(listing.normalized_currency, "EUR")
        self.assertAlmostEqual(listing.normalized_price, 445000 / 3.6725 * 0.85, places=2)
        self.assertNotIn("price_currency", listing.missing)

    def test_rate_limiter_rejects_after_limit(self):
        limiter = SlidingWindowLimiter()
        self.assertTrue(limiter.allow("1.2.3.4", "api", 2, 60))
        self.assertTrue(limiter.allow("1.2.3.4", "api", 2, 60))
        self.assertFalse(limiter.allow("1.2.3.4", "api", 2, 60))
        self.assertTrue(limiter.allow("5.6.7.8", "api", 2, 60))

    def test_personal_access_codes_resolve_stable_user(self):
        auth = AccessAuthenticator('{"dmitry":"secret-one","manager":"secret-two"}', "admin-secret")
        self.assertTrue(auth.configured)
        self.assertEqual(auth.authenticate("secret-one"), "dmitry")
        self.assertEqual(auth.authenticate("secret-two"), "manager")
        self.assertIsNone(auth.authenticate("wrong"))
        self.assertTrue(auth.authenticate_admin("admin-secret"))
        self.assertFalse(auth.authenticate_admin("wrong"))

    def test_vision_is_fail_closed_and_caps_expensive_inputs(self):
        with patch.dict(
            os.environ,
            {
                "OPENAI_VISION_ENABLED": "0",
                "OPENAI_VISION_DETAIL": "high",
                "OPENAI_VISION_MAX_IMAGES": "99",
                "OPENAI_VISION_MAX_CANDIDATES_PER_RUN": "99",
            },
        ):
            verifier = VisionVerifier(api_key="not-a-real-key")
        self.assertFalse(verifier.configured)
        self.assertEqual(verifier.detail, "low")
        self.assertEqual(verifier.max_images, 2)
        self.assertEqual(verifier.max_candidates_per_run, 2)

    def test_vision_persists_response_usage_without_live_api_call(self):
        profile = SearchProfile(
            make="Cadillac",
            model="Escalade",
            markets=["Европа"],
            required_features=["задние экраны"],
        )
        listing = parse_listing(
            "https://example.com/vision",
            "example.com",
            "Cadillac Escalade",
            "2026 Cadillac Escalade 10 km",
            profile,
        )
        listing.image_urls = ["https://images.example.com/one.jpg"]
        api_payload = {
            "usage": {
                "input_tokens": 700,
                "output_tokens": 120,
                "input_tokens_details": {"cached_tokens": 50},
            },
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "results": [
                                        {
                                            "feature": "задние экраны",
                                            "status": "confirmed",
                                            "confidence": 0.97,
                                            "evidence": "two factory screens",
                                            "image_index": 0,
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ],
                }
            ],
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(api_payload, ensure_ascii=False).encode("utf-8")

        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "OPENAI_VISION_ENABLED": "1",
                "OPENAI_DAILY_TOKEN_LIMIT": "50000",
                "OPENAI_DAILY_COST_LIMIT_USD": "0.10",
            },
        ):
            store = Store(f"{td}/db.sqlite")
            search_id, _, _ = store.create_search(profile, owner_id="dmitry")
            run_id, _ = store.start_run(search_id)
            verifier = VisionVerifier(api_key="not-a-real-key")
            with patch("universal_search.vision.urlopen", return_value=FakeResponse()):
                outcome = verifier.verify(
                    listing,
                    ["задние экраны"],
                    store=store,
                    search_id=search_id,
                    run_id=run_id,
                )
            self.assertTrue(outcome.attempted)
            self.assertEqual(outcome.status, "succeeded")
            self.assertIn("задние экраны", outcome.confirmations)
            report = store.usage_report()
            self.assertEqual(report["totals"]["input_tokens"], 700)
            self.assertEqual(report["totals"]["output_tokens"], 120)
            self.assertEqual(report["totals"]["cached_tokens"], 50)
            self.assertEqual(report["totals"]["images"], 1)

    def test_store_enforces_owner_active_count_ttl_and_run_cooldown(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(f"{td}/db.sqlite")
            profile = SearchProfile(make="BMW", model="X5", markets=["Европа"])
            search_id, _, _ = store.create_search(profile, owner_id="dmitry", ttl_hours=1)
            self.assertEqual(store.active_search_count("dmitry"), 1)
            run_id, reason = store.start_run(search_id)
            self.assertIsNotNone(run_id)
            self.assertIsNone(reason)
            second_run, second_reason = store.start_run(search_id)
            self.assertIsNone(second_run)
            self.assertEqual(second_reason, "search is already running")
            store.finish_run(search_id, run_id, 60, status="succeeded")
            cooldown_run, cooldown_reason = store.start_run(search_id)
            self.assertIsNone(cooldown_run)
            self.assertEqual(cooldown_reason, "search cooldown is active")
            store.disable_search(search_id)
            self.assertEqual(store.active_search_count("dmitry"), 0)

    def test_active_search_limit_is_enforced_inside_creation_transaction(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(f"{td}/db.sqlite")
            profile = SearchProfile(make="BMW", model="X5", markets=["Европа"])
            store.create_search(profile, owner_id="dmitry", max_active=1)
            with self.assertRaises(ActiveSearchLimitReached):
                store.create_search(profile, owner_id="dmitry", max_active=1)

    def test_usage_reservation_is_atomic_and_records_exact_response_usage(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(f"{td}/db.sqlite")
            profile = SearchProfile(make="BMW", model="X5", markets=["Европа"])
            search_id, _, _ = store.create_search(profile, owner_id="dmitry")
            run_id, _ = store.start_run(search_id)
            usage_id, _ = store.reserve_api_call(
                search_id=search_id,
                run_id=run_id,
                model="gpt-5.6-luna",
                images=2,
                reserved_tokens=3000,
                reserved_cost_usd=0.01,
                daily_token_limit=5000,
                daily_cost_limit_usd=0.10,
            )
            self.assertIsNotNone(usage_id)
            blocked_id, current = store.reserve_api_call(
                search_id=search_id,
                run_id=run_id,
                model="gpt-5.6-luna",
                images=2,
                reserved_tokens=3000,
                reserved_cost_usd=0.01,
                daily_token_limit=5000,
                daily_cost_limit_usd=0.10,
            )
            self.assertIsNone(blocked_id)
            self.assertEqual(current["budget_tokens"], 3000)
            store.finalize_api_call(
                usage_id,
                status="succeeded",
                input_tokens=100,
                output_tokens=50,
                cached_tokens=10,
                estimated_cost_usd=0.0001,
            )
            report = store.usage_report()
            self.assertEqual(report["totals"]["api_calls"], 1)
            self.assertEqual(report["totals"]["input_tokens"], 100)
            self.assertEqual(report["totals"]["output_tokens"], 50)
            self.assertEqual(report["totals"]["cached_tokens"], 10)
            self.assertEqual(report["totals"]["images"], 2)
            self.assertEqual(report["totals"]["budget_tokens"], 150)

    def test_successful_vision_signature_is_not_rechecked(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(f"{td}/db.sqlite")
            self.assertTrue(store.vision_check_due("s", "f", "sig"))
            store.record_vision_check("s", "f", "sig", "succeeded")
            self.assertFalse(store.vision_check_due("s", "f", "sig"))
            self.assertTrue(store.vision_check_due("s", "f", "changed"))

    def test_worker_caps_vision_candidates_per_run(self):
        profile = SearchProfile(
            make="Cadillac",
            model="Escalade",
            markets=["Европа"],
            required_features=["задние экраны"],
        )
        listings = []
        for index in range(3):
            listing = parse_listing(
                f"https://example.com/{index}",
                "example.com",
                f"Cadillac Escalade {index}",
                "2026 Cadillac Escalade 10 km",
                profile,
            )
            listing.image_urls = [f"https://images.example.com/{index}.jpg"]
            listings.append(listing)

        class FakeProvider:
            def search(self, _profile):
                return listings, []

        class FakeVision:
            configured = True
            disabled_reason = ""
            max_candidates_per_run = 2

            def __init__(self):
                self.calls = 0

            def signature(self, listing, features):
                return listing.fingerprint + ":" + ",".join(features)

            def verify(self, listing, features, **kwargs):
                self.calls += 1
                return VisionOutcome(attempted=True, status="succeeded")

        with tempfile.TemporaryDirectory() as td:
            store = Store(f"{td}/db.sqlite")
            search_id, _, _ = store.create_search(profile, owner_id="dmitry")
            vision = FakeVision()
            result = run_search(
                store,
                search_id,
                profile,
                provider=FakeProvider(),
                vision=vision,
            )
            self.assertEqual(vision.calls, 2)
            self.assertEqual(result["vision_candidates"], 2)

    def test_store_migrates_legacy_search_table(self):
        with tempfile.TemporaryDirectory() as td:
            path = f"{td}/legacy.sqlite"
            with sqlite3.connect(path) as db:
                db.executescript(
                    """
                    CREATE TABLE searches (
                      id TEXT PRIMARY KEY, owner_token TEXT NOT NULL, profile_json TEXT NOT NULL,
                      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, next_run_at TEXT,
                      last_run_at TEXT, telegram_chat_id TEXT, telegram_bind_code TEXT,
                      enabled INTEGER NOT NULL DEFAULT 1
                    );
                    CREATE TABLE listings (
                      search_id TEXT NOT NULL, fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL,
                      first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                      last_notified_price REAL, PRIMARY KEY(search_id, fingerprint)
                    );
                    CREATE INDEX idx_searches_due ON searches(enabled, next_run_at);
                    """
                )
            store = Store(path)
            with store.conn() as db:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(searches)")}
            self.assertTrue({"owner_id", "expires_at", "run_lock_until"}.issubset(columns))

    def test_store_deduplicates_same_listing(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(f"{td}/db.sqlite")
            p = SearchProfile(make="BMW", model="X5", markets=["Европа"])
            sid, _, _ = store.create_search(p)
            item = parse_listing("https://example.com/x?utm=1", "example.com", "BMW X5", "2026 BMW X5 15 km", p)
            first, _ = store.save_listing(sid, item)
            second, _ = store.save_listing(sid, item)
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(len(store.list_results(sid)), 1)


if __name__ == "__main__":
    unittest.main()
