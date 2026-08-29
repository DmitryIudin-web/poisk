from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from teramont_monitor.events import apply_scan
from teramont_monitor.models import Event, MonitorState, SourceResult
from teramont_monitor.storage import load_pending, load_state, save_pending, save_state
from teramont_monitor.telegram import TelegramError, format_events, send_pending
from tests.test_qualify import matching_listing, matching_range_rover


def sample_event(kind: str = "price_drop") -> Event:
    listing = replace(
        matching_listing(),
        title="Volkswagen <Teramont> & Peak",
        cash_price=5_950_000,
        price_currency="RUB",
        region="russia",
    )
    return Event(
        id="abc123",
        kind=kind,
        listing_key="drom:1",
        occurred_at="2026-08-28T12:00:00Z",
        listing=listing,
        detail={"old_price": 6_000_000, "new_price": 5_950_000, "drop": 50_000},
    )


class TelegramTests(unittest.TestCase):
    def test_range_rover_eur_price_drop_displays_target_market_currency_and_evidence(self) -> None:
        listing = replace(
            matching_range_rover(),
            cash_price=99_000,
            price_currency="EUR",
            region="europe",
        )
        event = Event(
            id="rr-eur-drop-1000",
            kind="price_drop",
            listing_key="range-rover-l460-d350-autobiography-2026:autoscout24:1",
            occurred_at="2026-08-28T12:00:00Z",
            listing=listing,
            detail={"old_price": 100_000, "new_price": 99_000, "drop": 1_000, "currency": "EUR"},
        )

        text = format_events([event])

        self.assertIn("Range Rover L460 D350 Autobiography 2026", text)
        self.assertIn("Европа", text)
        self.assertIn("1 000 €", text)
        self.assertIn("Factory RSE: confirmed", text)
        self.assertIn("Left-hand drive: confirmed", text)
        self.assertIn("event: rr-eur-drop-1000", text)
        self.assertNotIn("bot-token", text)
        self.assertNotIn("chat-id", text)
        self.assertNotIn("@", text)
        self.assertNotIn("+7", text)

    def test_formatter_contains_event_id_escapes_html_and_no_credentials(self) -> None:
        text = format_events([sample_event()])
        self.assertIn("Снижение цены", text)
        self.assertIn("event: abc123", text)
        self.assertIn("&lt;Teramont&gt; &amp; Peak", text)
        self.assertNotIn("bot-token", text)

    def test_formatter_rejects_unapproved_event_kind(self) -> None:
        with self.assertRaises(ValueError):
            format_events([sample_event("debug")])

    def test_successful_delivery_removes_pending_and_marks_emitted(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            event = sample_event()
            save_state(root / "state.json", MonitorState())
            save_pending(root / "pending-events.json", [event])
            calls = []

            delivered = send_pending(
                root,
                "bot-token",
                "chat-id",
                transport=lambda token, chat, text: calls.append((token, chat, text)),
            )

            self.assertEqual(delivered, 1)
            self.assertEqual(load_pending(root / "pending-events.json"), [])
            self.assertIn(event.id, load_state(root / "state.json").emitted_event_ids)
            self.assertEqual(calls[0][:2], ("bot-token", "chat-id"))

    def test_failed_delivery_keeps_pending(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            event = sample_event()
            save_state(root / "state.json", MonitorState())
            save_pending(root / "pending-events.json", [event])

            def fail(_token: str, _chat: str, _text: str) -> None:
                raise TelegramError("unavailable")

            with self.assertRaises(TelegramError):
                send_pending(root, "bot-token", "chat-id", transport=fail)

            self.assertEqual(load_pending(root / "pending-events.json"), [event])


if __name__ == "__main__":
    unittest.main()
