"""Task 05 — the guard matrix. Pure decisions, exhaustively enumerated."""

from __future__ import annotations

import unittest
from pathlib import Path

from coderai.pr_automation.guard.policy import (
    ALLOW, DENY_CONFIG_WRITE, DENY_CROSS_REPO_PUSH, DENY_DELETE_REF, DENY_ENV,
    DENY_FETCH_REFSPEC, DENY_FORCE_PUSH, DENY_GH_ENDPOINT, DENY_GH_SUBCOMMAND,
    DENY_GLOBAL_OPTION, DENY_NO_VERIFY, DENY_OUTSIDE_WORKTREE, DENY_PUSH_ALL,
    DENY_PUSH_REF, DENY_PUSH_REMOTE, DENY_PUSH_TAGS, DENY_SUBCOMMAND, GuardConfig,
    decide, decide_gh, decide_git,
)

WORKTREE = "/tmp/coder-ai/pr-1420"
CONFIG = GuardConfig(worktree=WORKTREE, remote="origin", head_branch="feature/worker-retry",
                     local_branch="coder-ai/pr-1420", session_dir="", allow_push=True)


def git(*args: str, env=None, cwd: str = WORKTREE, config: GuardConfig = CONFIG):
    return decide_git(["git", *args], env or {}, cwd, config)


def gh(*args: str, env=None, cwd: str = WORKTREE, config: GuardConfig = CONFIG):
    return decide_gh(["gh", *args], env or {}, cwd, config)


class AllowedWork(unittest.TestCase):
    """An autonomous PR engineer must be able to do its actual job."""

    def test_inspection_and_editing(self) -> None:
        for args in (("status",), ("status", "--porcelain"), ("diff",), ("diff", "--staged"),
                     ("log", "--oneline", "-20"), ("show", "HEAD"), ("blame", "worker/retry.py"),
                     ("rev-parse", "HEAD"), ("ls-files",), ("grep", "retry"),
                     ("add", "-A"), ("add", "worker/retry.py"), ("rm", "old.py"),
                     ("mv", "a.py", "b.py"), ("restore", "worker/retry.py"),
                     ("merge-base", "--is-ancestor", "a", "b")):
            with self.subTest(args=args):
                self.assertTrue(git(*args), f"{args} should be allowed")

    def test_commit(self) -> None:
        self.assertTrue(git("commit", "-m", "fix(worker): prevent duplicate retry execution"))
        self.assertTrue(git("commit", "--amend", "-m", "fix(worker): retry"))

    def test_push_forms_that_reach_exactly_the_pr_head(self) -> None:
        for args in (("push",), ("push", "origin"), ("push", "origin", "coder-ai/pr-1420"),
                     ("push", "origin", "coder-ai/pr-1420:feature/worker-retry"),
                     ("push", "origin", "HEAD:feature/worker-retry"),
                     ("push", "origin", "HEAD:refs/heads/feature/worker-retry"),
                     ("push", "-u", "origin", "coder-ai/pr-1420"),
                     ("push", "--quiet"), ("push", "--dry-run")):
            with self.subTest(args=args):
                decision = git(*args)
                self.assertTrue(decision, f"{args} rejected: {decision.reason}")
                self.assertEqual(decision.code, ALLOW)

    def test_fetch_and_read_only_config(self) -> None:
        self.assertTrue(git("fetch", "origin"))
        self.assertTrue(git("config", "--get", "push.default"))
        self.assertTrue(git("config", "--list"))


