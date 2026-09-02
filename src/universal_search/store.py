from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .schema import Listing, SearchProfile

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS searches (
  id TEXT PRIMARY KEY,
  owner_token TEXT NOT NULL,
  owner_id TEXT NOT NULL DEFAULT 'legacy',
  profile_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  next_run_at TEXT,
  last_run_at TEXT,
  telegram_chat_id TEXT,
  telegram_bind_code TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  expires_at TEXT,
  run_lock_until TEXT
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
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  search_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  found INTEGER,
  new_relevant INTEGER,
  price_drops INTEGER,
  vision_candidates INTEGER,
  error_type TEXT
);
CREATE TABLE IF NOT EXISTS api_usage (
  id TEXT PRIMARY KEY,
  search_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  model TEXT NOT NULL,
  status TEXT NOT NULL,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cached_tokens INTEGER,
  images INTEGER NOT NULL,
  reserved_tokens INTEGER NOT NULL,
  reserved_cost_usd REAL NOT NULL,
  estimated_cost_usd REAL,
  created_at TEXT NOT NULL,
  finalized_at TEXT,
  error_type TEXT
);
CREATE TABLE IF NOT EXISTS vision_checks (
  search_id TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  signature TEXT NOT NULL,
  status TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  PRIMARY KEY(search_id, fingerprint, signature)
);
CREATE INDEX IF NOT EXISTS idx_api_usage_created ON api_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_api_usage_run ON api_usage(search_id, run_id);
"""


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _effective_price(payload: dict) -> float | None:
    for key in ("export_price", "net_price", "price"):
        value = payload.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


class ActiveSearchLimitReached(RuntimeError):
    pass


class Store:
    def __init__(self, path: str = "data/searches.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.default_ttl_hours = _env_int("SEARCH_TTL_HOURS", 24, 1, 24 * 30)
        self.min_interval_minutes = _env_int("MIN_SEARCH_INTERVAL_MINUTES", 60, 15, 24 * 60)
        self.run_lock_minutes = _env_int("SEARCH_RUN_LOCK_MINUTES", 60, 1, 120)
        with self.conn() as db:
            db.executescript(SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            self._migrate(db)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _ensure_column(db: sqlite3.Connection, table: str, name: str, definition: str) -> None:
        columns = {str(row["name"]) for row in db.execute(f"PRAGMA table_info({table})")}
        if name not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _migrate(self, db: sqlite3.Connection) -> None:
        self._ensure_column(db, "searches", "owner_id", "TEXT NOT NULL DEFAULT 'legacy'")
        self._ensure_column(db, "searches", "expires_at", "TEXT")
        self._ensure_column(db, "searches", "run_lock_until", "TEXT")
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=self.default_ttl_hours)).isoformat()
        db.execute("UPDATE searches SET owner_id='legacy' WHERE owner_id IS NULL OR owner_id='' ")
        db.execute("UPDATE searches SET expires_at=? WHERE expires_at IS NULL", (expires_at,))
        db.execute("DROP INDEX IF EXISTS idx_searches_due")
        db.execute("CREATE INDEX idx_searches_due ON searches(enabled, expires_at, next_run_at)")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_searches_owner ON searches(owner_id, enabled, expires_at)"
        )

    def create_search(
        self,
        profile: SearchProfile,
        owner_id: str = "legacy",
        ttl_hours: int | None = None,
        max_active: int | None = None,
    ) -> tuple[str, str, str]:
        search_id = secrets.token_urlsafe(9)
        owner_token = secrets.token_urlsafe(24)
        bind_code = secrets.token_hex(3).upper()
        now_dt = datetime.now(timezone.utc)
        ttl = self.default_ttl_hours if ttl_hours is None else max(1, min(24 * 30, int(ttl_hours)))
        expires_at = (now_dt + timedelta(hours=ttl)).isoformat()
        now = now_dt.isoformat()
        with self.conn() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE searches SET enabled=0, updated_at=? WHERE enabled=1 AND expires_at IS NOT NULL AND expires_at<=?",
                (now, now),
            )
            if max_active is not None:
                active = db.execute(
                    """SELECT COUNT(*) AS count FROM searches
                       WHERE owner_id=? AND enabled=1 AND (expires_at IS NULL OR expires_at>?)""",
                    (owner_id, now),
                ).fetchone()
                if int(active["count"]) >= max(1, int(max_active)):
                    raise ActiveSearchLimitReached(owner_id)
            db.execute(
                """INSERT INTO searches(
                       id, owner_token, owner_id, profile_json, created_at, updated_at,
                       next_run_at, telegram_bind_code, enabled, expires_at
                   ) VALUES(?,?,?,?,?,?,?,?,1,?)""",
                (
                    search_id,
                    owner_token,
                    owner_id,
                    json.dumps(profile.to_dict(), ensure_ascii=False),
                    now,
                    now,
                    now,
                    bind_code,
                    expires_at,
                ),
            )
        return search_id, owner_token, bind_code

    def get_search(self, search_id: str) -> sqlite3.Row | None:
        with self.conn() as db:
            return db.execute("SELECT * FROM searches WHERE id=?", (search_id,)).fetchone()

    def authorize(self, search_id: str, token: str) -> bool:
        row = self.get_search(search_id)
        return bool(row and secrets.compare_digest(row["owner_token"], token))

    def expire_searches(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.conn() as db:
            cursor = db.execute(
                "UPDATE searches SET enabled=0, updated_at=? WHERE enabled=1 AND expires_at IS NOT NULL AND expires_at<=?",
                (now, now),
            )
            return int(cursor.rowcount)

    def active_search_count(self, owner_id: str) -> int:
        self.expire_searches()
        now = datetime.now(timezone.utc).isoformat()
        with self.conn() as db:
            row = db.execute(
                """SELECT COUNT(*) AS count FROM searches
                   WHERE owner_id=? AND enabled=1 AND (expires_at IS NULL OR expires_at>?)""",
                (owner_id, now),
            ).fetchone()
            return int(row["count"])

    def disable_search(self, search_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.conn() as db:
            db.execute(
                "UPDATE searches SET enabled=0, run_lock_until=NULL, updated_at=? WHERE id=?",
                (now, search_id),
            )

    def due_searches(self, limit: int = 20) -> list[sqlite3.Row]:
        self.expire_searches()
        now = datetime.now(timezone.utc).isoformat()
        with self.conn() as db:
            return list(
                db.execute(
                    """SELECT * FROM searches
                       WHERE enabled=1
                         AND (expires_at IS NULL OR expires_at>?)
                         AND (next_run_at IS NULL OR next_run_at<=?)
                         AND (run_lock_until IS NULL OR run_lock_until<=?)
                       ORDER BY next_run_at LIMIT ?""",
                    (now, now, now, limit),
                )
            )

    def start_run(self, search_id: str) -> tuple[str | None, str | None]:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lock_until = (now_dt + timedelta(minutes=self.run_lock_minutes)).isoformat()
        with self.conn() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM searches WHERE id=?", (search_id,)).fetchone()
            if not row:
                return None, "search not found"
            if not row["enabled"]:
                return None, "search is disabled"
            if row["expires_at"] and row["expires_at"] <= now:
                db.execute("UPDATE searches SET enabled=0, updated_at=? WHERE id=?", (now, search_id))
                return None, "search expired"
            if row["next_run_at"] and row["next_run_at"] > now:
                return None, "search cooldown is active"
            if row["run_lock_until"] and row["run_lock_until"] > now:
                return None, "search is already running"

            run_id = secrets.token_urlsafe(12)
            db.execute(
                "UPDATE searches SET run_lock_until=?, updated_at=? WHERE id=?",
                (lock_until, now, search_id),
            )
            db.execute(
                "INSERT INTO runs(id, search_id, started_at, status) VALUES(?,?,?,'running')",
                (run_id, search_id, now),
            )
            return run_id, None

    def finish_run(
        self,
        search_id: str,
        run_id: str,
        interval_minutes: int,
        *,
        status: str,
        found: int | None = None,
        new_relevant: int | None = None,
        price_drops: int | None = None,
        vision_candidates: int | None = None,
        error_type: str | None = None,
    ) -> None:
        now_dt = datetime.now(timezone.utc)
        interval = max(self.min_interval_minutes, int(interval_minutes))
        next_run = now_dt + timedelta(minutes=interval)
        now = now_dt.isoformat()
        with self.conn() as db:
            db.execute(
                """UPDATE runs SET finished_at=?, status=?, found=?, new_relevant=?,
                       price_drops=?, vision_candidates=?, error_type=? WHERE id=? AND search_id=?""",
                (
                    now,
                    status,
                    found,
                    new_relevant,
                    price_drops,
                    vision_candidates,
                    (error_type or "")[:120] or None,
                    run_id,
                    search_id,
                ),
            )
            db.execute(
                """UPDATE searches SET last_run_at=?, next_run_at=?, run_lock_until=NULL,
                       updated_at=? WHERE id=?""",
                (now, next_run.isoformat(), now, search_id),
            )

    def mark_run(self, search_id: str, interval_minutes: int) -> None:
        now_dt = datetime.now(timezone.utc)
        interval = max(self.min_interval_minutes, int(interval_minutes))
        next_run = now_dt + timedelta(minutes=interval)
        with self.conn() as db:
            db.execute(
                """UPDATE searches SET last_run_at=?, next_run_at=?, run_lock_until=NULL,
                       updated_at=? WHERE id=?""",
                (now_dt.isoformat(), next_run.isoformat(), now_dt.isoformat(), search_id),
            )

    def has_listing(self, search_id: str, fingerprint: str) -> bool:
        with self.conn() as db:
            row = db.execute(
                "SELECT 1 FROM listings WHERE search_id=? AND fingerprint=?",
                (search_id, fingerprint),
            ).fetchone()
            return row is not None

    def save_listing(self, search_id: str, listing: Listing) -> tuple[bool, float | None]:
        now = datetime.now(timezone.utc).isoformat()
        payload_dict = listing.to_dict()
        payload = json.dumps(payload_dict, ensure_ascii=False)
        with self.conn() as db:
            old = db.execute(
                "SELECT payload_json FROM listings WHERE search_id=? AND fingerprint=?",
                (search_id, listing.fingerprint),
            ).fetchone()
            if old:
                old_price = _effective_price(json.loads(old["payload_json"]))
                db.execute(
                    "UPDATE listings SET payload_json=?, last_seen_at=? WHERE search_id=? AND fingerprint=?",
                    (payload, now, search_id, listing.fingerprint),
                )
                return False, old_price
            db.execute(
                """INSERT INTO listings(search_id, fingerprint, payload_json, first_seen_at, last_seen_at)
                   VALUES(?,?,?,?,?)""",
                (search_id, listing.fingerprint, payload, now, now),
            )
            return True, None

    def list_results(self, search_id: str, limit: int = 100) -> list[dict]:
        with self.conn() as db:
            rows = db.execute(
                """SELECT payload_json, first_seen_at, last_seen_at FROM listings
                   WHERE search_id=? ORDER BY last_seen_at DESC LIMIT ?""",
                (search_id, limit),
            ).fetchall()
        result = []
        for row in rows:
            item = json.loads(row["payload_json"])
            item["first_seen_at"] = row["first_seen_at"]
            item["last_seen_at"] = row["last_seen_at"]
            result.append(item)
        return result

    def vision_check_due(
        self,
        search_id: str,
        fingerprint: str,
        signature: str,
        retry_after_hours: int = 24,
    ) -> bool:
        with self.conn() as db:
            row = db.execute(
                """SELECT status, checked_at FROM vision_checks
                   WHERE search_id=? AND fingerprint=? AND signature=?""",
                (search_id, fingerprint, signature),
            ).fetchone()
        if not row:
            return True
        if row["status"] == "succeeded":
            return False
        try:
            checked_at = datetime.fromisoformat(row["checked_at"])
        except (TypeError, ValueError):
            return True
        return checked_at <= datetime.now(timezone.utc) - timedelta(hours=max(1, retry_after_hours))

    def record_vision_check(self, search_id: str, fingerprint: str, signature: str, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.conn() as db:
            db.execute(
                """INSERT INTO vision_checks(search_id, fingerprint, signature, status, checked_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(search_id, fingerprint, signature)
                   DO UPDATE SET status=excluded.status, checked_at=excluded.checked_at""",
                (search_id, fingerprint, signature, status, now),
            )

    @staticmethod
    def _day_bounds(day: date | None = None) -> tuple[str, str]:
        selected = day or datetime.now(timezone.utc).date()
        start = datetime.combine(selected, time.min, tzinfo=timezone.utc)
        return start.isoformat(), (start + timedelta(days=1)).isoformat()

    def _daily_usage_row(self, db: sqlite3.Connection, day: date | None = None) -> sqlite3.Row:
        start, end = self._day_bounds(day)
        return db.execute(
            """SELECT
                   COUNT(*) AS api_calls,
                   COALESCE(SUM(COALESCE(input_tokens, 0)), 0) AS input_tokens,
                   COALESCE(SUM(COALESCE(output_tokens, 0)), 0) AS output_tokens,
                   COALESCE(SUM(COALESCE(cached_tokens, 0)), 0) AS cached_tokens,
                   COALESCE(SUM(images), 0) AS images,
                   COALESCE(SUM(
                     CASE WHEN input_tokens IS NULL OR output_tokens IS NULL
                          THEN reserved_tokens ELSE input_tokens + output_tokens END
                   ), 0) AS budget_tokens,
                   COALESCE(SUM(COALESCE(estimated_cost_usd, reserved_cost_usd)), 0) AS estimated_cost_usd
               FROM api_usage WHERE created_at>=? AND created_at<?""",
            (start, end),
        ).fetchone()

    def reserve_api_call(
        self,
        *,
        search_id: str,
        run_id: str,
        model: str,
        images: int,
        reserved_tokens: int,
        reserved_cost_usd: float,
        daily_token_limit: int,
        daily_cost_limit_usd: float,
    ) -> tuple[str | None, dict]:
        now = datetime.now(timezone.utc).isoformat()
        with self.conn() as db:
            db.execute("BEGIN IMMEDIATE")
            current = dict(self._daily_usage_row(db))
            next_tokens = int(current["budget_tokens"]) + int(reserved_tokens)
            next_cost = float(current["estimated_cost_usd"]) + float(reserved_cost_usd)
            if daily_token_limit > 0 and next_tokens > daily_token_limit:
                return None, current
            if daily_cost_limit_usd > 0 and next_cost > daily_cost_limit_usd:
                return None, current

            usage_id = secrets.token_urlsafe(12)
            db.execute(
                """INSERT INTO api_usage(
                       id, search_id, run_id, model, status, images, reserved_tokens,
                       reserved_cost_usd, created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    usage_id,
                    search_id,
                    run_id,
                    model,
                    "reserved",
                    max(0, int(images)),
                    max(0, int(reserved_tokens)),
                    max(0.0, float(reserved_cost_usd)),
                    now,
                ),
            )
            current["budget_tokens"] = next_tokens
            current["estimated_cost_usd"] = next_cost
            return usage_id, current

    def finalize_api_call(
        self,
        usage_id: str,
        *,
        status: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
        error_type: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.conn() as db:
            db.execute(
                """UPDATE api_usage SET status=?, input_tokens=?, output_tokens=?, cached_tokens=?,
                       estimated_cost_usd=?, finalized_at=?, error_type=? WHERE id=?""",
                (
                    status,
                    input_tokens,
                    output_tokens,
                    cached_tokens,
                    estimated_cost_usd,
                    now,
                    (error_type or "")[:120] or None,
                    usage_id,
                ),
            )

    def usage_report(self, day: date | None = None, limit: int = 100) -> dict:
        start, end = self._day_bounds(day)
        with self.conn() as db:
            totals = dict(self._daily_usage_row(db, day))
            rows = db.execute(
                """SELECT id, search_id, run_id, model, 1 AS api_calls, status, input_tokens, output_tokens,
                          cached_tokens, images,
                          COALESCE(estimated_cost_usd, reserved_cost_usd) AS estimated_cost_usd,
                          created_at, finalized_at, error_type
                   FROM api_usage WHERE created_at>=? AND created_at<?
                   ORDER BY created_at DESC LIMIT ?""",
                (start, end, max(1, min(500, int(limit)))),
            ).fetchall()
        totals["estimated_cost_usd"] = round(float(totals["estimated_cost_usd"]), 8)
        return {"day_utc": start[:10], "totals": totals, "events": [dict(row) for row in rows]}

    def bind_telegram(self, code: str, chat_id: str) -> str | None:
        now = datetime.now(timezone.utc).isoformat()
        with self.conn() as db:
            row = db.execute(
                """SELECT id FROM searches WHERE telegram_bind_code=? AND enabled=1
                   AND (expires_at IS NULL OR expires_at>?)""",
                (code.upper(), now),
            ).fetchone()
            if not row:
                return None
            db.execute("UPDATE searches SET telegram_chat_id=? WHERE id=?", (str(chat_id), row["id"]))
            return str(row["id"])
