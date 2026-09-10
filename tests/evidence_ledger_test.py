"""Tasks 01–02 — evidence recorded by the harness, not authored by the model."""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from coderai.evidence import ledger

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "scripts" / "evidence-post-tool.py"


def _append_many(args) -> None:
    project, count = args
    for index in range(count):
        ledger.append(Path(project), ledger.COMMAND, command=f"echo {index}", exit=0)


class Ledger(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        (self.project / ".coder-ai").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_records_round_trip(self) -> None:
        ledger.append(self.project, ledger.COMMAND, command="make test", exit=0)
        ledger.append(self.project, ledger.EDIT, paths=["web/Card.tsx"])
        records = ledger.read(self.project)
        self.assertEqual([item.kind for item in records], [ledger.COMMAND, ledger.EDIT])
        self.assertEqual(records[0].command, "make test")
        self.assertEqual(records[1].paths, ["web/Card.tsx"])

    def test_the_file_is_private(self) -> None:
        ledger.append(self.project, ledger.COMMAND, command="x")
        path = ledger.directory(self.project) / "current.jsonl"
        self.assertEqual(oct(path.stat().st_mode)[-3:], "600")

    def test_secrets_never_enter_the_ledger(self) -> None:
        ledger.append(self.project, ledger.COMMAND,
                      command="deploy --token ghp_abcdefghijklmnopqrstuvwxyz0123456789")
        ledger.append(self.project, ledger.COMMAND, command="AWS_SECRET_KEY=hunter2hunter2 make")
        text = (ledger.directory(self.project) / "current.jsonl").read_text()
        self.assertNotIn("ghp_abcdefghij", text)
        self.assertNotIn("hunter2", text)
        self.assertIn("[redacted]", text)

    def test_output_is_never_stored(self) -> None:
        source = (REPO / "src" / "coderai" / "evidence" / "ledger.py").read_text()
        self.assertNotIn("stdout", source)
        self.assertNotIn("stderr", source)

    def test_concurrent_appends_stay_valid(self) -> None:
        with multiprocessing.Pool(4) as pool:
            pool.map(_append_many, [(str(self.project), 20)] * 4)
        lines = (ledger.directory(self.project) / "current.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 80)
        for line in lines:
            json.loads(line)

    def test_a_new_task_does_not_inherit_old_evidence(self) -> None:
        ledger.append(self.project, ledger.COMMAND, command="old work", exit=0)
        ledger.start(self.project, "new-task")
        self.assertEqual(ledger.read(self.project), [])
        self.assertEqual(len(ledger.read(self.project, task="current")), 1)

    def test_unsafe_task_ids_are_refused(self) -> None:
        for task in ("../escape", "a/b", "", "x" * 100):
            with self.subTest(task=task), self.assertRaises(ledger.LedgerError):
                ledger.start(self.project, task)

    def test_an_empty_ledger_means_nothing_is_verified(self) -> None:
        self.assertFalse(ledger.exists(self.project))
        ledger.append(self.project, ledger.COMMAND, command="x")
        self.assertTrue(ledger.exists(self.project))

    def test_last_edit_finds_when_a_file_changed(self) -> None:
        ledger.append(self.project, ledger.COMMAND, command="make test", exit=0)
        ledger.append(self.project, ledger.EDIT, paths=["web/Card.tsx"])
        records = ledger.read(self.project)
        edited = ledger.last_edit(records, ["web/Card.tsx"])
        self.assertGreater(edited, 0)
        self.assertGreaterEqual(edited, records[0].at)
        self.assertEqual(ledger.last_edit(records, ["other.py"]), 0.0)

    def test_commands_matching(self) -> None:
        ledger.append(self.project, ledger.COMMAND, command="make test", exit=0)
        ledger.append(self.project, ledger.COMMAND, command="npm run lint", exit=0)
        records = ledger.read(self.project)
        self.assertEqual(len(ledger.commands_matching(records, ["make test"])), 1)
        self.assertEqual(len(ledger.commands_matching(records, ["pytest"])), 0)


class Hook(unittest.TestCase):
    """The hook is how facts arrive without the model's help."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        (self.project / ".coder-ai").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def fire(self, payload: dict) -> subprocess.CompletedProcess:
        env = {**os.environ, "CODER_AI_SRC": str(REPO / "src")}
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True, env=env, timeout=30)

    def test_a_successful_command_is_recorded(self) -> None:
        result = self.fire({"cwd": str(self.project), "tool_name": "Bash",
                            "tool_input": {"command": "make test"},
                            "tool_response": {"stdout": "ok", "stderr": ""}})
        self.assertEqual(result.returncode, 0)
        records = ledger.read(self.project)
        self.assertEqual(records[0].command, "make test")
        self.assertTrue(records[0].ok)

    def test_a_failing_command_is_recorded_as_failing(self) -> None:
        self.fire({"cwd": str(self.project), "tool_name": "Bash",
                   "tool_input": {"command": "make test"},
                   "tool_response": {"error": "exit status 1", "is_error": True}})
        self.assertFalse(ledger.read(self.project)[0].ok)

    def test_an_explicit_exit_code_wins(self) -> None:
        self.fire({"cwd": str(self.project), "tool_name": "Bash",
                   "tool_input": {"command": "pytest"},
                   "tool_response": {"exit_code": 2}})
        record = ledger.read(self.project)[0]
        self.assertEqual(record.exit, 2)
        self.assertFalse(record.ok)

    def test_an_interrupted_command_is_not_evidence(self) -> None:
        self.fire({"cwd": str(self.project), "tool_name": "Bash",
                   "tool_input": {"command": "make test"},
                   "tool_response": {"interrupted": True}})
        self.assertFalse(ledger.read(self.project)[0].ok)

    def test_edits_are_recorded_relative_to_the_project(self) -> None:
        target = self.project / "web" / "Card.tsx"
        self.fire({"cwd": str(self.project), "tool_name": "Edit",
                   "tool_input": {"file_path": str(target)}, "tool_response": {}})
        self.assertEqual(ledger.read(self.project)[0].paths, ["web/Card.tsx"])

    def test_secrets_in_a_command_are_redacted_by_the_hook(self) -> None:
        self.fire({"cwd": str(self.project), "tool_name": "Bash",
                   "tool_input": {"command": "curl -H 'Authorization: Bearer abcdefghijklmnopqrst'"},
                   "tool_response": {}})
        self.assertNotIn("abcdefghijklmnopqrst", ledger.read(self.project)[0].command)

    def test_malformed_input_never_fails_the_tool_call(self) -> None:
        for payload in ("not json", "[]", "null", '{"tool_name": "Bash"}'):
            with self.subTest(payload=payload):
                env = {**os.environ, "CODER_AI_SRC": str(REPO / "src")}
                result = subprocess.run([sys.executable, str(HOOK)], input=payload,
                                        capture_output=True, text=True, env=env)
                self.assertEqual(result.returncode, 0)

    def test_a_project_without_coder_ai_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            self.fire({"cwd": other, "tool_name": "Bash",
                       "tool_input": {"command": "make test"}, "tool_response": {}})
            self.assertFalse((Path(other) / ".coder-ai").exists())

    def test_the_hook_is_registered_and_installed(self) -> None:
        self.assertIn("evidence_observation", (REPO / "config" / "hooks.yaml").read_text())
        self.assertIn("evidence-post-tool.py", (REPO / "install.sh").read_text())

    def test_it_is_fast(self) -> None:
        import time
        started = time.monotonic()
        self.fire({"cwd": str(self.project), "tool_name": "Bash",
                   "tool_input": {"command": "echo hi"}, "tool_response": {}})
        self.assertLess(time.monotonic() - started, 3.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