class DeniedGitOperations(unittest.TestCase):
    def assert_denied(self, decision, code: str) -> None:
        self.assertFalse(decision.allowed, f"expected denial, got ALLOW: {decision}")
        self.assertEqual(decision.code, code)
        self.assertTrue(decision.reason, "a denial must explain itself")

    def test_push_to_main_and_other_branches(self) -> None:
        for ref in ("main", "master", "refs/heads/main", "develop",
                    "coder-ai/pr-1420:main", "HEAD:main", "HEAD:refs/heads/main",
                    "coder-ai/pr-1420:refs/heads/release"):
            with self.subTest(ref=ref):
                self.assert_denied(git("push", "origin", ref), DENY_PUSH_REF)

    def test_force_push_every_spelling(self) -> None:
        for args in (("push", "--force"), ("push", "-f"), ("push", "--force-with-lease"),
                     ("push", "--force-if-includes"),
                     ("push", "--force-with-lease=feature/worker-retry"),
                     ("push", "origin", "+coder-ai/pr-1420:feature/worker-retry")):
            with self.subTest(args=args):
                self.assert_denied(git(*args), DENY_FORCE_PUSH)

    def test_branch_deletion(self) -> None:
        for args in (("push", "origin", "--delete", "feature/worker-retry"),
                     ("push", "origin", ":feature/worker-retry"),
                     ("push", "-d", "origin", "x"),
                     ("push", "origin", "coder-ai/pr-1420:")):
            with self.subTest(args=args):
                self.assertFalse(git(*args).allowed)
                self.assertIn(git(*args).code, {DENY_DELETE_REF, DENY_PUSH_REF})

    def test_push_everything(self) -> None:
        for args, code in ((("push", "--mirror"), DENY_PUSH_ALL),
                           (("push", "--all"), DENY_PUSH_ALL),
                           (("push", "--tags"), DENY_PUSH_TAGS),
                           (("push", "--follow-tags"), DENY_PUSH_TAGS)):
            with self.subTest(args=args):
                self.assert_denied(git(*args), code)

    def test_push_to_another_remote(self) -> None:
        self.assert_denied(git("push", "upstream", "coder-ai/pr-1420"), DENY_PUSH_REMOTE)
        self.assert_denied(git("push", "https://github.com/other/repo.git", "HEAD:main"),
                           DENY_PUSH_REMOTE)

    def test_history_and_merge_operations(self) -> None:
        for args in (("merge", "main"), ("rebase", "-i", "HEAD~3"), ("reset", "--hard", "HEAD~1"),
                     ("reset", "--soft", "HEAD~1"), ("cherry-pick", "abc123"),
                     ("revert", "abc123"), ("filter-branch", "--all"), ("update-ref",
                     "refs/heads/main", "abc"), ("pull",), ("pull", "--rebase")):
            with self.subTest(args=args):
                self.assert_denied(git(*args), DENY_SUBCOMMAND)

    def test_branch_tag_and_remote_management(self) -> None:
        for args in (("branch", "-D", "main"), ("branch", "new"), ("tag", "v1.0"),
                     ("tag", "-d", "v1.0"), ("remote", "set-url", "origin", "evil"),
                     ("worktree", "add", "/tmp/x"), ("clean", "-fdx"),
                     ("checkout", "main"), ("switch", "main"), ("submodule", "update"),
                     ("credential", "fill"), ("daemon",), ("clone", "x"), ("init",)):
            with self.subTest(args=args):
                self.assert_denied(git(*args), DENY_SUBCOMMAND)

    def test_skipping_hooks_is_refused(self) -> None:
        self.assert_denied(git("commit", "--no-verify", "-m", "x"), DENY_NO_VERIFY)
        self.assert_denied(git("push", "--no-verify"), DENY_NO_VERIFY)

    def test_config_writes(self) -> None:
        for args in (("config", "remote.origin.url", "evil"),
                     ("config", "--global", "user.email", "x"),
                     ("config", "core.hooksPath", "/dev/null"),
                     ("config", "--unset", "push.default")):
            with self.subTest(args=args):
                self.assert_denied(git(*args), DENY_CONFIG_WRITE)

    def test_fetch_refspecs_that_write_local_refs(self) -> None:
        self.assert_denied(git("fetch", "origin", "main:refs/heads/main"), DENY_FETCH_REFSPEC)

    def test_unknown_subcommand_is_denied_by_default(self) -> None:
        self.assert_denied(git("frobnicate"), DENY_SUBCOMMAND)


class BypassVectors(unittest.TestCase):
    """Every known escape from the boundary, enumerated and refused."""

    def assert_denied(self, decision, code: str | None = None) -> None:
        self.assertFalse(decision.allowed, f"BYPASS: {decision}")
        if code:
            self.assertEqual(decision.code, code)

    def test_inline_configuration(self) -> None:
        for args in (("-c", "core.hooksPath=/dev/null", "push", "--force"),
                     ("-c", "remote.origin.url=evil", "push"),
                     ("--config-env=core.hooksPath=X", "push"),
                     ("-c", "alias.p=push --force", "p")):
            with self.subTest(args=args):
                self.assert_denied(git(*args), DENY_GLOBAL_OPTION)

    def test_relocating_git(self) -> None:
        for args in (("--git-dir", "/elsewhere/.git", "push", "origin", "main"),
                     ("--git-dir=/elsewhere/.git", "status"),
                     ("--work-tree", "/elsewhere", "add", "-A"),
                     ("-C", "/elsewhere", "push", "origin", "main"),
                     ("--exec-path=/tmp/evil", "push"),
                     ("--namespace", "x", "push"),
                     ("--bare", "push")):
            with self.subTest(args=args):
                self.assert_denied(git(*args), DENY_GLOBAL_OPTION)

    def test_environment_relocation(self) -> None:
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
                     "GIT_CONFIG_COUNT", "GIT_SSH_COMMAND", "GIT_OBJECT_DIRECTORY",
                     "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE",
                     "GIT_PROXY_COMMAND", "GIT_EXTERNAL_DIFF", "GIT_EXEC_PATH",
                     "GIT_TEMPLATE_DIR", "GIT_NAMESPACE", "GIT_ALLOW_PROTOCOL"):
            with self.subTest(name=name):
                self.assert_denied(git("status", env={name: "/evil"}), DENY_ENV)

    def test_running_outside_the_worktree(self) -> None:
        for cwd in ("/", "/tmp", "/home/engineer/work/aiila", "/tmp/coder-ai", str(Path.home())):
            with self.subTest(cwd=cwd):
                self.assert_denied(git("status", cwd=cwd), DENY_OUTSIDE_WORKTREE)

    def test_a_subdirectory_of_the_worktree_is_fine(self) -> None:
        self.assertTrue(git("status", cwd=WORKTREE + "/worker"))

    def test_sibling_directory_with_a_shared_prefix_is_not_inside(self) -> None:
        self.assert_denied(git("status", cwd="/tmp/coder-ai/pr-14200"), DENY_OUTSIDE_WORKTREE)

    def test_program_name_routing_cannot_be_spoofed(self) -> None:
        # An absolute path still routes on the basename.
        self.assertFalse(decide(["/usr/bin/git", "push", "--force"], {}, WORKTREE, CONFIG))
        self.assertFalse(decide(["/opt/homebrew/bin/gh", "pr", "merge", "1420"], {},
                                WORKTREE, CONFIG))

    def test_cross_repository_pr_cannot_push(self) -> None:
        fork = GuardConfig(worktree=WORKTREE, remote="origin", head_branch="feature/x",
                           local_branch="coder-ai/pr-1420", allow_push=False)
        self.assert_denied(git("push", config=fork), DENY_CROSS_REPO_PUSH)
        self.assertTrue(git("commit", "-m", "x", config=fork))  # still allowed to work


