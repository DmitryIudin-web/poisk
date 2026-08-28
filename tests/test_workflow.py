from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "monitor.yml"
README = ROOT / "README.md"


class WorkflowContractTests(unittest.TestCase):
    def test_workflow_has_hourly_manual_and_least_privilege_contract(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("17 * * * *", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("contents: write", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("python-version: '3.12'", text)

    def test_workflow_uses_exact_telegram_secrets(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("secrets.TELEGRAM_BOT_TOKEN", text)
        self.assertIn("secrets.TELEGRAM_CHAT_ID", text)
        secret_names = set(re.findall(r"secrets\.([A-Z0-9_]+)", text))
        self.assertEqual(secret_names, {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"})
        self.assertIsNone(re.search(r"\d{8,12}:[A-Za-z0-9_-]{30,}", text))

    def test_tests_run_before_collect_and_state_is_pushed_before_notify(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        tests_at = text.index("unittest discover")
        collect_at = text.index("teramont_monitor collect")
        first_push_at = text.index("push origin HEAD:monitor-state")
        notify_at = text.index("teramont_monitor notify")
        second_push_at = text.index("push origin HEAD:monitor-state", first_push_at + 1)
        self.assertLess(tests_at, collect_at)
        self.assertLess(collect_at, first_push_at)
        self.assertLess(first_push_at, notify_at)
        self.assertLess(notify_at, second_push_at)

    def test_failure_diagnostics_are_sanitized_artifact(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("if: failure()", text)
        self.assertIn("actions/upload-artifact@v4", text)
        self.assertIn("monitor-summary.json", text)
        self.assertNotIn("raw-html", text)

    def test_readme_documents_exact_setup_and_activation_boundary(self) -> None:
        text = README.read_text(encoding="utf-8")
        for phrase in (
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
            "Read and write permissions",
            "monitor-state",
            "Run workflow",
            "source_gap",
            "не запускает мониторинг",
        ):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
