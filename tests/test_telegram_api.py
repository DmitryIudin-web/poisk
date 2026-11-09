from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from teramont_monitor import telegram_api as api
from teramont_monitor.cli import main
from teramont_monitor.models import MonitorState, PriceDigest, RankedOffer
from teramont_monitor.storage import load_pending, load_state, save_pending, save_price_digest, save_state
from teramont_monitor.telegram import send_pending, send_price_digest
from tests.test_telegram import sample_event

TOKEN, CHAT = "fake-token", "fake-destination"
SENSITIVE = f"https://api.telegram.org/bot{TOKEN}/test {CHAT} untrusted-response"
ENV = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": CHAT}


def ok(result):
    response = io.BytesIO(json.dumps({"ok": True, "result": result}).encode())
    response.code = 200
    response.headers = {}
    return response


def rejection(status, description, *, parameters=None, headers=None):
    body = {"ok": False, "error_code": status, "description": f"{description} {SENSITIVE}"}
    if parameters:
        body["parameters"] = parameters
    return HTTPError(SENSITIVE, status, SENSITIVE, headers or {}, io.BytesIO(json.dumps(body).encode()))


def cards():
    listing = replace(sample_event().listing, image_url="https://example.org/card.jpg")
    offers = tuple(RankedOffer(replace(listing, title=f"Car {i}"), "candidate") for i in (1, 2))
    return PriceDigest("teramont-pro-2026", "Teramont", "2026-11-09T00:54:27Z", 4, 1, candidates=offers)