class GhBoundary(unittest.TestCase):
    def test_reading_is_allowed(self) -> None:
        for args in (("pr", "view", "1420"), ("pr", "diff", "1420"), ("pr", "checks"),
                     ("issue", "view", "99"), ("run", "view", "123", "--log-failed"),
                     ("api", "repos/org/aiila/pulls/1420/comments")):
            with self.subTest(args=args):
                self.assertTrue(gh(*args), f"{args} should be allowed")

    def test_replying_is_allowed(self) -> None:
        self.assertTrue(gh("pr", "comment", "1420", "--body", "checked, fixed in 91ad773"))
        self.assertTrue(gh("pr", "review", "1420", "--comment", "--body", "replied"))
        self.assertTrue(gh("api", "-X", "POST",
                           "repos/org/aiila/pulls/1420/comments", "-f", "body=x"))
        self.assertTrue(gh("api", "--method", "POST",
                           "repos/org/aiila/issues/1420/comments", "-f", "body=x"))

    def test_merging_and_administration_are_refused(self) -> None:
        for args in (("pr", "merge", "1420", "--squash"), ("pr", "close", "1420"),
                     ("pr", "edit", "1420", "--base", "main"), ("release", "create", "v1"),
                     ("secret", "set", "TOKEN"), ("workflow", "run", "deploy"),
                     ("repo", "delete", "org/aiila"), ("ruleset", "check"),
                     ("auth", "token")):
            with self.subTest(args=args):
                decision = gh(*args)
                self.assertFalse(decision.allowed, f"BYPASS: {args}")
                self.assertEqual(decision.code, DENY_GH_SUBCOMMAND)

    def test_api_writes_outside_pr_discussion_are_refused(self) -> None:
        for args in (("api", "-X", "PUT", "repos/org/aiila/pulls/1420/merge"),
                     ("api", "-X", "DELETE", "repos/org/aiila/git/refs/heads/main"),
                     ("api", "-X", "PATCH", "repos/org/aiila"),
                     ("api", "-X", "POST", "repos/org/aiila/git/refs"),
                     ("api", "-X", "PUT", "repos/org/aiila/branches/main/protection")):
            with self.subTest(args=args):
                self.assert_refused(gh(*args))

    def test_field_flags_imply_a_write(self) -> None:
        self.assert_refused(gh("api", "repos/org/aiila/pulls/1420/merge", "-f", "x=1"))

    def test_approving_is_a_humans_act(self) -> None:
        self.assertFalse(gh("pr", "review", "1420", "--approve").allowed)
        self.assertFalse(gh("pr", "review", "1420", "--request-changes", "-b", "x").allowed)

    def test_gh_outside_the_worktree(self) -> None:
        self.assertFalse(gh("pr", "view", "1420", cwd="/tmp").allowed)

    def assert_refused(self, decision) -> None:
        self.assertFalse(decision.allowed, f"BYPASS: {decision}")
        self.assertEqual(decision.code, DENY_GH_ENDPOINT)


class Purity(unittest.TestCase):
    def test_decisions_do_not_spawn_processes(self) -> None:
        import subprocess
        from unittest import mock

        with mock.patch.object(subprocess, "run", side_effect=AssertionError("spawned!")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("spawned!")):
            self.assertTrue(git("status"))
            self.assertFalse(git("push", "--force").allowed)
            self.assertTrue(gh("pr", "view", "1"))

    def test_every_denial_names_a_stable_code(self) -> None:
        decision = git("push", "origin", "main")
        self.assertTrue(decision.code.startswith("DENY_"))
        self.assertNotEqual(decision.reason, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
