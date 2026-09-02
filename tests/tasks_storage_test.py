#!/usr/bin/env python3
"""Structured event and SQLite storage tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "src" / "coderai" / "project_tasks" / "cli.py"


class TasksStorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        project = Path(self.temporary.name) / "project"
        (project / ".coder-ai").mkdir(parents=True)
        self.project = project.resolve()
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

    def event(self) -> dict[str, object]:
        return {
            "eventVersion": 1,
            "eventId": "event-one",
            "type": "task_started",
            "summary": "Create the local event store",
            "task": {"id": "task-one", "title": "Create event store", "theme": "Project Tasks"},
            "session": {
                "id": "session-one", "agentName": "codex", "agentVersion": "1.0",
                "modelName": "test-model", "nativeSessionId": "native-one", "resumeSupported": True,
            },
            "evidence": [{"kind": "request", "summary": "User approved the POC"}],
        }

    def test_records_idempotent_event_task_and_session(self) -> None:
        payload = json.dumps(self.event())
        self.run_cli("record", "--json", payload)
        self.run_cli("record", "--json", payload)
        database = self.project / ".coder-ai" / "tasks" / "tasks.sqlite3"
        self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o600)
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM tasks").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM task_events").fetchone()[0], 1)

    def test_rejects_raw_prompt_and_secret_fields(self) -> None:
        for field in ("rawPrompt", "sourceCode", "api_key", "password"):
            event = self.event()
            event[field] = "must-not-store"
            result = self.run_cli("record", "--json", json.dumps(event), check=False)
            self.assertEqual(result.returncode, 3, field)
            self.assertIn("forbidden field", result.stderr)

    def test_rejects_unknown_event_and_oversized_summary(self) -> None:
        event = self.event()
        event["type"] = "anything_happened"
        result = self.run_cli("record", "--json", json.dumps(event), check=False)
        self.assertEqual(result.returncode, 3)
        event = self.event()
        event["summary"] = "x" * 2001
        result = self.run_cli("record", "--json", json.dumps(event), check=False)
        self.assertEqual(result.returncode, 3)

    def test_native_session_id_is_stored_separately(self) -> None:
        self.run_cli("record", "--json", json.dumps(self.event()))
        database = self.project / ".coder-ai" / "tasks" / "tasks.sqlite3"
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT agent_name,model_name,native_session_id,resume_supported FROM sessions"
            ).fetchone()
        self.assertEqual(row, ("codex", "test-model", "native-one", 1))


if __name__ == "__main__":
    unittest.main()
