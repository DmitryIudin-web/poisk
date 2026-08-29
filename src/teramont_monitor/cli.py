from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .events import apply_scan
from .profiles import load_target_profile
from .sources import Fetcher, load_source_configs, scan_all
from .storage import append_history, load_pending, load_state, save_pending, save_state
from .telegram import TelegramError, send_pending


DEFAULT_TARGET = "config/targets/teramont-pro-2026.json"


@dataclass(frozen=True)
class RunSummary:
    successful_sources: int
    failed_sources: int
    listings: int
    new_events: int
    gaps: dict[str, str]
    source_statuses: dict[str, dict[str, bool | int | str]]
    exit_code: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _summary(results, new_events: int = 0) -> RunSummary:
    successful = sum(result.ok for result in results.values())
    failed = len(results) - successful
    return RunSummary(
        successful_sources=successful,
        failed_sources=failed,
        listings=sum(len(result.listings) for result in results.values()),
        new_events=new_events,
        gaps={name: result.gap.code for name, result in results.items() if result.gap},
        source_statuses={
            name: (
                {
                    "status": "ok",
                    "listings": len(result.listings),
                    "warnings": len(result.warnings),
                    "complete": result.complete,
                }
                if result.ok
                else {"status": "source_gap", "listings": 0, "gap": result.gap.code if result.gap else "unknown"}
            )
            for name, result in results.items()
        },
        exit_code=0 if successful else 2,
    )


def _assert_state_target_isolation(state, pending, target_id: str) -> None:
    persisted_target_ids = {
        item.listing.target_id for item in state.listings.values()
    }
    persisted_target_ids.update(event.listing.target_id for event in pending)
    foreign_target_ids = persisted_target_ids - {target_id}
    if foreign_target_ids:
        raise ValueError(
            "state directory contains events or listings for another target: "
            + ", ".join(sorted(foreign_target_ids))
        )


def collect(
    config_path: str | Path,
    state_dir: str | Path,
    *,
    target_path: str | Path = DEFAULT_TARGET,
    fetcher: Fetcher | None = None,
    dry_run: bool = False,
    observed_at: str | None = None,
) -> RunSummary:
    observed_at = observed_at or _now()
    profile = load_target_profile(target_path)
    root = Path(state_dir)
    state = load_state(root / "state.json")
    pending = load_pending(root / "pending-events.json")
    _assert_state_target_isolation(state, pending, profile.target_id)
    results = scan_all(load_source_configs(config_path), profile, fetcher=fetcher)
    known = {event.id for event in pending}
    next_state, events, history = apply_scan(state, results, observed_at, profile, known_event_ids=known)
    summary = _summary(results, len(events))
    if dry_run:
        return summary
    if history:
        append_history(root / "history.jsonl", history)
    if not summary.successful_sources:
        return summary
    merged = [*pending]
    pending_ids = {event.id for event in pending}
    merged.extend(event for event in events if event.id not in pending_ids)
    save_pending(root / "pending-events.json", merged)
    save_state(root / "state.json", next_state)
    return summary


def smoke(
    config_path: str | Path,
    *,
    target_path: str | Path = DEFAULT_TARGET,
    fetcher: Fetcher | None = None,
) -> RunSummary:
    profile = load_target_profile(target_path)
    results = scan_all(load_source_configs(config_path), profile, fetcher=fetcher)
    return _summary(results)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Volkswagen Teramont Pro marketplace monitor")
    commands = parser.add_subparsers(dest="command", required=True)
    collect_command = commands.add_parser("collect", help="scan sources and persist state")
    collect_command.add_argument("--config", default="config/sources.json")
    collect_command.add_argument("--state-dir", required=True)
    collect_command.add_argument("--target", default=DEFAULT_TARGET)
    collect_command.add_argument("--dry-run", action="store_true")
    notify_command = commands.add_parser("notify", help="deliver queued Telegram events")
    notify_command.add_argument("--state-dir", required=True)
    smoke_command = commands.add_parser("smoke", help="scan sources without persistence")
    smoke_command.add_argument("--config", default="config/sources.json")
    smoke_command.add_argument("--target", default=DEFAULT_TARGET)
    smoke_command.add_argument("--dry-run", action="store_true", help="accepted for explicitness; smoke is always read-only")
    return parser


def main(argv: list[str] | None = None, *, environ: Mapping[str, str] | None = None) -> int:
    args = _parser().parse_args(argv)
    environ = os.environ if environ is None else environ
    if args.command == "collect":
        summary = collect(args.config, args.state_dir, target_path=args.target, dry_run=args.dry_run)
        print(json.dumps(asdict(summary), ensure_ascii=False, sort_keys=True))
        return summary.exit_code
    if args.command == "smoke":
        summary = smoke(args.config, target_path=args.target)
        print(json.dumps(asdict(summary), ensure_ascii=False, sort_keys=True))
        return summary.exit_code
    token = environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required", file=sys.stderr)
        return 2
    try:
        delivered = send_pending(args.state_dir, token, chat_id)
    except TelegramError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps({"delivered": delivered}, sort_keys=True))
    return 0
