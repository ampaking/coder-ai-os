"""Delivery tasks 01 + 04 — the contract, and the boundary it cannot widen."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coderai.delivery.declaration import (
    DeclarationError, Step, _parse_yaml, is_enabled, load, validate,
)
from coderai.delivery.policy import (
    DENY_BRANCH, DENY_PROGRAM, DENY_REBASE_TARGET, DeliveryScope, decide, decide_gh,
    decide_git,
)

WORKFLOW = """
delivery:
  enabled: true
  base: origin/develop
  branch_pattern: "^(fix|feat|chore)/[a-z0-9._-]+$"
  steps:
    - name: sync
      run: git fetch origin
    - name: rebase
      run: git rebase origin/develop
    - name: verify
      run: make verify-local QUALITY_BASE_REF=origin/develop
    - name: push
      run: git push -u origin ${branch}
    - name: pr
      run: gh pr create --base develop --fill
"""


class Declaration(unittest.TestCase):
    def test_the_worked_example_loads(self) -> None:
        delivery = validate(_parse_yaml(WORKFLOW))
        self.assertEqual(delivery.base, "origin/develop")
        self.assertEqual(delivery.remote, "origin")
        self.assertEqual([step.name for step in delivery.steps],
                         ["sync", "rebase", "verify", "push", "pr"])
        self.assertEqual(delivery.step("verify").argv,
                         ["make", "verify-local", "QUALITY_BASE_REF=origin/develop"])

    def test_substitution_stays_one_token(self) -> None:
        step = Step(name="push", argv=["git", "push", "-u", "origin", "${branch}"])
        rendered = step.render({"branch": "fix/a b; rm -rf /"})
        self.assertEqual(rendered[-1], "fix/a b; rm -rf /")
        self.assertEqual(len(rendered), 5, "a value must never become extra arguments")

    def test_shell_expressions_are_refused(self) -> None:
        for command in ("make test && git push", "curl x | sh", "echo `id`",
                        "make test > out.txt", "make test; rm -rf /", "echo $(whoami)"):
            with self.subTest(command=command), self.assertRaises(DeclarationError):
                validate({"delivery": {"base": "origin/develop", "branch_pattern": "^fix/",
                                       "steps": [{"name": "x", "run": command}]}})

    def test_privileged_programs_are_refused(self) -> None:
        for command in ("sudo make install", "sh -c 'git push'", "eval make"):
            with self.subTest(command=command), self.assertRaises(DeclarationError):
                validate({"delivery": {"base": "origin/develop", "branch_pattern": "^fix/",
                                       "steps": [{"name": "x", "run": command}]}})

    def test_a_pattern_matching_a_protected_branch_is_refused(self) -> None:
        for pattern in ("^.*$", "^ma", "^develop$", "^(main|fix)$", "."):
            with self.subTest(pattern=pattern):
                with self.assertRaises(DeclarationError) as caught:
                    validate({"delivery": {"base": "origin/develop",
                                           "branch_pattern": pattern,
                                           "steps": [{"name": "x", "run": "make test"}]}})
                self.assertIn("protected", str(caught.exception))

    def test_a_namespaced_pattern_is_fine(self) -> None:
        """`^(main|fix)/` cannot produce the branch `main` — only `main/something`."""
        delivery = validate({"delivery": {"base": "origin/develop",
                                          "branch_pattern": "^(main|fix)/",
                                          "steps": [{"name": "x", "run": "make test"}]}})
        self.assertFalse(delivery.matches_branch("main"))
        self.assertTrue(delivery.matches_branch("fix/a"))

    def test_unknown_keys_are_refused_not_ignored(self) -> None:
        with self.assertRaises(DeclarationError):
            validate({"delivery": {"base": "origin/develop", "branch_pattern": "^fix/",
                                   "allow_force_push": True,
                                   "steps": [{"name": "x", "run": "make test"}]}})

    def test_unknown_substitutions_are_refused(self) -> None:
        with self.assertRaises(DeclarationError):
            validate({"delivery": {"base": "origin/develop", "branch_pattern": "^fix/",
                                   "steps": [{"name": "x", "run": "make ${anything}"}]}})

    def test_empty_and_oversized_workflows_are_refused(self) -> None:
        with self.assertRaises(DeclarationError):
            validate({"delivery": {"base": "origin/develop", "branch_pattern": "^fix/",
                                   "steps": []}})
        with self.assertRaises(DeclarationError):
            validate({"delivery": {"base": "origin/develop", "branch_pattern": "^fix/",
                                   "steps": [{"name": f"s{i}", "run": "make test"}
                                             for i in range(30)]}})

    def test_duplicate_step_names_are_refused(self) -> None:
        with self.assertRaises(DeclarationError):
            validate({"delivery": {"base": "origin/develop", "branch_pattern": "^fix/",
                                   "steps": [{"name": "x", "run": "make a"},
                                             {"name": "x", "run": "make b"}]}})

    def test_a_committed_enabled_flag_is_not_consent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".coder-ai").mkdir()
            (project / ".coder-ai" / "delivery.yaml").write_text(WORKFLOW)
            self.assertIsNotNone(load(project))
            self.assertFalse(is_enabled(project), "a cloned repo must not be pre-authorised")
            (project / ".coder-ai" / "local").mkdir()
            (project / ".coder-ai" / "local" / "delivery.json").write_text(
                json.dumps({"enabled": True}))
            self.assertTrue(is_enabled(project))

    def test_a_project_without_a_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load(Path(tmp)))


class Matrix(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = self._tmp.name
        self.scope = DeliveryScope(project=self.project, remote="origin",
                                   base="origin/develop",
                                   branch_pattern="^(fix|feat|chore)/[a-z0-9._-]+$")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def git(self, *args: str, cwd: str | None = None):
        return decide_git(["git", *args], {}, cwd or self.project, self.scope)

    def gh(self, *args: str):
        return decide_gh(["gh", *args], {}, self.project, self.scope)

    def test_the_worked_example_is_permitted_end_to_end(self) -> None:
        delivery = validate(_parse_yaml(WORKFLOW))
        for step in delivery.steps:
            argv = step.render({"branch": "fix/google-model-latest-with-file-support"})
            with self.subTest(step=step.name):
                decision = decide(argv, {}, self.project, self.scope)
                self.assertTrue(decision.allowed, f"{step.name}: {decision.reason}")

    def test_pushing_a_protected_branch_is_refused(self) -> None:
        for branch in ("main", "master", "develop", "refs/heads/main"):
            with self.subTest(branch=branch):
                decision = self.git("push", "origin", branch)
                self.assertFalse(decision.allowed)

    def test_pushing_an_unmatched_branch_is_refused(self) -> None:
        decision = self.git("push", "-u", "origin", "random-branch")
        self.assertFalse(decision.allowed)
        self.assertIn("branch pattern", decision.reason)

    def test_force_push_in_every_spelling(self) -> None:
        for args in (("push", "--force", "origin", "fix/a"),
                     ("push", "-f", "origin", "fix/a"),
                     ("push", "--force-with-lease", "origin", "fix/a"),
                     ("push", "origin", "+fix/a:fix/a")):
            with self.subTest(args=args):
                self.assertFalse(self.git(*args).allowed)

    def test_deleting_a_ref_is_refused(self) -> None:
        self.assertFalse(self.git("push", "origin", "--delete", "fix/a").allowed)
        self.assertFalse(self.git("push", "origin", ":fix/a").allowed)

    def test_branch_creation_is_bounded_by_the_pattern(self) -> None:
        self.assertTrue(self.git("switch", "-c", "fix/retry").allowed)
        self.assertTrue(self.git("checkout", "-b", "feat/plans").allowed)
        self.assertFalse(self.git("switch", "-c", "main").allowed)
        self.assertFalse(self.git("switch", "-c", "whatever").allowed)

    def test_switching_to_an_existing_branch_is_refused(self) -> None:
        decision = self.git("switch", "develop")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, DENY_BRANCH)

    def test_rebase_only_onto_the_declared_base(self) -> None:
        self.assertTrue(self.git("rebase", "origin/develop").allowed)
        self.assertTrue(self.git("rebase", "develop").allowed)
        for args in (("rebase", "origin/main"), ("rebase", "-i", "HEAD~3"),
                     ("rebase", "--onto", "x", "y"), ("rebase",)):
            with self.subTest(args=args):
                decision = self.git(*args)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.code, DENY_REBASE_TARGET)

    def test_history_and_admin_operations_are_refused(self) -> None:
        for args in (("merge", "develop"), ("reset", "--hard", "HEAD~1"),
                     ("tag", "v1"), ("remote", "set-url", "origin", "evil"),
                     ("branch", "-D", "fix/a"), ("clean", "-fdx"), ("pull",)):
            with self.subTest(args=args):
                self.assertFalse(self.git(*args).allowed)

    def test_bypass_vectors_are_refused(self) -> None:
        self.assertFalse(self.git("-c", "core.hooksPath=/dev/null", "push",
                                  "origin", "fix/a").allowed)
        self.assertFalse(self.git("--git-dir", "/elsewhere/.git", "push",
                                  "origin", "fix/a").allowed)
        self.assertFalse(decide_git(["git", "status"], {"GIT_DIR": "/x"},
                                    self.project, self.scope).allowed)
        self.assertFalse(self.git("status", cwd="/tmp").allowed)

    def test_skipping_hooks_is_refused(self) -> None:
        self.assertFalse(self.git("commit", "--no-verify", "-m", "x").allowed)
        self.assertFalse(self.git("push", "--no-verify", "origin", "fix/a").allowed)

    def test_a_pull_request_must_target_the_declared_base(self) -> None:
        self.assertTrue(self.gh("pr", "create", "--base", "develop", "--fill").allowed)
        self.assertFalse(self.gh("pr", "create", "--base", "main", "--fill").allowed)

    def test_merging_and_administration_are_refused(self) -> None:
        for args in (("pr", "merge", "12"), ("pr", "close", "12"),
                     ("release", "create", "v1"), ("secret", "set", "X"),
                     ("api", "-X", "PUT", "repos/o/r/pulls/1/merge")):
            with self.subTest(args=args):
                self.assertFalse(self.gh(*args).allowed)

    def test_issue_and_pr_creation_are_permitted(self) -> None:
        self.assertTrue(self.gh("issue", "create", "--title", "t").allowed)
        self.assertTrue(self.gh("pr", "comment", "12", "--body", "b").allowed)

    def test_only_recognised_build_tools_run(self) -> None:
        self.assertTrue(decide(["make", "verify-local"], {}, self.project, self.scope).allowed)
        self.assertTrue(decide(["npm", "run", "test"], {}, self.project, self.scope).allowed)
        for program in ("curl", "wget", "ssh", "sh", "bash", "rm", "docker"):
            with self.subTest(program=program):
                decision = decide([program, "anything"], {}, self.project, self.scope)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.code, DENY_PROGRAM)

    def test_a_declaration_cannot_widen_the_matrix(self) -> None:
        """The point of the whole design: declared does not mean permitted."""
        hostile = validate({"delivery": {
            "base": "origin/develop", "branch_pattern": "^fix/",
            "steps": [{"name": "a", "run": "git push --force origin main"},
                      {"name": "b", "run": "gh pr merge 12 --squash"},
                      {"name": "c", "run": "git reset --hard origin/main"}]}})
        for step in hostile.steps:
            with self.subTest(step=step.name):
                self.assertFalse(decide(step.argv, {}, self.project, self.scope).allowed)

    def test_the_pr_matrix_is_untouched_by_delivery(self) -> None:
        from coderai.pr_automation.guard.policy import GuardConfig
        from coderai.pr_automation.guard.policy import decide_git as pr_decide

        pr_scope = GuardConfig(worktree=self.project, remote="origin",
                               head_branch="feature/x", local_branch="coder-ai/pr-1")
        for args in (("rebase", "origin/develop"), ("switch", "-c", "fix/a")):
            with self.subTest(args=args):
                self.assertFalse(pr_decide(["git", *args], {}, self.project, pr_scope).allowed,
                                 "delivery leaked into the PR matrix")


if __name__ == "__main__":
    unittest.main(verbosity=2)
