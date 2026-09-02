#!/usr/bin/env python3
"""Focused consent and project-isolation tests."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "src" / "coderai" / "project_tasks" / "cli.py"


def project_hash(project: Path) -> str:
    return f"sha256:{hashlib.sha256(str(project).encode()).hexdigest()}"


class ProjectTasksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "project"
        (self.project / ".coder-ai").mkdir(parents=True)
        self.project = self.project.resolve()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        identity = {
            "project_id": "project",
            "git_root_hash": project_hash(self.project),
            "remote_hash": "sha256:test",
        }
        (self.project / ".coder-ai" / "identity.json").write_text(json.dumps(identity))

    def run_cli(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(CLI), "--project", str(self.project), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

    def test_enable_is_local_and_mode_is_private(self) -> None:
        self.run_cli("enable")
        settings = self.project / ".coder-ai" / "tasks" / "settings.json"
        value = json.loads(settings.read_text())
        self.assertTrue(value["enabled"])
        self.assertTrue(value["automaticCollection"])
        self.assertEqual(value["projectHash"], project_hash(self.project))
        self.assertEqual(stat.S_IMODE(settings.stat().st_mode), 0o600)

    def test_copied_settings_are_rejected(self) -> None:
        self.run_cli("enable")
        other = Path(self.temporary.name) / "other"
        (other / ".coder-ai" / "tasks").mkdir(parents=True)
        other = other.resolve()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        (other / ".coder-ai" / "identity.json").write_text(json.dumps({
            "project_id": "other", "git_root_hash": project_hash(other), "remote_hash": "sha256:test"
        }))
        shutil_source = self.project / ".coder-ai" / "tasks" / "settings.json"
        (other / ".coder-ai" / "tasks" / "settings.json").write_bytes(shutil_source.read_bytes())
        result = subprocess.run(
            [sys.executable, str(CLI), "--project", str(other), "status"],
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("another project", result.stderr)

    def test_symlink_state_is_rejected(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        os.symlink(outside, self.project / ".coder-ai" / "tasks")
        result = self.run_cli("enable", check=False)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(list(outside.iterdir()), [])

    def test_delete_requires_yes_and_removes_exact_state(self) -> None:
        self.run_cli("enable")
        result = self.run_cli("delete", check=False)
        self.assertEqual(result.returncode, 3)
        self.run_cli("delete", "--yes")
        self.assertFalse((self.project / ".coder-ai" / "tasks").exists())
        self.assertTrue((self.project / ".coder-ai" / "identity.json").exists())

    def test_open_can_be_closed_from_another_command(self) -> None:
        self.run_cli("enable")
        process = subprocess.Popen(
            [sys.executable, str(CLI), "--project", str(self.project), "open", "--no-browser"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(lambda: process.poll() is None and process.terminate())
        state = self.project / ".coder-ai" / "tasks" / "dashboard.json"
        for _ in range(50):
            if state.exists():
                break
            time.sleep(0.02)
        self.assertTrue(state.exists())
        duplicate = self.run_cli("open", "--no-browser", check=False)
        self.assertEqual(duplicate.returncode, 3)
        self.assertIn("already running", duplicate.stderr)
        self.assertIsNone(process.poll())
        result = self.run_cli("close")
        self.assertIn("Stopped", result.stdout)
        self.assertEqual(process.wait(timeout=3), 0)
        process.communicate()
        self.assertFalse(state.exists())

    def test_close_discards_stale_state_without_signalling(self) -> None:
        self.run_cli("enable")
        state = self.project / ".coder-ai" / "tasks" / "dashboard.json"
        state.write_text(json.dumps({
            "instanceId": "stale", "controlToken": "stale-control", "port": 9,
            "projectHash": project_hash(self.project),
        }))
        result = self.run_cli("close")
        self.assertIn("No running", result.stdout)
        self.assertFalse(state.exists())


if __name__ == "__main__":
    unittest.main()
