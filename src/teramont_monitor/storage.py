from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .models import Event, MonitorState, PriceDigest


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def load_state(path: str | Path) -> MonitorState:
    return MonitorState.from_dict(_read_json(Path(path), {}))


def save_state(path: str | Path, state: MonitorState) -> None:
    _write_json_atomic(Path(path), state.to_dict())


def load_pending(path: str | Path) -> list[Event]:
    return [Event.from_dict(item) for item in _read_json(Path(path), [])]


def save_pending(path: str | Path, events: Iterable[Event]) -> None:
    _write_json_atomic(Path(path), [event.to_dict() for event in events])


def load_price_digest(path: str | Path) -> PriceDigest:
    return PriceDigest.from_dict(_read_json(Path(path), {}))


def save_price_digest(path: str | Path, digest: PriceDigest) -> None:
    _write_json_atomic(Path(path), digest.to_dict())


def load_digest_progress(path: Path, digest: PriceDigest, total: int) -> int:
    if not path.exists():
        return 0
    record = _read_json(path, {})
    if (record["target_id"], record["observed_at"]) != (digest.target_id, digest.observed_at):
        return 0
    count = record["acknowledged"]
    if type(count) is not int or not 0 <= count <= total:
        raise ValueError("invalid digest delivery count")
    return count


def save_digest_progress(path: Path, digest: PriceDigest, count: int) -> None:
    _write_json_atomic(path, {
        "target_id": digest.target_id, "observed_at": digest.observed_at, "acknowledged": count,
    })


def append_history(path: str | Path, records: Iterable[dict[str, Any]]) -> None:
    path = Path(path)
    rows = list(records)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        for record in rows:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            stream.write("\n")
