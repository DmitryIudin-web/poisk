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
from .sources import Fetcher, load_source_configs, scan_all
from .storage import append_history, load_pending, load_state, save_pending, save_state
from .telegram import TelegramError, send_pending


@dataclass(frozen=True)
class RunSummary:
    successful_sources: int
    failed_sources: int
    listings: int
    new_events: int
    gaps: dict[str, str]
    source_statuses: dict[str, dict[str, int | str]]
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
                {"status": "ok", "listings": len(result.listings), "warnings": len(result.warnings)}
                if result.ok
                else {"status": "source_gap", "listings": 0, "gap": result.gap.code if result.gap else "unknown"}
            )
            for name, result in results.items()
        },
        exit_code=0 if successful else 2,
    )


def collect(
    config_path: str | Path,
    state_dir: str | Path,
    *,
    fetcher: Fetcher | None = None,
    dry_run: bool = False,
    observed_at: str | None = None,
) -> RunSummary:
    observed_at = observed_at or _now()
    results = scan_all(load_source_configs(config_path), fetcher=fetcher)
    root = Path(state_dir)
    state = load_state(root / "state.json")
    pending = load_pending(root / "pending-events.json")
    known = {event.id for event in pending}
    next_state, events, history = apply_scan(state, results, observed_at, known_event_ids=known)
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
    save_state(root / "state.json", next_state)
    save_pending(root / "pending-events.json", merged)
    return summary


def smoke(config_path: str | Path, *, fetcher: Fetcher | None = None) -> RunSummary:
    results = scan_all(load_source_configs(config_path), fetcher=fetcher)
    return _summary(results)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Volkswagen Teramont Pro marketplace monitor")
    commands = parser.add_subparsers(dest="command", required=True)
    collect_command = commands.add_parser("collect", help="scan sources and persist state")
    collect_command.add_argument("--config", default="config/sources.json")
    collect_command.add_argument("--state-dir", required=True)
    collect_command.add_argument("--dry-run", action="store_true")
    notify_command = commands.add_parser("notify", help="deliver queued Telegram events")
    notify_command.add_argument("--state-dir", required=True)
    smoke_command = commands.add_parser("smoke", help="scan sources without persistence")
    smoke_command.add_argument("--config", default="config/sources.json")
    smoke_command.add_argument("--dry-run", action="store_true", help="accepted for explicitness; smoke is always read-only")
    return parser


def main(argv: list[str] | None = None, *, environ: Mapping[str, str] | None = None) -> int:
    args = _parser().parse_args(argv)
    environ = os.environ if environ is None else environ
    if args.command == "collect":
        summary = collect(args.config, args.state_dir, dry_run=args.dry_run)
        print(json.dumps(asdict(summary), ensure_ascii=False, sort_keys=True))
        return summary.exit_code
    if args.command == "smoke":
        summary = smoke(args.config)
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
