"""Task 07 — the report the harness writes, and the case that motivated it."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from coderai.evidence import ledger
from coderai.evidence.acceptance import load as load_acceptance, record_start
from coderai.evidence.prove import NOT_DONE, STALE, UNVERIFIED, VERIFIED, prove, render


class Scenario(unittest.TestCase):
    """A project with an API and a UI, and an agent that only did the API."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        (self.project / ".coder-ai" / "val").mkdir(parents=True)
        (self.project / ".coder-ai" / "val" / "config.json").write_text(
            json.dumps({"watchGlobs": ["web/**/*.tsx"]}))
        (self.project / "Makefile").write_text("test:\n\tpytest -q\n")
        subprocess.run(["git", "init", "-q", str(self.project)], check=True,
                       capture_output=True)
        self.task = "subscriptions"
        self.changed = ["api/subscriptions.py", "web/PlanCard.tsx"]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def acceptance(self, body: str) -> None:
        path = self.project / ".ai" / self.task / "acceptance.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        listing = load_acceptance(self.project, self.task)
        assert listing is not None
        record_start(self.project, self.task, listing)

    def verdict(self):
        return prove(self.project, self.task, changed=self.changed)

    def statuses(self):
        return {line.item: line.status for line in self.verdict().lines}

    def test_the_case_that_motivated_this(self) -> None:
        """API done and checked, UI written but never looked at, two items untouched."""
        ledger.start(self.project, self.task)
        self.acceptance(
            "- [x] API: POST /subscriptions returns 201\n"
            "- [x] UI: the plan card shows the renewal date\n"
            "- [ ] UI: mobile 375px layout does not overflow\n"
            "- [ ] i18n: ja and en strings for the card\n")
        ledger.append(self.project, ledger.EDIT, paths=["web/PlanCard.tsx"])
        ledger.append(self.project, ledger.COMMAND, command="make test", exit=0, ok=True)

        verdict = self.verdict()
        statuses = {line.item: line.status for line in verdict.lines}
        self.assertEqual(statuses["API: POST /subscriptions returns 201"], VERIFIED)
        self.assertEqual(statuses["UI: the plan card shows the renewal date"], UNVERIFIED)
        self.assertEqual(statuses["UI: mobile 375px layout does not overflow"], NOT_DONE)
        self.assertEqual(statuses["i18n: ja and en strings for the card"], NOT_DONE)
        self.assertIn("partial", verdict.summary)
        self.assertFalse(verdict.verified)

        text = render(verdict)
        self.assertIn("NOT DONE", text)
        self.assertIn("not verified", text)

    def test_a_visual_run_verifies_the_ui(self) -> None:
        ledger.start(self.project, self.task)
        self.acceptance("- [x] UI: the plan card shows the renewal date\n")
        ledger.append(self.project, ledger.EDIT, paths=["web/PlanCard.tsx"])
        ledger.append(self.project, ledger.COMMAND,
                      command=".coder-ai/val/run run --task plan-card", exit=0, ok=True)
        self.assertEqual(self.statuses()["UI: the plan card shows the renewal date"], VERIFIED)

    def test_evidence_older_than_the_edit_is_stale(self) -> None:
        """A run from before the last change proves nothing about the change."""
        ledger.start(self.project, self.task)
        ledger.append(self.project, ledger.COMMAND,
                      command=".coder-ai/val/run run --task plan-card", exit=0, ok=True)
        import time
        time.sleep(0.01)
        ledger.append(self.project, ledger.EDIT, paths=["web/PlanCard.tsx"])
        self.assertEqual(self.statuses()["visual"], STALE)

    def test_a_failed_command_is_not_evidence(self) -> None:
        ledger.start(self.project, self.task)
        ledger.append(self.project, ledger.COMMAND, command="make test", exit=1, ok=False)
        self.assertEqual(self.statuses()["tests"], UNVERIFIED)

    def test_an_empty_ledger_verifies_nothing(self) -> None:
        self.acceptance("- [x] API: POST /subscriptions returns 201\n")
        verdict = self.verdict()
        self.assertFalse(verdict.observed)
        self.assertTrue(all(line.status != VERIFIED for line in verdict.lines))
        self.assertIn("No evidence was recorded", render(verdict))

    def test_a_shrunken_acceptance_list_is_reported(self) -> None:
        ledger.start(self.project, self.task)
        self.acceptance("- [ ] API: POST /subscriptions returns 201\n"
                        "- [ ] UI: mobile 375px layout does not overflow\n")
        (self.project / ".ai" / self.task / "acceptance.md").write_text(
            "- [x] API: POST /subscriptions returns 201\n")
        text = render(self.verdict())
        self.assertIn("acceptance list changed", text)
        self.assertIn("mobile 375px", text)

    def test_it_suggests_splitting_work_that_crosses_surfaces(self) -> None:
        ledger.start(self.project, self.task)
        self.acceptance("- [ ] API: POST /subscriptions returns 201\n"
                        "- [ ] UI: mobile 375px layout does not overflow\n"
                        "- [ ] i18n: ja strings\n")
        text = render(self.verdict())
        self.assertIn("crosses 3 surfaces", text)

    def test_the_report_fits_on_a_screen(self) -> None:
        ledger.start(self.project, self.task)
        self.acceptance("".join(f"- [ ] API: endpoint {index} returns 201\n"
                                for index in range(40)))
        self.assertLess(len(render(self.verdict()).splitlines()), 60)

    def test_exit_status_means_something(self) -> None:
        ledger.start(self.project, self.task)
        ledger.append(self.project, ledger.COMMAND, command="make test", exit=0, ok=True)
        self.assertFalse(prove(self.project, self.task, changed=self.changed).verified)
        self.assertTrue(prove(self.project, self.task, changed=[]).verified)


class Command(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        (self.project / ".coder-ai").mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True,
                       capture_output=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_rig(self, *args: str) -> subprocess.CompletedProcess:
        CLI = Path(__file__).resolve().parents[1] / "bin" / "coder-ai"
        return subprocess.run([str(CLI), "prove", *args], cwd=str(self.project),
                              capture_output=True, text=True, timeout=90)

    def test_it_runs_and_never_crashes(self) -> None:
        result = self.run_rig()
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        self.assertIn(result.returncode, (0, 1))

    def test_json_output_is_parseable(self) -> None:
        result = self.run_rig("--json")
        payload = json.loads(result.stdout)
        self.assertIn("summary", payload)
        self.assertIn("observed", payload)

    def test_starting_a_task_scopes_the_evidence(self) -> None:
        result = self.run_rig("start", "my-task")
        self.assertEqual(result.returncode, 0)
        self.assertIn("earlier evidence does not count", result.stdout)
        self.assertEqual(ledger.current_task(self.project), "my-task")

    def test_an_unsafe_task_id_is_refused(self) -> None:
        self.assertEqual(self.run_rig("start", "../escape").returncode, 2)

    def test_help(self) -> None:
        result = self.run_rig("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("coder-ai prove", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
