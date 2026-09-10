"""Delivery tasks 02, 03, 05, 06 + evidence 09 — discovery, opt-in, the run, the gate."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from coderai.delivery import declaration as declaration_module
from coderai.delivery.discovery import propose
from coderai.delivery.optin import answered, propose_and_record, record
from coderai.delivery.ship import (
    BLOCKED, FAILED, OK, REFUSED, SKIPPED, ShipError, plan_steps, ship, validate_message,
)
from coderai.evidence import ledger

WORKFLOW = """
delivery:
  base: origin/develop
  branch_pattern: "^(fix|feat)/[a-z0-9._-]+$"
  steps:
    - name: sync
      run: git fetch origin
    - name: verify
      run: make verify-local QUALITY_BASE_REF=origin/develop
    - name: push
      run: git push -u origin ${branch}
    - name: pr
      run: gh pr create --base develop --fill
"""


def a_project(root: Path, *, workflow: str | None = WORKFLOW, enabled: bool = True) -> Path:
    project = root / "app"
    (project / ".coder-ai").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "fix/retry", str(project)], check=True,
                   capture_output=True)
    if workflow:
        (project / ".coder-ai" / "delivery.yaml").write_text(workflow)
    if enabled:
        record(project, True, "test")
    return project


class Discovery(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = self.root / "app"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "develop", str(self.project)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.project), "remote", "add", "origin",
                        "https://example.invalid/x.git"], check=True, capture_output=True)
        (self.project / ".git" / "refs" / "remotes" / "origin").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def with_base(self) -> None:
        subprocess.run(["git", "-C", str(self.project), "commit", "-q", "--allow-empty",
                        "-m", "seed"], check=True, capture_output=True,
                       env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
                            "PATH": "/usr/bin:/bin"})
        subprocess.run(["git", "-C", str(self.project), "update-ref",
                        "refs/remotes/origin/develop", "HEAD"], check=True, capture_output=True)

    def test_it_proposes_the_projects_own_verify_target(self) -> None:
        self.with_base()
        (self.project / "Makefile").write_text("build:\n\techo x\nverify-local:\n\tpytest -q\n")
        proposal = propose(self.project)
        self.assertIsNotNone(proposal)
        assert proposal is not None
        verify = next(step for step in proposal.steps if step.name == "verify")
        self.assertEqual(verify.run, "make verify-local")
        self.assertIn("Makefile:", verify.evidence)
        self.assertEqual(proposal.base, "origin/develop")

    def test_ci_outranks_a_makefile(self) -> None:
        self.with_base()
        (self.project / "Makefile").write_text("check:\n\techo x\n")
        workflows = self.project / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("jobs:\n  a:\n    steps:\n      - run: make ci\n")
        proposal = propose(self.project)
        assert proposal is not None
        verify = next(step for step in proposal.steps if step.name == "verify")
        self.assertEqual(verify.run, "make ci")
        self.assertIn(".github/workflows/ci.yml:", verify.evidence)

    def test_package_scripts_are_used_when_present(self) -> None:
        self.with_base()
        (self.project / "package.json").write_text(
            json.dumps({"scripts": {"verify": "vitest run"}}, indent=2))
        proposal = propose(self.project)
        assert proposal is not None
        self.assertEqual(next(s for s in proposal.steps if s.name == "verify").run,
                         "npm run verify")

    def test_dangerous_targets_are_never_proposed(self) -> None:
        self.with_base()
        (self.project / "Makefile").write_text("deploy-prod:\n\techo x\nverify:\n\techo y\n")
        proposal = propose(self.project)
        assert proposal is not None
        # The word appears in the file's own prohibitions; what matters is that no
        # STEP runs one.
        self.assertNotIn("deploy", " ".join(step.run for step in proposal.steps))
        self.assertIn("may never merge, tag, release, or deploy", proposal.document())

    def test_a_project_with_nothing_to_verify_gets_no_proposal(self) -> None:
        self.with_base()
        self.assertIsNone(propose(self.project))

    def test_the_proposal_writes_a_loadable_declaration(self) -> None:
        self.with_base()
        (self.project / "Makefile").write_text("verify:\n\tpytest -q\n")
        proposal = propose(self.project)
        assert proposal is not None
        (self.project / ".coder-ai").mkdir(exist_ok=True)
        proposal.write(self.project)
        loaded = declaration_module.load(self.project)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual([step.name for step in loaded.steps],
                         ["sync", "rebase", "verify", "push", "pr"])


class OptIn(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = a_project(Path(self._tmp.name), enabled=False)
        self.lines: list[str] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def out(self, text="") -> None:
        self.lines.append(str(text))

    def test_non_interactive_never_enables(self) -> None:
        code = propose_and_record(self.project, interactive=False, out=self.out)
        self.assertEqual(code, 1)
        self.assertFalse(declaration_module.is_enabled(self.project))
        self.assertIn("not enabling", "\n".join(self.lines))

    def test_a_recorded_decision_is_not_re_asked(self) -> None:
        propose_and_record(self.project, interactive=False, out=self.out)
        self.lines.clear()
        propose_and_record(self.project, interactive=False, out=self.out)
        self.assertIn("already declined", "\n".join(self.lines))

    def test_confirming_enables_it(self) -> None:
        self.assertEqual(propose_and_record(self.project, assume_yes=True, out=self.out), 0)
        self.assertTrue(declaration_module.is_enabled(self.project))
        self.assertIn("delivery enabled", "\n".join(self.lines))

    def test_the_decision_is_machine_local_and_private(self) -> None:
        propose_and_record(self.project, assume_yes=True, out=self.out)
        path = self.project / ".coder-ai" / "local" / "delivery.json"
        self.assertTrue(path.is_file())
        self.assertEqual(oct(path.stat().st_mode)[-3:], "600")


class Runner(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = a_project(Path(self._tmp.name))
        self.ran: list[list[str]] = []
        ledger.start(self.project, "current")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def runner(self, failing: str = ""):
        def execute(argv, cwd, timeout):
            self.ran.append(list(argv))
            if failing and failing in " ".join(argv):
                return 1, "boom", 5
            return 0, "", 5
        return execute

    def verified(self) -> None:
        """Make the evidence gate pass: no requirements outstanding."""
        ledger.append(self.project, ledger.COMMAND, command="make verify-local", exit=0)

    def test_it_runs_the_declared_steps_in_order(self) -> None:
        self.verified()
        report = ship(self.project, branch="fix/retry", runner=self.runner())
        self.assertTrue(report.ok, report.render())
        self.assertEqual([item.name for item in report.results],
                         ["sync", "verify", "push", "pr"])
        self.assertEqual(self.ran[0], ["git", "fetch", "origin"])
        self.assertIn("fix/retry", self.ran[2])

    def test_a_failing_step_stops_everything_after_it(self) -> None:
        self.verified()
        report = ship(self.project, branch="fix/retry",
                      runner=self.runner(failing="verify-local"))
        self.assertFalse(report.ok)
        self.assertEqual(report.failed.name, "verify")
        self.assertEqual([item.name for item in report.results], ["sync", "verify"])
        self.assertNotIn(["git", "push", "-u", "origin", "fix/retry"], self.ran)

    def test_dry_run_executes_nothing(self) -> None:
        report = ship(self.project, branch="fix/retry", dry_run=True, runner=self.runner())
        self.assertEqual(self.ran, [])
        self.assertTrue(all(item.status == SKIPPED for item in report.results))
        self.assertIn("would run", report.render())

    def test_a_step_the_matrix_refuses_stops_the_run(self) -> None:
        (self.project / ".coder-ai" / "delivery.yaml").write_text(WORKFLOW.replace(
            "git push -u origin ${branch}", "git push --force origin main"))
        self.verified()
        report = ship(self.project, branch="fix/retry", runner=self.runner())
        self.assertFalse(report.ok)
        self.assertEqual(report.failed.status, REFUSED)
        self.assertIn("DENY_", report.failed.detail)

    def test_an_unmatched_branch_cannot_deliver(self) -> None:
        report = ship(self.project, branch="random", runner=self.runner())
        self.assertFalse(report.started)
        self.assertIn("does not match", report.reason)

    def test_a_project_without_a_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = a_project(Path(tmp), workflow=None)
            report = ship(project, runner=self.runner())
            self.assertFalse(report.started)
            self.assertIn("declares no delivery workflow", report.reason)

    def test_a_declaration_that_was_never_enabled_here(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = a_project(Path(tmp), enabled=False)
            report = ship(project, branch="fix/retry", runner=self.runner())
            self.assertFalse(report.started)
            self.assertIn("not enabled on this machine", report.reason)

    def test_steps_may_be_narrowed_but_not_to_skip_a_check(self) -> None:
        self.verified()
        report = ship(self.project, branch="fix/retry", only=["sync", "verify"],
                      runner=self.runner())
        self.assertEqual([item.name for item in report.results], ["sync", "verify"])
        with self.assertRaises(ShipError) as caught:
            plan_steps(declaration_module.load(self.project), ["push"])
        self.assertIn("will not skip it", str(caught.exception))

    def test_each_run_is_recorded_as_evidence(self) -> None:
        self.verified()
        ship(self.project, branch="fix/retry", runner=self.runner())
        commands = [item.command for item in ledger.read(self.project)]
        self.assertIn("git fetch origin", commands)


class TheGate(unittest.TestCase):
    """Evidence task 09 — unverified work cannot be delivered."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = a_project(self.root)
        (self.project / ".coder-ai" / "val").mkdir()
        (self.project / ".coder-ai" / "val" / "config.json").write_text(
            json.dumps({"watchGlobs": ["web/**/*.tsx"]}))
        (self.project / "web").mkdir()
        (self.project / "web" / "Card.tsx").write_text("x\n")
        ledger.start(self.project, "current")
        self.ran: list[list[str]] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def runner(self):
        def execute(argv, cwd, timeout):
            self.ran.append(list(argv))
            return 0, "", 5
        return execute

    def test_unverified_ui_cannot_be_pushed(self) -> None:
        ledger.append(self.project, ledger.EDIT, paths=["web/Card.tsx"])
        report = ship(self.project, branch="fix/retry", runner=self.runner())
        blocked = [item for item in report.results if item.status == BLOCKED]
        self.assertTrue(blocked, report.render())
        self.assertIn("unverified", blocked[0].detail)
        self.assertNotIn(["git", "push", "-u", "origin", "fix/retry"], self.ran)

    def test_a_visual_run_unblocks_it(self) -> None:
        ledger.append(self.project, ledger.EDIT, paths=["web/Card.tsx"])
        ledger.append(self.project, ledger.COMMAND,
                      command=".coder-ai/val/run run --task card", exit=0)
        ledger.append(self.project, ledger.COMMAND, command="make verify-local", exit=0)
        report = ship(self.project, branch="fix/retry", runner=self.runner())
        self.assertTrue(report.ok, report.render())

    def test_the_gate_does_not_block_a_dry_run(self) -> None:
        ledger.append(self.project, ledger.EDIT, paths=["web/Card.tsx"])
        report = ship(self.project, branch="fix/retry", dry_run=True, runner=self.runner())
        self.assertFalse([item for item in report.results if item.status == BLOCKED])

    def test_an_agent_cannot_override_the_gate(self) -> None:
        ledger.append(self.project, ledger.EDIT, paths=["web/Card.tsx"])
        report = ship(self.project, branch="fix/retry", allow_unverified=True,
                      is_agent=True, runner=self.runner())
        self.assertFalse(report.started)
        self.assertIn("may not bypass", report.reason)

    def test_a_human_may_override(self) -> None:
        ledger.append(self.project, ledger.EDIT, paths=["web/Card.tsx"])
        report = ship(self.project, branch="fix/retry", allow_unverified=True,
                      is_agent=False, runner=self.runner())
        self.assertTrue(report.started)


