"""Telegram Bot API calls. Diagnostics contain only allowlisted classifications."""

from __future__ import annotations

import json
import time
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class TelegramError(RuntimeError):
    pass


class PhotoRejected(TelegramError):
    """Telegram explicitly rejected this photo or caption; text can be sent."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_open = build_opener(_NoRedirect()).open

# Match known reasons, never log arbitrary API/proxy response text.
_REASONS = {
    "chat not found": "chat_not_found",
    "group chat was upgraded": "chat_migrated",
    "bot was blocked": "bot_blocked",
    "bot was kicked": "bot_removed",
    "user is deactivated": "user_deactivated",
    "not enough rights": "insufficient_rights",
    "have no rights": "insufficient_rights",
    "bot is not a member": "bot_not_member",
    "can't parse entities": "invalid_markup",
    "message is too long": "text_too_long",
    "caption is too long": "caption_too_long",
    "failed to get http url content": "invalid_photo",
    "wrong type of the web page content": "invalid_photo",
    "wrong file identifier/http url specified": "invalid_photo",
    "wrong http url": "invalid_photo",
    "photo_invalid_dimensions": "invalid_photo",
    "image_process_failed": "invalid_photo",
    "file is too big": "invalid_photo",
}
_STATUS_REASONS = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden",
    404: "not_found", 429: "rate_limited",
}


def _read_payload(stream) -> dict:
    data = stream.read(65537)
    if len(data) <= 65536:
        try:
            payload = json.loads(data)
        except (ValueError, RecursionError):
            pass
        else:
            if isinstance(payload, dict):
                return payload
    return {}


def _request(method: str, token: str, fields: dict) -> dict:
    endpoint = f"https://api.telegram.org/bot{quote(token, safe=':')}/{method}"
    request = Request(endpoint, data=urlencode(fields).encode("utf-8"))
    retried = False
    while True:
        try:
            try:
                response = _open(request, timeout=20)
            except HTTPError as error:
                response = error
            with response:
                status = response.code
                retry_header = response.headers.get("Retry-After")
                payload = _read_payload(response)
        except (OSError, HTTPException, ValueError):
            raise TelegramError(f"{method} reason=transport_error outcome=unknown") from None

        if status == 200 and payload.get("ok") is True:
            result = payload.get("result")
            if isinstance(result, dict):
                return result
        code = payload.get("error_code")
        if type(code) is not int or not 400 <= code <= 599:
            code = None
        effective = code if status == 200 and code else status
        reason = _STATUS_REASONS.get(effective, "server_error" if effective >= 500 else "unexpected_response")
        description = payload.get("description")
        params = payload.get("parameters")
        params = params if isinstance(params, dict) else {}
        if effective in (400, 403) and isinstance(description, str):
            reason = next((label for part, label in _REASONS.items() if part in description.lower()), reason)
        if effective == 400 and type(params.get("migrate_to_chat_id")) is int:
            reason = "chat_migrated"

        diagnostic = f"{method} HTTP={status} reason={reason}"
        if code:
            diagnostic += f" api_code={code}"
        if effective == 429 and payload.get("ok") is False and code == 429:
            wait = params.get("retry_after")
            if type(wait) is not int and retry_header:
                if len(retry_header) <= 9 and retry_header.isascii() and retry_header.isdecimal():
                    wait = int(retry_header)
            if type(wait) is int and 0 <= wait <= 2**31 - 1:
                diagnostic += f" retry_after={wait}"
                if not retried and wait <= 30:
                    retried = True
                    time.sleep(wait)
                    continue
        if method == "sendPhoto" and effective == 400 and payload.get("ok") is False and reason in {"invalid_photo", "caption_too_long"}:
            raise PhotoRejected(diagnostic)
        raise TelegramError(diagnostic)


def send_message(token: str, chat_id: str, text: str) -> None:
    result = _request("sendMessage", token, {
        "chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true",
    })
    if type(result.get("message_id")) is not int:
        raise TelegramError("sendMessage reason=invalid_acknowledgement outcome=unknown")


def send_photo(token: str, chat_id: str, photo: str, caption: str) -> None:
    result = _request("sendPhoto", token, {
        "chat_id": chat_id, "photo": photo, "caption": caption, "parse_mode": "HTML",
    })
    if type(result.get("message_id")) is not int:
        raise TelegramError("sendPhoto reason=invalid_acknowledgement outcome=unknown")


def check_access(token: str, chat_id: str) -> None:
    """Inspect bot identity, chat access and applicable posting permissions."""
    bot = _request("getMe", token, {})
    if bot.get("is_bot") is not True or type(bot.get("id")) is not int:
        raise TelegramError("getMe reason=invalid_bot_response")
    chat = _request("getChat", token, {"chat_id": chat_id})
    kind = chat.get("type")
    if type(chat.get("id")) is not int or kind not in ("private", "group", "supergroup", "channel"):
        raise TelegramError("getChat reason=invalid_chat_response")
    if kind == "private":
        return
    member = _request("getChatMember", token, {"chat_id": chat_id, "user_id": bot["id"]})
    role = member.get("status")
    if role == "creator":
        return
    if role == "administrator":
        allowed = kind != "channel" or member.get("can_post_messages") is True
    elif role == "restricted":
        allowed = kind != "channel" and member.get("is_member") is True and member.get("can_send_messages") is True
    else:
        permissions = chat.get("permissions") or {}
        allowed = role == "member" and kind != "channel" and isinstance(permissions, dict) and permissions.get("can_send_messages") is True
    if not allowed:
        raise TelegramError("getChatMember reason=posting_permission_missing_or_unconfirmed")
