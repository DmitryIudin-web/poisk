from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .schema import Listing, SearchProfile

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS searches (
  id TEXT PRIMARY KEY,
  owner_token TEXT NOT NULL,
  profile_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  next_run_at TEXT,
  last_run_at TEXT,
  telegram_chat_id TEXT,
  telegram_bind_code TEXT,
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS listings (
  search_id TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  last_notified_price REAL,
  PRIMARY KEY(search_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_searches_due ON searches(enabled, next_run_at);
"""


def _effective_price(payload: dict) -> float | None:
    for key in ("export_price", "net_price", "price"):
        value = payload.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


class Store:
    def __init__(self, path: str = "data/searches.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def create_search(self, profile: SearchProfile) -> tuple[str, str, str]:
        search_id = secrets.token_urlsafe(9)
        owner_token = secrets.token_urlsafe(24)
        bind_code = secrets.token_hex(3).upper()
        now = datetime.now(timezone.utc).isoformat()
        with self.conn() as db:
            db.execute(
                "INSERT INTO searches(id, owner_token, profile_json, created_at, updated_at, next_run_at, telegram_bind_code, enabled) VALUES(?,?,?,?,?,?,?,1)",
                (search_id, owner_token, json.dumps(profile.to_dict(), ensure_ascii=False), now, now, now, bind_code),
            )
        return search_id, owner_token, bind_code

    def get_search(self, search_id: str) -> sqlite3.Row | None:
        with self.conn() as db:
            return db.execute("SELECT * FROM searches WHERE id=?", (search_id,)).fetchone()

    def authorize(self, search_id: str, token: str) -> bool:
        row = self.get_search(search_id)
        return bool(row and secrets.compare_digest(row["owner_token"], token))

    def due_searches(self, limit: int = 20) -> list[sqlite3.Row]:
        now = datetime.now(timezone.utc).isoformat()
        with self.conn() as db:
            return list(db.execute("SELECT * FROM searches WHERE enabled=1 AND (next_run_at IS NULL OR next_run_at<=?) ORDER BY next_run_at LIMIT ?", (now, limit)))

    def mark_run(self, search_id: str, interval_minutes: int) -> None:
        now = datetime.now(timezone.utc)
        next_run = now + timedelta(minutes=interval_minutes)
        with self.conn() as db:
            db.execute("UPDATE searches SET last_run_at=?, next_run_at=?, updated_at=? WHERE id=?", (now.isoformat(), next_run.isoformat(), now.isoformat(), search_id))

    def save_listing(self, search_id: str, listing: Listing) -> tuple[bool, float | None]:
        now = datetime.now(timezone.utc).isoformat()
        payload_dict = listing.to_dict()
        payload = json.dumps(payload_dict, ensure_ascii=False)
        with self.conn() as db:
            old = db.execute("SELECT payload_json FROM listings WHERE search_id=? AND fingerprint=?", (search_id, listing.fingerprint)).fetchone()
            if old:
                old_price = _effective_price(json.loads(old["payload_json"]))
                db.execute("UPDATE listings SET payload_json=?, last_seen_at=? WHERE search_id=? AND fingerprint=?", (payload, now, search_id, listing.fingerprint))
                return False, old_price
            db.execute("INSERT INTO listings(search_id, fingerprint, payload_json, first_seen_at, last_seen_at) VALUES(?,?,?,?,?)", (search_id, listing.fingerprint, payload, now, now))
            return True, None

    def list_results(self, search_id: str, limit: int = 100) -> list[dict]:
        with self.conn() as db:
            rows = db.execute("SELECT payload_json, first_seen_at, last_seen_at FROM listings WHERE search_id=? ORDER BY last_seen_at DESC LIMIT ?", (search_id, limit)).fetchall()
        result = []
        for row in rows:
            item = json.loads(row["payload_json"])
            item["first_seen_at"] = row["first_seen_at"]
            item["last_seen_at"] = row["last_seen_at"]
            result.append(item)
        return result

    def bind_telegram(self, code: str, chat_id: str) -> str | None:
        with self.conn() as db:
            row = db.execute("SELECT id FROM searches WHERE telegram_bind_code=?", (code.upper(),)).fetchone()
            if not row:
                return None
            db.execute("UPDATE searches SET telegram_chat_id=? WHERE id=?", (str(chat_id), row["id"]))
            return str(row["id"])
