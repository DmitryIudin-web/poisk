from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from teramont_monitor.cli import collect, main, smoke
from teramont_monitor.sources import load_source_configs
from teramont_monitor.storage import load_pending, load_state


ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "sources.json"
FIXTURES = Path(__file__).parent / "fixtures"
SEARCH_URLS = {config.search_url for config in load_source_configs(CONFIG)}
DETAIL = (
    "<html><body>Volkswagen Teramont Pro 2026 Peak, black on black, DCC, "
    "пробег 10 км, физически в наличии, Москва</body></html>"
)


def fixture_fetcher(url: str, _timeout: float) -> str:
    mapping = {
        "auto.ru/cars/volkswagen": "autoru",
        "auto.drom.ru/volkswagen": "drom",
        "avito.ru/all/avtomobili": "avito",
        "m.mashina.kg/catalog": "mashina",
        "kolesa.kz/cars": "kolesa",
    }
    for marker, name in mapping.items():
        if marker in url:
            return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    return DETAIL.replace("Москва", "Бишкек" if "mashina.kg" in url else "Алматы" if "kolesa.kz" in url else "Москва")


class CliTests(unittest.TestCase):
    def test_collect_persists_state_history_and_pending_events(self) -> None:
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)

            summary = collect(CONFIG, state_dir, fetcher=fixture_fetcher, observed_at="2026-08-28T12:00:00Z")

            self.assertEqual(summary.successful_sources, 5)
            self.assertEqual(summary.failed_sources, 0)
            self.assertTrue((state_dir / "history.jsonl").exists())
            self.assertEqual(len(load_state(state_dir / "state.json").listings), 5)
            self.assertEqual(len(load_pending(state_dir / "pending-events.json")), 5)

    def test_dry_run_does_not_mutate_state(self) -> None:
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)
            summary = collect(CONFIG, state_dir, fetcher=fixture_fetcher, dry_run=True)
            self.assertEqual(summary.successful_sources, 5)
            self.assertEqual(list(state_dir.iterdir()), [])

    def test_persisted_state_history_and_events_exclude_page_contacts(self) -> None:
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)

            def contact_fetcher(url: str, timeout: float) -> str:
                page = fixture_fetcher(url, timeout)
                return page if url in SEARCH_URLS else page.replace(
                    "</body>", " Телефон +7 999 123-45-67 seller@example.com</body>"
                )

            collect(CONFIG, state_dir, fetcher=contact_fetcher, observed_at="2026-08-28T12:00:00Z")

            persisted = "\n".join(
                (state_dir / name).read_text(encoding="utf-8")
                for name in ("state.json", "history.jsonl", "pending-events.json")
            )
            self.assertNotIn("seller@example.com", persisted)
            self.assertNotIn("999 123", persisted)

    def test_zero_successful_sources_returns_failure_without_market_events(self) -> None:
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)

            def fail(_url: str, _timeout: float) -> str:
                raise TimeoutError("timed out")

            summary = collect(CONFIG, state_dir, fetcher=fail, observed_at="2026-08-28T12:00:00Z")
            self.assertEqual(summary.successful_sources, 0)
            self.assertEqual(summary.exit_code, 2)
            rows = [json.loads(line) for line in (state_dir / "history.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 5)
            self.assertEqual({row["type"] for row in rows}, {"source_gap"})
            self.assertEqual(load_pending(state_dir / "pending-events.json"), [])

    def test_smoke_never_writes_state(self) -> None:
        summary = smoke(CONFIG, fetcher=fixture_fetcher)
        self.assertEqual(summary.successful_sources, 5)
        self.assertEqual(set(summary.source_statuses), {"autoru", "drom", "avito", "mashina", "kolesa"})
        self.assertEqual(summary.source_statuses["drom"]["status"], "ok")
        self.assertEqual(summary.source_statuses["drom"]["listings"], 1)

    def test_notify_command_requires_both_environment_secrets(self) -> None:
        with TemporaryDirectory() as directory:
            exit_code = main(["notify", "--state-dir", directory], environ={})
            self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
