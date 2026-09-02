#!/usr/bin/env python3
"""Task lifecycle, validation, links, and request-shaping tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from coderai.project_tasks.storage import connect

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "src" / "coderai" / "project_tasks" / "cli.py"


class TasksLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name) / "project"
        (base / ".coder-ai").mkdir(parents=True)
        self.project = base.resolve()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        digest = hashlib.sha256(str(self.project).encode()).hexdigest()
        (self.project / ".coder-ai" / "identity.json").write_text(json.dumps({
            "project_id": "project", "git_root_hash": f"sha256:{digest}", "remote_hash": "sha256:test"
        }))
        self.run_cli("enable")

    def run_cli(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(CLI), "--project", str(self.project), *arguments],
            check=False, capture_output=True, text=True,
        )
        if check and result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

    def record(self, event_type: str, **extra: object) -> dict[str, object]:
        event: dict[str, object] = {
            "type": event_type,
            "summary": event_type.replace("_", " "),
            "task": {"id": "task-main", "title": "Build task lifecycle"},
            "session": {"id": "session-main", "agentName": "codex"},
        }
        event.update(extra)
        return json.loads(self.run_cli("record", "--json", json.dumps(event)).stdout)

    def test_completion_requires_passing_validation(self) -> None:
        self.record("task_started")
        result = self.record("task_completed")
        self.assertEqual(result["status"], "needs_validation")
        self.record("validation_passed", validation={"category": "test", "commandSummary": "unit tests"})
        result = self.record("task_completed")
        self.assertEqual(result["status"], "completed")

    def test_task_details_separate_observed_span_from_reported_effort(self) -> None:
        self.record("task_started", occurredAt="2026-08-29T01:00:00+00:00")
        self.record("task_continued", occurredAt="2026-08-29T02:30:00+00:00")
        details = json.loads(self.run_cli("show", "task-main").stdout)
        self.assertIsNone(details["actual_minutes"])
        self.assertIsNone(details["estimated_minutes"])
        self.assertEqual(details["observedEffort"]["eventCount"], 2)
        self.assertEqual(details["observedEffort"]["evidenceSpanMinutes"], 90.0)
        self.assertEqual(details["observedEffort"]["endedSessionMinutes"], 0)
        self.assertIn("not continuous human work", details["observedEffort"]["caution"])

    def test_replayed_validation_event_is_idempotent(self) -> None:
        event = {
            "eventId": "same-validation", "type": "validation_passed", "summary": "tests pass",
            "task": {"id": "task-main", "title": "Build task lifecycle"},
            "session": {"id": "session-main", "agentName": "codex"},
            "validation": {"category": "test", "commandSummary": "unit tests"},
        }
        self.run_cli("record", "--json", json.dumps(event))
        self.run_cli("record", "--json", json.dumps(event))
        details = json.loads(self.run_cli("show", "task-main").stdout)
        self.assertEqual(len(details["validations"]), 1)
        with connect(self.project) as connection:
            observations = [dict(row) for row in connection.execute("SELECT * FROM observations")]
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["source"], "repository-checks")
        self.assertEqual(observations[0]["kind"], "test")
        self.assertEqual(observations[0]["outcome"], "passed")
        self.assertEqual(observations[0]["summary"], "test validation passed")
        self.assertNotIn("unit tests", json.dumps(observations[0]))

    def test_failed_validation_collects_duration_without_command_text(self) -> None:
        self.record(
            "validation_failed", confidence=0.75,
            validation={"category": "lint", "commandSummary": "private file path", "durationMs": 1250},
        )
        with connect(self.project) as connection:
            observation = dict(connection.execute("SELECT * FROM observations").fetchone())
        self.assertEqual(observation["outcome"], "failed")
        self.assertEqual(observation["metric_value"], 1250)
        self.assertEqual(observation["metric_unit"], "milliseconds")
        self.assertEqual(observation["confidence"], 0.75)
        self.assertNotIn("private file path", json.dumps(observation))

    def test_pause_block_resume_and_correction(self) -> None:
        self.record("task_started")
        self.assertEqual(self.record("task_paused")["status"], "paused")
        self.assertEqual(self.record("task_blocked", details={"summary": "Need schema"})["status"], "blocked")
        self.record("task_corrected", correction={"field": "title", "value": "Build deterministic lifecycle"})
        details = json.loads(self.run_cli("show", "task-main").stdout)
        self.assertEqual(details["title"], "Build deterministic lifecycle")
        self.assertEqual(details["status"], "blocked")
        self.assertEqual(self.record("task_resumed")["status"], "active")
        details = json.loads(self.run_cli("show", "task-main").stdout)
        self.assertEqual(details["blockers"][0]["status"], "resolved")
        self.assertIsNotNone(details["blockers"][0]["resolved_at"])

    def test_validation_before_later_change_cannot_complete_task(self) -> None:
        self.record("task_started")
        self.record("validation_passed", validation={"category": "test", "commandSummary": "old proof"})
        self.record("task_continued")
        result = self.record("task_completed")
        self.assertEqual(result["status"], "needs_validation")

    def test_task_link_requires_existing_task(self) -> None:
        self.record("task_started")
        second = {
            "type": "task_started", "summary": "second", "task": {"id": "task-two", "title": "Second"},
            "session": {"id": "session-two", "agentName": "claude"},
        }
        self.run_cli("record", "--json", json.dumps(second))
        self.record("task_continued", links=[{"taskId": "task-two", "type": "depends_on"}])
        details = json.loads(self.run_cli("show", "task-main").stdout)
        self.assertEqual(details["links"], [{"taskId": "task-two", "type": "depends_on"}])

    def test_shape_request_flags_mixed_work_and_missing_proof(self) -> None:
        result = json.loads(self.run_cli("shape", "Fix upload and improve UI and also change API").stdout)
        self.assertEqual(result["goal"], "Fix upload and improve UI and also change API")
        self.assertGreaterEqual(len(result["suggestions"]), 2)
        self.assertIn("proof", result)


if __name__ == "__main__":
    unittest.main()