class TelegramClientTests(unittest.TestCase):
    def setUp(self):
        self.http = self.enterContext(patch.object(api, "_open"))
        self.sleep = self.enterContext(patch.object(api.time, "sleep"))

    def test_known_rejections_and_proxy_errors_are_classified_and_redacted(self):
        for status, description, reason in (
            (400, "Bad Request: chat not found", "chat_not_found"),
            (400, "Bad Request: can't parse entities", "invalid_markup"),
            (400, "Bad Request: something unexpected", "bad_request"),
            (401, "Unauthorized", "unauthorized"),
            (403, "Forbidden: bot was blocked by the user", "bot_blocked"),
            (404, "Not Found", "not_found"),
            (502, "Bad gateway", "server_error"),
        ):
            with self.subTest(status=status, reason=reason):
                self.http.side_effect = rejection(status, description)
                with self.assertRaises(api.TelegramError) as caught:
                    api.send_message(TOKEN, CHAT, "test")
                self.assertEqual(str(caught.exception), f"sendMessage HTTP={status} reason={reason} api_code={status}")

    def test_migration_withholds_new_destination(self):
        self.http.side_effect = rejection(400, "moved", parameters={"migrate_to_chat_id": -1234567890})
        with self.assertRaises(api.TelegramError) as caught:
            api.send_message(TOKEN, CHAT, "test")
        self.assertEqual(str(caught.exception), "sendMessage HTTP=400 reason=chat_migrated api_code=400")
        self.http.assert_called_once()

    def test_explicit_rate_limit_retries_once_after_requested_delay(self):
        for params, headers in (({"retry_after": 3}, {}), ({}, {"Retry-After": "3"})):
            with self.subTest(params=params):
                self.http.reset_mock()
                self.sleep.reset_mock()
                self.http.side_effect = [rejection(429, "slow down", parameters=params, headers=headers),
                                         ok({"message_id": 101})]
                api.send_message(TOKEN, CHAT, "test")
                self.assertEqual(self.http.call_count, 2)
                self.sleep.assert_called_once_with(3)

    def test_repeated_or_long_rate_limits_are_deferred(self):
        self.http.side_effect = [rejection(429, "slow", parameters={"retry_after": 2}),
                                 rejection(429, "slow", parameters={"retry_after": 4})]
        with self.assertRaisesRegex(api.TelegramError, "retry_after=4"):
            api.send_message(TOKEN, CHAT, "test")
        self.sleep.assert_called_once_with(2)
        self.http.reset_mock()
        self.sleep.reset_mock()
        self.http.side_effect = rejection(429, "slow", parameters={"retry_after": 61})
        with self.assertRaisesRegex(api.TelegramError, "retry_after=61"):
            api.send_message(TOKEN, CHAT, "test")
        self.http.assert_called_once()
        self.sleep.assert_not_called()

    def test_non_json_http_error_preserves_http_status(self):
        self.http.side_effect = HTTPError(SENSITIVE, 503, SENSITIVE, {}, io.BytesIO(b"<html>untrusted</html>"))
        with self.assertRaisesRegex(api.TelegramError, "^sendMessage HTTP=503 reason=server_error$"):
            api.send_message(TOKEN, CHAT, "test")

    def test_redirect_never_replays_credentials(self):
        handler = api._NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 307, "", {}, "https://example.invalid"))
        self.http.side_effect = HTTPError(SENSITIVE, 307, SENSITIVE, {}, io.BytesIO(b""))
        with self.assertRaisesRegex(api.TelegramError, "^sendMessage HTTP=307 reason=unexpected_response$"):
            api.send_message(TOKEN, CHAT, "test")
        self.http.assert_called_once()

    def test_unsuccessful_json_under_http_200_does_not_acknowledge(self):
        response = ok({})
        response.seek(0)
        response.truncate()
        response.write(json.dumps({"ok": False, "error_code": 401, "description": SENSITIVE}).encode())
        response.seek(0)
        self.http.return_value = response
        with self.assertRaisesRegex(api.TelegramError, "^sendMessage HTTP=200 reason=unauthorized api_code=401$"):
            api.send_message(TOKEN, CHAT, "test")

    def test_missing_acknowledgement_does_not_mark_an_event_or_leak_response(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            event = sample_event()
            save_pending(root / "pending-events.json", [event])
            for message_id in (None, 0, True):
                self.http.return_value = ok({"message_id": message_id, "untrusted": SENSITIVE})
                with self.assertRaisesRegex(api.TelegramError, "^sendMessage reason=invalid_acknowledgement outcome=unknown$"):
                    send_pending(root, TOKEN, CHAT)
            self.assertEqual(load_pending(root / "pending-events.json"), [event])
            self.assertFalse(load_state(root / "state.json").emitted_event_ids)

    def test_cli_check_only_reads_and_reports_no_identifiers(self):
        self.http.side_effect = [ok({"id": 42, "is_bot": True}), ok({"id": 84, "type": "channel"}),
                                 ok({"status": "administrator", "can_post_messages": True})]
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            result = main(["check-telegram"], environ=ENV)
        self.assertEqual(result, 0)
        methods = [c.args[0].full_url.rsplit("/", 1)[1] for c in self.http.call_args_list]
        self.assertEqual(methods, ["getMe", "getChat", "getChatMember"])
        self.assertEqual(out.getvalue().strip(), "Telegram bot and chat access check passed")

    def test_preflight_stops_at_rejected_token(self):
        self.http.side_effect = rejection(401, "Unauthorized")
        out = io.StringIO()
        with redirect_stderr(out):
            self.assertEqual(main(["check-telegram"], environ=ENV), 2)
        self.http.assert_called_once()
        self.assertEqual(out.getvalue().strip(), "getMe HTTP=401 reason=unauthorized api_code=401")

    def test_private_chat_and_inadequate_channel_rights(self):
        bot = {"is_bot": True, "id": 42}
        self.http.side_effect = [ok(bot), ok({"id": 84, "type": "private"})]
        api.check_access(TOKEN, CHAT)
        self.assertEqual(self.http.call_count, 2)
        self.http.side_effect = [ok(bot), ok({"id": 84, "type": "channel"}),
                                 ok({"status": "administrator", "can_post_messages": False})]
        with self.assertRaisesRegex(api.TelegramError, "posting_permission_missing"):
            api.check_access(TOKEN, CHAT)

    def test_photo_rejection_allows_fallback_only_for_media(self):
        for failure in (rejection(403, "bot was blocked"), URLError(SENSITIVE), rejection(500, "failed")):
            with self.subTest(failure=type(failure)), TemporaryDirectory() as directory:
                root = Path(directory)
                save_price_digest(root / "price-digest.json", cards())
                self.http.reset_mock()
                self.http.side_effect = failure
                with self.assertRaises(api.TelegramError) as caught:
                    send_price_digest(root, TOKEN, CHAT)
                self.assertFalse(isinstance(caught.exception, api.PhotoRejected))
                self.assertNotIn(TOKEN, str(caught.exception))
                self.assertNotIn(CHAT, str(caught.exception))
                self.http.assert_called_once()
                self.assertFalse((root / "digest-delivery.json").exists())

        self.http.side_effect = rejection(400, "Bad Request: failed to get HTTP URL content")
        with self.assertRaisesRegex(api.PhotoRejected, "reason=invalid_photo"):
            api.send_photo(TOKEN, CHAT, "https://example.org/photo", "caption")

    def test_event_restart_preserves_legacy_ids_and_partial_acknowledgement(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = sample_event()
            second = replace(first, id="second")
            save_state(root / "state.json", MonitorState(emitted_event_ids={"legacy"}))
            save_pending(root / "pending-events.json", [replace(first, id="legacy"), first, second])
            self.http.side_effect = [ok({"message_id": 100}), rejection(403, "forbidden")]
            with self.assertRaises(api.TelegramError):
                send_pending(root, TOKEN, CHAT)
            self.assertEqual(load_state(root / "state.json").emitted_event_ids, {"legacy", first.id})
            self.assertEqual(load_pending(root / "pending-events.json"), [second])
            self.http.side_effect = [ok({"message_id": 101})]
            self.assertEqual(send_pending(root, TOKEN, CHAT), 1)
            self.assertEqual(send_pending(root, TOKEN, CHAT), 0)
            self.assertEqual(self.http.call_count, 3)

    def test_partial_digest_resumes_and_new_scan_gets_its_own_delivery(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            digest = cards()
            save_price_digest(root / "price-digest.json", digest)
            self.http.side_effect = [ok({"message_id": 11}), rejection(401, "Unauthorized")]
            with self.assertRaises(api.TelegramError):
                send_price_digest(root, TOKEN, CHAT)
            self.http.side_effect = [ok({"message_id": 12})]
            self.assertEqual(send_price_digest(root, TOKEN, CHAT), 1)
            self.assertIn(b"Car+2", self.http.call_args.args[0].data)
            self.assertEqual(send_price_digest(root, TOKEN, CHAT), 0)
            self.assertEqual(self.http.call_count, 3)
            save_price_digest(root / "price-digest.json", replace(digest, observed_at="2026-11-09T01:54:27Z"))
            self.http.side_effect = [ok({"message_id": 13}), ok({"message_id": 14})]
            self.assertEqual(send_price_digest(root, TOKEN, CHAT), 2)
            self.assertFalse((root / "state.json").exists())

    def test_empty_digest_is_acknowledged_only_on_success(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            save_price_digest(root / "price-digest.json", replace(cards(), candidates=()))
            transport = Mock(side_effect=[api.TelegramError("fail"), None])
            with self.assertRaises(api.TelegramError):
                send_price_digest(root, TOKEN, CHAT, transport=transport)
            self.assertEqual(send_price_digest(root, TOKEN, CHAT, transport=transport), 1)
            self.assertEqual(send_price_digest(root, TOKEN, CHAT, transport=transport), 0)
            self.assertEqual(transport.call_count, 2)
