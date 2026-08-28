from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from teramont_monitor.events import apply_scan
from teramont_monitor.models import MonitorState, SourceResult
from teramont_monitor.storage import append_history, load_pending, load_state, save_pending, save_state
from tests.test_qualify import matching_listing


class StorageTests(unittest.TestCase):
    def test_missing_state_and_pending_files_are_empty(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(load_state(root / "state.json"), MonitorState())
            self.assertEqual(load_pending(root / "pending-events.json"), [])

    def test_state_round_trips_atomically(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state, _, _ = apply_scan(
                MonitorState(),
                {"drom": SourceResult("drom", True, (matching_listing(),))},
                "2026-08-28T12:00:00Z",
            )

            save_state(path, state)

            self.assertEqual(load_state(path), state)
            self.assertFalse(path.with_name("state.json.tmp").exists())

    def test_pending_events_round_trip(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pending-events.json"
            _, events, _ = apply_scan(
                MonitorState(),
                {"drom": SourceResult("drom", True, (matching_listing(),))},
                "2026-08-28T12:00:00Z",
            )
            save_pending(path, events)
            self.assertEqual(load_pending(path), events)

    def test_history_is_append_only_jsonl(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            append_history(path, [{"type": "observation", "n": 1}])
            append_history(path, [{"type": "source_gap", "n": 2}])

            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["n"] for row in rows], [1, 2])


if __name__ == "__main__":
    unittest.main()
