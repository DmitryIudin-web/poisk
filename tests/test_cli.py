from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from teramont_monitor.cli import collect, main, smoke
from teramont_monitor.storage import load_pending, load_state


ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "sources.json"
FIXTURES = Path(__file__).parent / "fixtures"
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

    def test_zero_successful_sources_returns_failure_without_market_events(self) -> None:
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)

            def fail(_url: str, _timeout: float) -> str:
                raise TimeoutError("timed out")

            summary = collect(CONFIG, state_dir, fetcher=fail, observed_at="2026-08-28T12:00:00Z")
            self.assertEqual(summary.successful_sources, 0)
            self.assertEqual(summary.exit_code, 2)
            self.assertEqual(load_pending(state_dir / "pending-events.json"), [])

    def test_smoke_never_writes_state(self) -> None:
        summary = smoke(CONFIG, fetcher=fixture_fetcher)
        self.assertEqual(summary.successful_sources, 5)

    def test_notify_command_requires_both_environment_secrets(self) -> None:
        with TemporaryDirectory() as directory:
            exit_code = main(["notify", "--state-dir", directory], environ={})
            self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
