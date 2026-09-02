from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from coderai.project_tasks.hook_collector import MAX_HOOK_BYTES, capture_hook, capture_validation_result
from coderai.project_tasks.analytics import period_insights
from coderai.project_tasks.project import state_dir
from coderai.project_tasks.storage import connect, list_tasks

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "src" / "coderai" / "project_tasks" / "cli.py"


class ProjectTasksHookCollectorTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = (Path(temporary.name) / "sample").resolve()
        (self.project / ".coder-ai").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        digest = hashlib.sha256(str(self.project).encode()).hexdigest()
        (self.project / ".coder-ai" / "identity.json").write_text(json.dumps({
            "project_id": "sample", "git_root_hash": f"sha256:{digest}", "remote_hash": "sha256:test",
        }), encoding="utf-8")
        self.run_cli("enable")

    def run_cli(self, *args: str, input_value: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), "--project", str(self.project), *args], input=input_value,
            capture_output=True, text=True, check=False,
        )

    def test_disabled_is_complete_no_op(self) -> None:
        self.run_cli("collect", "disable")
        database = state_dir(self.project) / "tasks.sqlite3"
        before = database.stat().st_mtime_ns
        result = capture_hook(self.project, "claude", json.dumps({
            "hook_event_name": "Stop", "session_id": "session-private",
            "prompt": "must never be stored",
        }).encode())
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(database.stat().st_mtime_ns, before)

    def test_enabled_capture_is_idempotent_and_content_free(self) -> None:
        self.run_cli("collect", "enable")
        payload = json.dumps({
            "hook_event_name": "PostToolUse", "session_id": "session-private",
            "tool_name": "Bash", "tool_use_id": "tool-private",
            "tool_input": {"command": "echo secret", "file_path": "/private/source.py"},
            "tool_response": "raw terminal output",
        })
        first = json.loads(self.run_cli("hook", "claude", "--json", input_value=payload).stdout)
        second = json.loads(self.run_cli("hook", "claude", "--json", input_value=payload).stdout)
        self.assertTrue(first["captured"])
        self.assertEqual(second["status"], "duplicate")
        with connect(self.project) as connection:
            observations = [dict(row) for row in connection.execute(
                "SELECT source,kind,summary FROM observations WHERE source='agent-hook'"
            )]
            receipts = [dict(row) for row in connection.execute(
                "SELECT collector,accepted_count,duplicate_count FROM collector_receipts "
                "WHERE collector='claude-lifecycle' ORDER BY finished_at"
            )]
        self.assertEqual(observations, [{"source": "agent-hook", "kind": "agent-tool-use",
                                         "summary": "AI coding activity observed"}])
        self.assertEqual(len(receipts), 1)
        stored = json.dumps({"observations": observations, "receipts": receipts})
        for forbidden in ("secret", "private", "source.py", "terminal", "session-private", "tool-private"):
            self.assertNotIn(forbidden, stored)
        insights = period_insights(self.project, "day")
        automatic = [item for item in insights["activity"] if item.get("evidenceOnly")]
        self.assertEqual(len(automatic), 1)
        self.assertEqual(insights["totals"]["other"], 0)
        self.assertEqual(insights["totals"]["automaticObservations"], 1)
        self.assertEqual(insights["totals"]["totalEvidence"], 1)
        self.assertEqual(insights["progressGuidance"]["headline"],
                         "Turn activity into an inspectable outcome")
        self.assertIn("not a productivity score", insights["progressGuidance"]["caution"])
        self.assertEqual(list_tasks(self.project), [])

        stopped = json.loads(self.run_cli("hook", "claude", "--json", input_value=json.dumps({
            "hook_event_name": "Stop", "session_id": "session-private",
        })).stdout)
        self.assertTrue(stopped["captured"])
        self.assertNotIn("taskStatus", stopped)
        self.assertEqual(list_tasks(self.project), [])
        repeated = json.loads(self.run_cli("hook", "claude", "--json", input_value=json.dumps({
            "hook_event_name": "Stop", "session_id": "session-private",
            "stop_hook_active": True,
        })).stdout)
        self.assertEqual(repeated["status"], "duplicate")
        self.assertEqual(list_tasks(self.project), [])

        old = (datetime.now(UTC) - timedelta(days=100)).isoformat()
        with connect(self.project) as connection:
            connection.execute("UPDATE observations SET occurred_at=?", (old,))
            connection.execute("UPDATE collector_receipts SET finished_at=?", (old,))
        self.run_cli("cleanup")
        with connect(self.project) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM collector_receipts").fetchone()[0], 0)

    def test_invalid_and_oversized_input_fail_without_blocking_agent(self) -> None:
        self.run_cli("collect", "enable")
        invalid = self.run_cli("hook", "claude", "--json", input_value="not-json")
        oversized = self.run_cli("hook", "claude", "--json", input_value="x" * (MAX_HOOK_BYTES + 1))
        self.assertEqual(invalid.returncode, 0)
        self.assertEqual(oversized.returncode, 0)
        self.assertEqual(json.loads(invalid.stdout)["status"], "rejected")
        self.assertEqual(json.loads(oversized.stdout)["reason"], "input-too-large")

    def test_hook_hot_path_does_not_reinitialize_current_schema(self) -> None:
        self.run_cli("collect", "enable")
        with mock.patch("coderai.project_tasks.storage.initialize") as initialize:
            result = capture_hook(self.project, "claude", json.dumps({
                "hook_event_name": "PostToolUse", "session_id": "session-fast",
                "tool_name": "Edit", "tool_use_id": "tool-fast",
            }).encode())
        self.assertTrue(result["captured"])
        initialize.assert_not_called()

    def test_explicit_validation_result_is_idempotent_and_actionable(self) -> None:
        self.run_cli("collect", "enable")
        first = capture_validation_result(self.project, "val", "run-private", "failed")
        replay = capture_validation_result(self.project, "val", "run-private", "failed")
        self.assertTrue(first["captured"])
        self.assertEqual(replay["status"], "duplicate")
        insights = period_insights(self.project, "day")
        self.assertEqual(insights["totals"]["automaticFailed"], 1)
        self.assertEqual(insights["progressGuidance"]["headline"],
                         "Resolve the strongest attention signal")
        with connect(self.project) as connection:
            stored = json.dumps([dict(row) for row in connection.execute(
                "SELECT source,kind,outcome,summary FROM observations"
            )])
        self.assertNotIn("run-private", stored)


if __name__ == "__main__":
    unittest.main()
