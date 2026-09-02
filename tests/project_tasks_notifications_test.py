#!/usr/bin/env python3
"""Deterministic local-notification contract tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from coderai.project_tasks.analytics import safe_export
from coderai.project_tasks.notifications import analyze_notifications, notification_counts, set_notification_state
from coderai.project_tasks.project import load_settings, root_hash, write_settings
from coderai.project_tasks.storage import connect
from coderai.project_tasks.scheduler import install_scheduler, scheduler_status, uninstall_scheduler

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "src" / "coderai" / "project_tasks" / "cli.py"


class ProjectTasksNotificationsTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = (Path(temporary.name) / "sample").resolve()
        (self.project / ".coder-ai").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        digest = hashlib.sha256(str(self.project).encode()).hexdigest()
        (self.project / ".coder-ai" / "identity.json").write_text(json.dumps({
            "project_id": "sample", "git_root_hash": f"sha256:{digest}",
            "remote_hash": "sha256:test",
        }), encoding="utf-8")
        subprocess.run(
            [sys.executable, str(CLI), "--project", str(self.project), "enable"],
            check=True, capture_output=True, text=True,
        )
        settings = load_settings(self.project)
        settings["notificationsEnabled"] = True
        write_settings(self.project, settings)

    def test_weekly_and_attention_notifications_are_bounded_and_deduplicated(self) -> None:
        current = datetime(2026, 8, 28, 9, tzinfo=UTC)
        with connect(self.project) as connection:
            connection.execute(
                "INSERT INTO observations(id,project_hash,source,kind,scope,outcome,summary,confidence,"
                "occurred_at,collected_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("failed-proof", root_hash(self.project), "val", "validation-run", "project", "failed",
                 "Automated validation needs attention", 1.0, current.isoformat(), current.isoformat()),
            )
        first = analyze_notifications(self.project, current)
        second = analyze_notifications(self.project, current)
        self.assertEqual(len(first), 2)
        self.assertEqual([item["id"] for item in first], [item["id"] for item in second])
        self.assertEqual({item["kind"] for item in first}, {"weekly-progress", "daily-reflection"})
        serialized = json.dumps(first).lower()
        self.assertIn("take it easy", serialized)
        for forbidden in ("prompt", "sourcecode", "command", "filepath", "productivity"):
            self.assertNotIn(forbidden, serialized)
        with connect(self.project) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0], 2)

    def test_inbox_state_is_durable_and_project_scoped(self) -> None:
        current = datetime(2026, 8, 28, 9, tzinfo=UTC)
        with connect(self.project) as connection:
            connection.execute(
                "INSERT INTO observations(id,project_hash,source,kind,scope,outcome,summary,confidence,"
                "occurred_at,collected_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("proof", root_hash(self.project), "val", "validation-run", "project", "passed",
                 "Automated validation passed", 1.0, current.isoformat(), current.isoformat()),
            )
        notification = analyze_notifications(self.project, current)[0]
        self.assertEqual(set_notification_state(self.project, notification["id"], "read")["state"], "read")
        refreshed = analyze_notifications(self.project, current)
        self.assertEqual(next(item for item in refreshed if item["id"] == notification["id"])["state"], "read")
        self.assertEqual(set_notification_state(self.project, notification["id"], "dismissed")["state"], "dismissed")
        self.assertEqual(notification_counts(self.project)["dismissed"], 1)
        self.assertEqual(next(item for item in analyze_notifications(self.project, current)
                              if item["id"] == notification["id"])["state"], "dismissed")
        exported = next(item for item in safe_export(self.project)["notifications"]
                        if item["id"] == notification["id"])
        self.assertEqual(exported["state"], "dismissed")
        self.assertIsInstance(exported["evidence"], list)
        self.assertNotIn("fingerprint", exported)
        with self.assertRaisesRegex(ValueError, "notification state"):
            set_notification_state(self.project, notification["id"], "seen")
        with self.assertRaisesRegex(ValueError, "not found"):
            set_notification_state(self.project, "notification_other", "read")

    def test_no_evidence_creates_no_notification(self) -> None:
        self.assertEqual(analyze_notifications(
            self.project, datetime(2026, 8, 29, 6, tzinfo=UTC),
        ), [])

    def test_disabled_notifications_are_a_complete_no_op(self) -> None:
        settings = load_settings(self.project)
        settings["notificationsEnabled"] = False
        write_settings(self.project, settings)
        self.assertEqual(analyze_notifications(
            self.project, datetime(2026, 8, 29, 6, tzinfo=UTC),
        ), [])

    def test_macos_scheduler_is_explicit_bounded_and_removable(self) -> None:
        launch_home = self.project / "home"
        with mock.patch("coderai.project_tasks.scheduler.Path.home", return_value=launch_home), \
             mock.patch("coderai.project_tasks.scheduler.platform.system", return_value="Darwin"), \
             mock.patch("coderai.project_tasks.scheduler.subprocess.run") as run:
            run.return_value.returncode = 0
            installed = install_scheduler(self.project, 900)
            self.assertTrue(installed["installed"])
            plist_path = next((launch_home / "Library" / "LaunchAgents").glob("*.plist"))
            payload = plist_path.read_text(encoding="utf-8")
            self.assertIn("notify", payload)
            self.assertIn("--deliver", payload)
            self.assertIn("cli.py", payload)
            self.assertIn("18", payload)
            self.assertIn("StartCalendarInterval", payload)
            removed = uninstall_scheduler(self.project)
            self.assertFalse(removed["installed"])
            self.assertEqual(run.call_count, 4)
            self.assertIn("bootout", run.call_args_list[0].args[0])


if __name__ == "__main__":
    unittest.main()
