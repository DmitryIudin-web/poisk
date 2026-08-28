from __future__ import annotations

import unittest
from pathlib import Path
from urllib.error import HTTPError

from teramont_monitor.profiles import load_target_profile
from teramont_monitor.sources import load_source_configs, scan_all, scan_source


ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
CONFIGS = load_source_configs(ROOT / "config" / "sources.json")
BY_NAME = {config.name: config for config in CONFIGS}
TERAMONT_PROFILE = load_target_profile(ROOT / "config" / "targets" / "teramont-pro-2026.json")
RANGE_ROVER_PROFILE = load_target_profile(
    ROOT / "config" / "targets" / "range-rover-l460-d350-autobiography-2026.json"
)
DETAIL = (
    "<html><body>Volkswagen Teramont Pro 2026 Peak, black on black, DCC, "
    "пробег 10 км, физически в наличии, Москва</body></html>"
)


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


class SourceScannerTests(unittest.TestCase):
    def test_loads_exactly_five_sources(self) -> None:
        self.assertEqual([config.name for config in CONFIGS], ["autoru", "drom", "avito", "mashina", "kolesa"])
        self.assertTrue(all(config.user_agent.startswith("Mozilla/5.0") for config in CONFIGS))

    def test_range_rover_sources_have_approved_markets(self) -> None:
        configs = load_source_configs(ROOT / "config" / "range-rover-sources.json")

        self.assertEqual(
            [(item.name, item.market) for item in configs],
            [
                ("autoru", "russia"), ("drom", "russia"), ("avito", "russia"),
                ("mashina", "kyrgyzstan"), ("myauto", "georgia"),
                ("landrover_georgia", "georgia"), ("autobridge", "georgia"),
                ("mobile_de", "europe"), ("autoscout24", "europe"),
            ],
        )

    def test_scanner_preserves_legacy_teramont_profile_when_omitted(self) -> None:
        config = BY_NAME["drom"]

        def fetch(url: str, _timeout: float) -> str:
            return fixture("drom") if url == config.search_url else DETAIL

        result = scan_source(config, fetcher=fetch, sleeper=lambda _: None)

        self.assertTrue(result.ok)
        self.assertEqual(result.listings[0].target_id, TERAMONT_PROFILE.target_id)

    def test_range_rover_profile_and_market_survive_a_blocked_detail(self) -> None:
        config = {
            item.name: item
            for item in load_source_configs(ROOT / "config" / "range-rover-sources.json")
        }["autobridge"]
        search = fixture("range_rover_sources").replace(
            "</body>",
            '<a href="https://autobridge.ge/en/listings/land-rover-range-rover-2026-9e8d7c6b">second</a></body>',
        )
        detail = (
            "<html><body>Range Rover D350 Autobiography 2026, rear seat entertainment, "
            "пробег 10 км, в наличии, Тбилиси</body></html>"
        )

        def fetch(url: str, _timeout: float) -> str:
            if url == config.search_url:
                return search
            if url.endswith("1a2b3c4d"):
                return "<html><title>Captcha — доступ ограничен</title></html>"
            return detail

        result = scan_source(config, RANGE_ROVER_PROFILE, fetcher=fetch, sleeper=lambda _: None)

        self.assertTrue(result.ok)
        self.assertEqual(len(result.listings), 1)
        self.assertEqual(result.listings[0].target_id, RANGE_ROVER_PROFILE.target_id)
        self.assertEqual(result.listings[0].region, "georgia")
        self.assertEqual([warning.code for warning in result.warnings], ["detail_blocked"])
        self.assertFalse(result.complete)

    def test_scans_search_and_detail_page(self) -> None:
        config = BY_NAME["drom"]

        def fetch(url: str, timeout: float) -> str:
            return fixture("drom") if url == config.search_url else DETAIL

        result = scan_source(config, TERAMONT_PROFILE, fetcher=fetch, sleeper=lambda _: None)

        self.assertTrue(result.ok)
        self.assertEqual(len(result.listings), 1)
        self.assertTrue(result.listings[0].model_match.value)

    def test_blocked_source_returns_gap_not_empty_market(self) -> None:
        result = scan_source(
            BY_NAME["drom"], TERAMONT_PROFILE,
            fetcher=lambda _url, _timeout: "<html><title>Captcha — доступ ограничен</title></html>",
            sleeper=lambda _: None,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.gap.code, "blocked")

    def test_captcha_word_in_normal_large_page_script_is_not_blocked(self) -> None:
        config = BY_NAME["avito"]
        search = "<html><title>Авито — объявления</title><script>captchaWidget</script>" + fixture("avito") + ("x" * 6_000)

        def fetch(url: str, _timeout: float) -> str:
            return search if url == config.search_url else DETAIL

        result = scan_source(config, TERAMONT_PROFILE, fetcher=fetch, sleeper=lambda _: None)

        self.assertTrue(result.ok)
        self.assertEqual(len(result.listings), 1)

    def test_http_429_is_rate_limit_gap(self) -> None:
        def fetch(url: str, _timeout: float) -> str:
            raise HTTPError(url, 429, "Too Many Requests", {}, None)

        result = scan_source(BY_NAME["autoru"], TERAMONT_PROFILE, fetcher=fetch, sleeper=lambda _: None)
        self.assertFalse(result.ok)
        self.assertEqual(result.gap.code, "rate_limited")

    def test_unexpected_empty_page_is_gap(self) -> None:
        result = scan_source(
            BY_NAME["mashina"], TERAMONT_PROFILE,
            fetcher=lambda _url, _timeout: "<html><body>navigation only</body></html>",
            sleeper=lambda _: None,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.gap.code, "unexpected_empty")

    def test_explicit_empty_marker_is_successful_empty_market(self) -> None:
        result = scan_source(
            BY_NAME["drom"], TERAMONT_PROFILE,
            fetcher=lambda _url, _timeout: "<html><body>Ничего не найдено</body></html>",
            sleeper=lambda _: None,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.listings, ())

    def test_detail_failure_is_warning_when_another_detail_succeeds(self) -> None:
        config = BY_NAME["kolesa"]
        search = fixture("kolesa").replace(
            "</body>", '<a href="/a/show/227501941">Volkswagen Teramont</a></body>'
        )

        def fetch(url: str, _timeout: float) -> str:
            if url == config.search_url:
                return search
            if url.endswith("1941"):
                raise TimeoutError("timed out")
            return DETAIL.replace("Москва", "Алматы")

        result = scan_source(config, TERAMONT_PROFILE, fetcher=fetch, sleeper=lambda _: None)
        self.assertTrue(result.ok)
        self.assertEqual(len(result.listings), 1)
        self.assertEqual(len(result.warnings), 1)
        self.assertEqual(result.warnings[0].code, "detail_timeout")
        self.assertFalse(result.complete)

    def test_hitting_detail_cap_marks_scan_incomplete(self) -> None:
        config = BY_NAME["kolesa"]
        search = fixture("kolesa").replace(
            "</body>", '<a href="/a/show/227501941">Volkswagen Teramont</a></body>'
        )
        limited = type(config)(**{**config.__dict__, "max_details": 1})

        def fetch(url: str, _timeout: float) -> str:
            return search if url == config.search_url else DETAIL

        result = scan_source(limited, TERAMONT_PROFILE, fetcher=fetch, sleeper=lambda _: None)

        self.assertTrue(result.ok)
        self.assertEqual(len(result.listings), 1)
        self.assertFalse(result.complete)

    def test_exact_detail_cap_is_still_incomplete(self) -> None:
        config = BY_NAME["kolesa"]
        limited = type(config)(**{**config.__dict__, "max_details": 1})

        def fetch(url: str, _timeout: float) -> str:
            return fixture("kolesa") if url == config.search_url else DETAIL

        result = scan_source(limited, TERAMONT_PROFILE, fetcher=fetch, sleeper=lambda _: None)

        self.assertTrue(result.ok)
        self.assertEqual(len(result.listings), 1)
        self.assertFalse(result.complete)

    def test_blocked_detail_page_is_not_normalized_as_a_listing(self) -> None:
        config = BY_NAME["drom"]

        def fetch(url: str, _timeout: float) -> str:
            return fixture("drom") if url == config.search_url else "<html><title>Captcha — доступ ограничен</title></html>"

        result = scan_source(config, TERAMONT_PROFILE, fetcher=fetch, sleeper=lambda _: None)

        self.assertFalse(result.ok)
        self.assertEqual(result.gap.code, "detail_failed")
        self.assertEqual(result.warnings[0].code, "detail_blocked")

    def test_one_source_failure_does_not_discard_other_sources(self) -> None:
        configs = [BY_NAME["drom"], BY_NAME["mashina"]]

        def fetch(url: str, _timeout: float) -> str:
            if "drom.ru" in url:
                raise TimeoutError("drom timeout")
            if url == BY_NAME["mashina"].search_url:
                return fixture("mashina")
            return DETAIL.replace("Москва", "Бишкек")

        results = scan_all(configs, TERAMONT_PROFILE, fetcher=fetch, sleeper=lambda _: None)
        self.assertFalse(results["drom"].ok)
        self.assertTrue(results["mashina"].ok)


if __name__ == "__main__":
    unittest.main()
