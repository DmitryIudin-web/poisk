from __future__ import annotations

import hashlib
import hmac
import json
import os


class AccessAuthenticator:
    """Resolve private access codes to stable user IDs without persisting the codes."""

    def __init__(self, raw_tokens: str | None = None, admin_token: str | None = None):
        raw = os.getenv("APP_USER_TOKENS_JSON", "") if raw_tokens is None else raw_tokens
        self._users = self._parse_users(raw)
        configured_admin = os.getenv("APP_ADMIN_TOKEN", "") if admin_token is None else admin_token
        self._admin_digest = self._digest(configured_admin) if configured_admin else None

    @staticmethod
    def _digest(value: str) -> bytes:
        return hashlib.sha256(value.encode("utf-8")).digest()

    @classmethod
    def _parse_users(cls, raw: str) -> dict[str, bytes]:
        if not raw.strip():
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("APP_USER_TOKENS_JSON must be a JSON object") from exc
        if not isinstance(payload, dict):
            raise ValueError("APP_USER_TOKENS_JSON must be a JSON object")

        result: dict[str, bytes] = {}
        seen_digests: set[bytes] = set()
        for user_id, token in payload.items():
            normalized_id = str(user_id).strip()
            normalized_token = str(token).strip()
            if not normalized_id or not normalized_token:
                raise ValueError("APP_USER_TOKENS_JSON cannot contain blank user IDs or tokens")
            if len(normalized_id) > 80:
                raise ValueError("APP_USER_TOKENS_JSON user IDs must be at most 80 characters")
            digest = cls._digest(normalized_token)
            if digest in seen_digests:
                raise ValueError("APP_USER_TOKENS_JSON access codes must be unique")
            seen_digests.add(digest)
            result[normalized_id] = digest
        return result

    @property
    def configured(self) -> bool:
        return bool(self._users)

    @property
    def admin_configured(self) -> bool:
        return self._admin_digest is not None

    def authenticate(self, token: str | None) -> str | None:
        if not token:
            return None
        supplied = self._digest(token)
        for user_id, expected in self._users.items():
            if hmac.compare_digest(supplied, expected):
                return user_id
        return None

    def authenticate_admin(self, token: str | None) -> bool:
        if not token or self._admin_digest is None:
            return False
        return hmac.compare_digest(self._digest(token), self._admin_digest)


AUTH = AccessAuthenticator()
