from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "monitor.yml"
UNIVERSAL_CI = ROOT / ".github" / "workflows" / "test-universal.yml"
README = ROOT / "README.md"


class WorkflowContractTests(unittest.TestCase):
    def test_workflow_has_hourly_manual_and_least_privilege_contract(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("17 * * * *", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("contents: write", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("python-version: '3.12'", text)

    def test_job_environment_uses_context_available_before_runner_start(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("${{ runner.temp }}", text)
        self.assertIn("/tmp/vehicle-monitor-${{ github.run_id }}", text)
        self.assertIn("/tmp/vehicle-monitor-summaries-${{ github.run_id }}", text)

    def test_workflow_uses_exact_telegram_secrets(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("secrets.TELEGRAM_BOT_TOKEN", text)
        self.assertIn("secrets.TELEGRAM_CHAT_ID", text)
        secret_names = set(re.findall(r"secrets\.([A-Z0-9_]+)", text))
        self.assertEqual(secret_names, {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"})
        self.assertEqual(text.count("TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}"), 1)
        self.assertEqual(text.count("TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}"), 1)
        self.assertIsNone(re.search(r"\d{8,12}:[A-Za-z0-9_-]{30,}", text))

    def test_workflow_serializes_both_targets_and_persists_before_notification(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        ci_text = UNIVERSAL_CI.read_text(encoding="utf-8")
        self.assertIn("config/targets/teramont-pro-2026.json", text)
        self.assertIn("config/targets/range-rover-l460-d350-autobiography-2026.json", text)
        self.assertIn("config/range-rover-sources.json", text)
        self.assertIn('$STATE_DIR/range-rover-d350', text)
        self.assertNotIn("matrix:", text)
        self.assertNotIn("strategy:", text)
        self.assertNotIn("unittest discover", text)
        self.assertIn("unittest discover", ci_text)
        teramont_collect_at = text.index("teramont_monitor collect")
        range_rover_collect_at = text.index(
            "teramont_monitor collect", teramont_collect_at + 1
        )
        first_push_at = text.index("push origin HEAD:monitor-state")
        teramont_notify_at = text.index("teramont_monitor notify")
        range_rover_notify_at = text.index(
            "teramont_monitor notify", teramont_notify_at + 1
        )
        teramont_digest_at = text.index("teramont_monitor digest")
        range_rover_digest_at = text.index(
            "teramont_monitor digest", teramont_digest_at + 1
        )
        second_push_at = text.index("push origin HEAD:monitor-state", first_push_at + 1)
        self.assertLess(teramont_collect_at, range_rover_collect_at)
        self.assertLess(range_rover_collect_at, first_push_at)
        self.assertLess(first_push_at, teramont_notify_at)
        self.assertLess(first_push_at, range_rover_notify_at)
        self.assertLess(teramont_notify_at, range_rover_notify_at)
        self.assertLess(range_rover_notify_at, teramont_digest_at)
        self.assertLess(teramont_digest_at, range_rover_digest_at)
        self.assertLess(range_rover_notify_at, second_push_at)
        self.assertIn('steps.collect.outputs.teramont_status', text[teramont_digest_at - 500:range_rover_digest_at + 500])
        self.assertIn('steps.collect.outputs.range_rover_status', text[teramont_digest_at - 500:range_rover_digest_at + 500])

    def test_combined_failure_is_reported_only_after_delivery_state_is_persisted(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("id: collect", text)
        self.assertIn('teramont_status=${PIPESTATUS[0]}', text)
        self.assertIn('range_rover_status=${PIPESTATUS[0]}', text)
        self.assertIn('teramont_status=$teramont_status', text)
        self.assertIn('range_rover_status=$range_rover_status', text)
        persist_at = text.index("Persist observations and pending events")
        delivery_persist_at = text.index("Persist Telegram delivery state")
        fail_at = text.index("Fail after both target collections and delivery persistence")
        notify_at = text.index("Deliver significant Telegram events")
        self.assertLess(persist_at, fail_at)
        self.assertLess(notify_at, delivery_persist_at)
        self.assertLess(delivery_persist_at, fail_at)
        self.assertIn('steps.collect.outputs.teramont_status', text)
        self.assertIn('steps.collect.outputs.range_rover_status', text)
        self.assertIn('exit 2', text)

    def test_first_state_branch_setup_does_not_remove_already_empty_orphan(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("switch --orphan monitor-state", text)
        self.assertNotIn('git -C "$STATE_DIR" rm -rf .', text)

    def test_failure_diagnostics_are_sanitized_artifact(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("if: failure()", text)
        self.assertIn("actions/upload-artifact@v4", text)
        self.assertIn("teramont-summary.json", text)
        self.assertIn("range-rover-summary.json", text)
        self.assertNotIn("$STATE_DIR", text[text.index("Upload sanitized failure summaries"):])
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
