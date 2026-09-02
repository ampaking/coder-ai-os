from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coderai.orchestrator import CommandResult, RunController, _provider_command, discover_validation_commands


def command_result(argv: list[str], code: int = 0, stdout: str = "") -> CommandResult:
    return CommandResult(tuple(argv), code, stdout, "", 7)


class FakeRunner:
    def __init__(self, reviews: list[dict] | None = None, validation_code: int = 0,
                 fail_first_worker: bool = False) -> None:
        self.reviews = list(reviews or [])
        self.validation_code = validation_code
        self.fail_first_worker = fail_first_worker
        self.worker_calls = 0
        self.prompts: list[str] = []

    def __call__(self, argv: list[str], _cwd: Path, _timeout: int,
                 stdin: str | None = None) -> CommandResult:
        if argv[0] in {"codex", "claude"}:
            prompt = stdin or ""
            self.prompts.append(prompt)
            reviewing = prompt.startswith("Independently review")
            if not reviewing:
                self.worker_calls += 1
                if self.fail_first_worker and self.worker_calls == 1:
                    return command_result(argv, 1)
            payload = (self.reviews.pop(0) if reviewing and self.reviews else {
                "status": "approved" if reviewing else "implemented", "summary": "ok",
                "changed_files": [] if reviewing else ["src/example.py"],
                "findings": [], "remaining": [],
            })
            if argv[0] == "codex":
                target = Path(argv[argv.index("--output-last-message") + 1])
                target.write_text(json.dumps(payload), encoding="utf-8")
                return command_result(argv)
            return command_result(argv, stdout=json.dumps({"structured_output": payload}))
        if argv[:3] == ["git", "diff", "--check"]:
            return command_result(argv, self.validation_code)
        if argv[:3] == ["git", "status", "--porcelain=v1"]:
            return command_result(argv, stdout=" M src/example.py\0?? src/new.py\0")
        return command_result(argv)


class OrchestratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name).resolve()
        (self.project / ".git").mkdir()
        scripts = self.project / ".coder-ai" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "update-ai-context.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @mock.patch("coderai.orchestrator._available_providers", return_value=["codex"])
    def test_completion_requires_all_gates(self, _providers: mock.Mock) -> None:
        progress: list[str] = []
        state = RunController(self.project, FakeRunner(), progress=progress.append).execute(
            "implement feature", verify=["git diff --check"], max_attempts=1)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["completionGate"], {
            "worker": True, "impact": True, "validation": True, "review": True})
        self.assertEqual(state["changedFiles"], ["src/example.py", "src/new.py"])
        run_directory = self.project / ".coder-ai" / "runs" / state["runId"]
        self.assertTrue((run_directory / "impact.txt").is_file())
        self.assertTrue(any("worker codex" in message for message in progress))
        self.assertTrue(any(message.startswith("COMPLETE") for message in progress))
        self.assertTrue((self.project / ".coder-ai" / "runs" / state["runId"] / "state.json").is_file())

    @mock.patch("coderai.orchestrator._available_providers", return_value=["codex"])
    def test_failed_validation_never_completes(self, _providers: mock.Mock) -> None:
        state = RunController(self.project, FakeRunner(validation_code=1)).execute(
            "implement feature", verify=["git diff --check"], max_attempts=1)
        self.assertEqual(state["status"], "failed")
        self.assertFalse(state["completionGate"]["validation"])
        self.assertFalse(state["completionGate"]["review"])

    @mock.patch("coderai.orchestrator._available_providers", return_value=["codex"])
    def test_review_findings_return_to_worker(self, _providers: mock.Mock) -> None:
        finding = {"status": "findings", "summary": "race", "changed_files": [],
                   "findings": ["src/example.py:8 race"], "remaining": []}
        runner = FakeRunner(reviews=[finding])
        state = RunController(self.project, runner).execute(
            "implement feature", verify=["git diff --check"], max_attempts=2)
        self.assertEqual(state["status"], "completed")
        self.assertIn("src/example.py:8 race", next(p for p in runner.prompts if "attempt 2" in p))

    @mock.patch("coderai.orchestrator._available_providers", return_value=["codex", "claude"])
    def test_provider_failure_fails_over_and_cross_reviews(self, _providers: mock.Mock) -> None:
        state = RunController(self.project, FakeRunner(fail_first_worker=True)).execute(
            "implement feature", verify=["git diff --check"], max_attempts=2)
        self.assertEqual(state["workerProviders"], ["codex", "claude"])
        self.assertEqual(state["reviewProvider"], "codex")
        review = next(item for item in state["evidence"] if item["kind"] == "review")
        self.assertTrue(review["independentProvider"])

    def test_commands_keep_provider_sandboxes(self) -> None:
        schema, output = self.project / "schema.json", self.project / "result.json"
        codex = _provider_command("codex", "worker", self.project, schema, output, None)
        claude = _provider_command("claude", "reviewer", self.project, schema, output, None)
        self.assertIn("workspace-write", codex)
        self.assertIn("never", codex)
        self.assertIn("plan", claude)
        self.assertNotIn("dangerously-skip-permissions", claude)
        self.assertNotIn("task", codex)
        self.assertNotIn("review", claude)

    @mock.patch("coderai.orchestrator.shutil.which", return_value="/bin/tool")
    def test_validation_discovery_uses_script_names_not_shell_text(self, _which: mock.Mock) -> None:
        (self.project / "package.json").write_text(json.dumps({
            "scripts": {"test": "dangerous arbitrary contents", "lint": "also arbitrary"}
        }), encoding="utf-8")
        self.assertEqual(discover_validation_commands(self.project)[:2],
                         [["npm", "run", "lint"], ["npm", "run", "test"]])


if __name__ == "__main__":
    unittest.main()
