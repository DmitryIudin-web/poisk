from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from teramont_monitor.events import apply_scan
from teramont_monitor.models import Evidence, Event, MonitorState, SourceResult
from teramont_monitor.storage import load_pending, load_state, save_pending, save_state
from teramont_monitor.digest import build_price_digest
from teramont_monitor.profiles import load_target_profile
from teramont_monitor.storage import save_price_digest
from teramont_monitor.telegram import (
    TelegramError,
    format_events,
    format_price_digest,
    send_pending,
    send_price_digest,
)
from tests.test_qualify import matching_listing, matching_range_rover


ROOT = Path(__file__).resolve().parents[1]
TERAMONT_PROFILE = load_target_profile(ROOT / "config/targets/teramont-pro-2026.json")
RANGE_ROVER_PROFILE = load_target_profile(
    ROOT / "config/targets/range-rover-l460-d350-autobiography-2026.json"
)


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
    def test_price_digest_formats_confirmed_and_candidate_details(self) -> None:
        confirmed = replace(
            matching_listing(),
            source="autoru",
            title="Teramont Peak Москва",
            cash_price=5_700_000,
            price_currency="RUB",
            location="Москва",
            mileage_km=25,
            vin="WVGZZZCA1RC000001",
        )
        candidate = replace(
            matching_listing(),
            source="drom",
            title="Teramont candidate",
            cash_price=None,
            advertised_price=5_500_000,
            price_currency="RUB",
            location="Казань",
            dcc=Evidence(None, None),
            image_url="https://cdn.example/teramont.jpg",
        )
        digest = build_price_digest(
            {
                "autoru": SourceResult("autoru", True, (confirmed,)),
                "drom": SourceResult("drom", True, (candidate,)),
            },
            TERAMONT_PROFILE,
            "2026-08-29T09:00:00Z",
        )

        text = format_price_digest(digest)

        self.assertIn("Три минимальные цены", text)
        self.assertIn("5 700 000 ₽", text)
        self.assertIn("Москва", text)
        self.assertIn("25 км", text)
        self.assertIn("WVGZZZCA1RC000001", text)
        self.assertIn("Кандидаты", text)
        self.assertIn("5 500 000 ₽", text)
        self.assertIn("Цена из объявления", text)
        self.assertIn("условия оплаты не подтверждены", text)
        self.assertIn("Комплектация Peak/Summit: подтверждена", text)
        self.assertIn("DCC: не подтверждена", text)
        self.assertIn("Кузов: чёрный", text)
        self.assertIn("Салон: чёрный", text)
        self.assertIn("Статус: требует проверки", text)
        self.assertIn("не подтверждено: DCC, цена за наличные", text)
        self.assertIn("https://auto.drom.ru/moscow/volkswagen/teramont/1.html", text)
        self.assertNotIn("token", text)

    def test_range_rover_vehicle_card_names_target_technical_characteristics(self) -> None:
        listing = replace(
            matching_range_rover(),
            source="drom",
            region="russia",
            source_market="russia",
            location="Москва",
            cash_price=26_900_000,
            advertised_price=26_900_000,
            price_currency="RUB",
        )
        digest = build_price_digest(
            {"drom": SourceResult("drom", True, (listing,))},
            RANGE_ROVER_PROFILE,
            "2026-08-29T09:00:00Z",
        )

        text = format_price_digest(digest)

        self.assertIn("Комплектация Autobiography: подтверждена", text)
        self.assertIn("Двигатель D350: подтверждён", text)
        self.assertIn("Заводские мониторы для пассажиров: подтверждены", text)
        self.assertIn("Год: 2026", text)
        self.assertIn("Пробег: 19 км", text)
        self.assertIn("Цена за наличные: <b>26 900 000 ₽</b>", text)

    def test_send_price_digest_sends_photo_card_with_specs_price_and_link(self) -> None:
        listing = replace(
            matching_listing(),
            title="Volkswagen Teramont Pro Peak 2026",
            cash_price=5_900_000,
            advertised_price=5_900_000,
            price_currency="RUB",
            image_url="https://cdn.example/teramont-front.jpg",
        )
        digest = build_price_digest(
            {"drom": SourceResult("drom", True, (listing,))},
            TERAMONT_PROFILE,
            "2026-08-29T09:00:00Z",
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            save_price_digest(root / "price-digest.json", digest)
            text_calls = []
            photo_calls = []

            delivered = send_price_digest(
                root,
                "bot-token",
                "chat-id",
                transport=lambda token, chat, text: text_calls.append((token, chat, text)),
                photo_transport=lambda token, chat, photo, caption: photo_calls.append(
                    (token, chat, photo, caption)
                ),
            )

        self.assertEqual(delivered, 1)
        self.assertEqual(text_calls, [])
        self.assertEqual(photo_calls[0][2], "https://cdn.example/teramont-front.jpg")
        self.assertIn("Volkswagen Teramont Pro Peak 2026", photo_calls[0][3])
        self.assertIn("Цена за наличные: <b>5 900 000 ₽</b>", photo_calls[0][3])
        self.assertIn("Открыть объявление", photo_calls[0][3])

    def test_send_price_digest_uses_text_card_when_photo_is_missing(self) -> None:
        listing = replace(
            matching_listing(),
            cash_price=5_900_000,
            advertised_price=5_900_000,
            price_currency="RUB",
            image_url=None,
        )
        digest = build_price_digest(
            {"drom": SourceResult("drom", True, (listing,))},
            TERAMONT_PROFILE,
            "2026-08-29T09:00:00Z",
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            save_price_digest(root / "price-digest.json", digest)
            text_calls = []
            photo_calls = []

            delivered = send_price_digest(
                root,
                "bot-token",
                "chat-id",
                transport=lambda token, chat, text: text_calls.append((token, chat, text)),
                photo_transport=lambda token, chat, photo, caption: photo_calls.append(
                    (token, chat, photo, caption)
                ),
            )

        self.assertEqual(delivered, 1)
        self.assertEqual(len(text_calls), 1)
        self.assertEqual(photo_calls, [])
        self.assertIn("Фото: доступно по ссылке на объявление", text_calls[0][2])

    def test_send_price_digest_falls_back_to_text_when_telegram_rejects_photo(self) -> None:
        listing = replace(
            matching_listing(),
            cash_price=5_900_000,
            advertised_price=5_900_000,
            price_currency="RUB",
            image_url="https://cdn.example/blocked-photo.jpg",
        )
        digest = build_price_digest(
            {"drom": SourceResult("drom", True, (listing,))},
            TERAMONT_PROFILE,
            "2026-08-29T09:00:00Z",
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            save_price_digest(root / "price-digest.json", digest)
            text_calls = []

            def reject_photo(_token: str, _chat: str, _photo: str, _caption: str) -> None:
                raise TelegramError("photo rejected")

            delivered = send_price_digest(
                root,
                "bot-token",
                "chat-id",
                transport=lambda token, chat, text: text_calls.append((token, chat, text)),
                photo_transport=reject_photo,
            )

        self.assertEqual(delivered, 1)
        self.assertEqual(len(text_calls), 1)
        self.assertIn("Фото: доступно по ссылке на объявление", text_calls[0][2])

    def test_empty_price_digest_still_reports_no_confirmed_offers(self) -> None:
        digest = build_price_digest(
            {"autoru": SourceResult("autoru", True, ())},
            TERAMONT_PROFILE,
            "2026-08-29T09:00:00Z",
        )

        text = format_price_digest(digest)

        self.assertIn("Подтверждённых предложений нет", text)
        self.assertIn("Кандидатов с ценой из объявления нет", text)

    def test_send_price_digest_delivers_one_report_without_event_state_changes(self) -> None:
        digest = build_price_digest(
            {"autoru": SourceResult("autoru", True, ())},
            TERAMONT_PROFILE,
            "2026-08-29T09:00:00Z",
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            save_price_digest(root / "price-digest.json", digest)
            calls = []

            delivered = send_price_digest(
                root,
                "bot-token",
                "chat-id",
                transport=lambda token, chat, text: calls.append((token, chat, text)),
            )

            self.assertEqual(delivered, 1)
            self.assertEqual(len(calls), 1)
            self.assertFalse((root / "state.json").exists())

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