class Messages(unittest.TestCase):
    def test_ai_trailers_are_refused(self) -> None:
        for message in ("fix: x\n\nAI-Agent: claude", "fix: x\n\nCo-Authored-By: Claude <x>",
                        "fix: x\n\nAI-Model: opus"):
            with self.subTest(message=message), self.assertRaises(ShipError):
                validate_message(message)

    def test_an_ordinary_message_passes(self) -> None:
        self.assertEqual(validate_message("fix(worker): prevent duplicate retry execution"),
                         "fix(worker): prevent duplicate retry execution")

    def test_empty_is_refused(self) -> None:
        with self.assertRaises(ShipError):
            validate_message("   ")



class TheBaseBranchIsYourChoice(unittest.TestCase):
    """You develop from `develop` (or whatever you say); delivery may never push to it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "app"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "develop", str(self.project)],
                       check=True, capture_output=True)
        (self.project / "Makefile").write_text("verify:\n\tpytest -q\n")
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "PATH": "/usr/bin:/bin",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
        subprocess.run(["git", "-C", str(self.project), "commit", "-q", "--allow-empty",
                        "-m", "seed"], check=True, capture_output=True, env=env)
        for branch in ("develop", "main"):
            subprocess.run(["git", "-C", str(self.project), "update-ref",
                            f"refs/remotes/origin/{branch}", "HEAD"], check=True,
                           capture_output=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_develop_is_preferred_over_the_default_branch(self) -> None:
        from coderai.delivery.discovery import base_candidates, default_base

        self.assertEqual(base_candidates(self.project)[0], "origin/develop")
        self.assertEqual(default_base(self.project), "origin/develop")

    def test_your_choice_wins(self) -> None:
        from coderai.delivery.discovery import default_base

        self.assertEqual(default_base(self.project, "main"), "origin/main")
        self.assertEqual(default_base(self.project, "origin/staging"), "origin/staging")

    def test_the_proposal_uses_the_chosen_base_everywhere(self) -> None:
        from coderai.delivery.discovery import propose

        proposal = propose(self.project, "main")
        assert proposal is not None
        self.assertEqual(proposal.base, "origin/main")
        document = proposal.document()
        self.assertIn("git rebase origin/main", document)
        self.assertIn("gh pr create --base main", document)
        self.assertIn("may never push to that base", document)

    def test_the_chosen_base_becomes_a_protected_branch(self) -> None:
        from coderai.delivery.declaration import validate
        from coderai.delivery.discovery import propose
        from coderai.delivery.policy import DeliveryScope, decide_git

        proposal = propose(self.project, "develop")
        assert proposal is not None
        (self.project / ".coder-ai").mkdir()
        proposal.write(self.project)
        delivery = validate({"delivery": {
            "base": proposal.base, "branch_pattern": proposal.branch_pattern,
            "steps": [{"name": "v", "run": "make verify"}]}})
        scope = DeliveryScope(project=str(self.project), remote="origin",
                              base=delivery.base, branch_pattern=delivery.branch_pattern)
        for branch in ("develop", "main", "staging", "production", "master"):
            with self.subTest(branch=branch):
                self.assertFalse(
                    decide_git(["git", "push", "origin", branch], {},
                               str(self.project), scope).allowed,
                    f"delivery could push to {branch}")
        self.assertTrue(decide_git(["git", "push", "-u", "origin", "fix/retry"], {},
                                   str(self.project), scope).allowed)

    def test_a_feature_branch_is_cut_from_the_base(self) -> None:
        from coderai.delivery.policy import DeliveryScope, decide_git

        scope = DeliveryScope(project=str(self.project), remote="origin",
                              base="origin/develop",
                              branch_pattern="^(fix|feat|chore|docs|refactor|test)/[a-z0-9._-]+$")
        self.assertTrue(decide_git(["git", "switch", "-c", "feat/plans"], {},
                                   str(self.project), scope).allowed)
        self.assertTrue(decide_git(["git", "rebase", "origin/develop"], {},
                                   str(self.project), scope).allowed)
        self.assertFalse(decide_git(["git", "switch", "-c", "develop"], {},
                                    str(self.project), scope).allowed)

if __name__ == "__main__":
    unittest.main(verbosity=2)
