from __future__ import annotations

import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path

from coderai.project_tasks.agent_runner import _LAST_LAUNCH, agent_capabilities, agent_command, launch_agent, terminal_command
from coderai.project_tasks.storage import StorageError


class ProjectTasksAgentRunnerTest(unittest.TestCase):
    def test_provider_commands_are_fixed_and_interactive(self) -> None:
        project = Path("/tmp/example")
        codex = agent_command("codex", project, "explain")
        claude = agent_command("claude", project, "explain")
        self.assertEqual(codex, ["codex", "--sandbox", "read-only", "--cd", str(project), "explain"])
        self.assertEqual(claude, ["claude", "--permission-mode", "plan", "explain"])
        with self.assertRaises(StorageError):
            agent_command("shell", project, "explain")

    @mock.patch("coderai.project_tasks.agent_runner.explain_project")
    @mock.patch("coderai.project_tasks.agent_runner.shutil.which", return_value="/bin/codex")
    @mock.patch("coderai.project_tasks.agent_runner.time.sleep")
    @mock.patch("coderai.project_tasks.agent_runner.threading.Thread")
    @mock.patch("coderai.project_tasks.agent_runner.subprocess.Popen")
    def test_launches_in_project_without_capturing_output(
        self, popen: mock.Mock, thread: mock.Mock, _sleep: mock.Mock,
        _which: mock.Mock, explain: mock.Mock,
    ) -> None:
        project = Path("/tmp/example")
        _LAST_LAUNCH.clear()
        explain.return_value = {
            "question": "What next?", "evidence": ["one active task"],
            "caution": "Missing data is unknown.", "redactions": [],
        }
        process = popen.return_value
        process.poll.return_value = 0

        result = launch_agent(project, "codex", "What next?", "month", "2026-03-14")

        launcher = popen.call_args.args[0]
        self.assertEqual(launcher[0], "osascript" if sys.platform == "darwin" else launcher[0])
        self.assertIn("one active task", launcher[-1])
        popen.assert_called_once_with(
            launcher, cwd=project, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        thread.assert_not_called()
        self.assertEqual(result["mode"], "interactive-read-only")
        self.assertTrue(result["terminalOpened"])
        self.assertEqual(result["agentInitialized"], "unknown-check-terminal")
        self.assertNotIn("launched", result)
        self.assertNotIn("answer", result)
        explain.assert_called_once_with(project, "What next?", "month", "2026-03-14")

    @mock.patch("coderai.project_tasks.agent_runner.sys.platform", "darwin")
    def test_terminal_command_quotes_the_project_and_prompt(self) -> None:
        launcher = terminal_command(Path("/tmp/project name"), ["codex", "unsafe; prompt"])
        self.assertEqual(launcher[0], "osascript")
        self.assertIn("cd '/tmp/project name'", launcher[-1])
        self.assertIn("'unsafe; prompt'", launcher[-1])

    @mock.patch("coderai.project_tasks.agent_runner.sys.platform", "darwin")
    @mock.patch("coderai.project_tasks.agent_runner.shutil.which")
    def test_capabilities_explain_provider_availability(self, which: mock.Mock) -> None:
        which.side_effect = lambda name: f"/bin/{name}" if name == "codex" else None
        capabilities = agent_capabilities(Path("/tmp/example"))
        self.assertTrue(capabilities["codex"]["available"])
        self.assertFalse(capabilities["claude"]["available"])


if __name__ == "__main__":
    unittest.main()
