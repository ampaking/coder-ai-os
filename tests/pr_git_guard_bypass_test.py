"""Task 05 — the guard against a real git: every escape actually refused.

The policy tests prove the decision function. This proves the installed boundary:
real shims on PATH, a real pre-push hook, a real remote — and a real attempt to
push main, force-push, delete a ref, and bypass the shim by absolute path.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import pr_repo  # noqa: E402

from coderai.pr_automation import audit  # noqa: E402
from coderai.pr_automation.guard import install as guard_install  # noqa: E402
from coderai.pr_automation.state import Session  # noqa: E402
from coderai.pr_automation.worktree import ensure_worktree  # noqa: E402

HEAD_BRANCH = "feature/worker-retry"
DENY_EXIT = 13


class GuardedEnvironment(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.origin, self.clone = pr_repo.build(self.root)
        self.trees = self.root / "worktrees"
        self.sessions = self.root / "session"
        self.sessions.mkdir()
        self.session = Session(host="github.com", owner="org", repo="aiila", number=1420,
                               head_branch=HEAD_BRANCH, base_branch="main", remote="origin",
                               provider="claude", provider_argv=["claude"])
        self.tree = ensure_worktree(self.session, self.clone, root=self.trees)
        self.guard = guard_install.install(
            self.sessions, self.tree.path, remote="origin", head_branch=HEAD_BRANCH,
            local_branch=self.tree.branch,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_guarded(self, *args: str, cwd: Path | None = None,
                    env_extra: dict | None = None) -> subprocess.CompletedProcess:
        """Run a command exactly as the AI would: guard shims first on PATH."""
        env = {**os.environ, **self.guard.env, **(env_extra or {})}
        return subprocess.run(list(args), cwd=str(cwd or self.tree.path), env=env,
                              capture_output=True, text=True, timeout=120)

    def commit_a_change(self, text: str = "guarded change\n") -> None:
        (self.tree.path / "worker" / "retry.py").write_text(text, encoding="utf-8")
        self.assertEqual(self.run_guarded("git", "add", "-A").returncode, 0)
        result = self.run_guarded("git", "commit", "-m", "fix(worker): guarded change")
        self.assertEqual(result.returncode, 0, result.stderr)

    def origin_sha(self, ref: str) -> str:
        return pr_repo.git(self.origin, "rev-parse", ref)


class AllowedThroughTheShim(GuardedEnvironment):
    def test_shim_is_first_on_path(self) -> None:
        result = self.run_guarded("sh", "-c", "command -v git")
        self.assertEqual(result.stdout.strip(), str(self.guard.bin_dir / "git"))

    def test_engineering_workflow_succeeds(self) -> None:
        self.assertEqual(self.run_guarded("git", "status").returncode, 0)
        self.commit_a_change()
        before_main = self.origin_sha("main")
        push = self.run_guarded("git", "push")
        self.assertEqual(push.returncode, 0, push.stderr)
        self.assertEqual(self.origin_sha(HEAD_BRANCH),
                         pr_repo.git(self.tree.path, "rev-parse", "HEAD"))
        self.assertEqual(self.origin_sha("main"), before_main)

    def test_explicit_refspec_push_succeeds(self) -> None:
        self.commit_a_change()
        push = self.run_guarded("git", "push", "origin",
                                f"{self.tree.branch}:{HEAD_BRANCH}")
        self.assertEqual(push.returncode, 0, push.stderr)


class RefusedThroughTheShim(GuardedEnvironment):
    def assert_refused(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, DENY_EXIT,
                         f"BYPASS — command succeeded:\n{result.stdout}\n{result.stderr}")
        self.assertIn("coder-ai guard: refused", result.stderr)

    def test_push_main(self) -> None:
        before = self.origin_sha("main")
        self.assert_refused(self.run_guarded("git", "push", "origin", "HEAD:main"))
        self.assertEqual(self.origin_sha("main"), before)

    def test_force_push(self) -> None:
        self.commit_a_change()
        self.assert_refused(self.run_guarded("git", "push", "--force"))
        self.assert_refused(self.run_guarded("git", "push", "--force-with-lease"))

    def test_delete_the_pr_branch(self) -> None:
        self.assert_refused(self.run_guarded("git", "push", "origin", "--delete", HEAD_BRANCH))
        self.assertTrue(self.origin_sha(HEAD_BRANCH))

    def test_merge_rebase_reset_checkout(self) -> None:
        for args in (("git", "merge", "main"), ("git", "rebase", "main"),
                     ("git", "reset", "--hard", "HEAD~1"), ("git", "checkout", "main"),
                     ("git", "branch", "-D", "main"), ("git", "tag", "v9")):
            with self.subTest(args=args):
                self.assert_refused(self.run_guarded(*args))

    def test_inline_config_and_relocation(self) -> None:
        for args in (("git", "-c", "core.hooksPath=/dev/null", "push", "--force"),
                     ("git", "--git-dir", str(self.clone / ".git"), "push", "origin", "main"),
                     ("git", "-C", str(self.clone), "push", "origin", "main")):
            with self.subTest(args=args):
                self.assert_refused(self.run_guarded(*args))

    def test_environment_relocation(self) -> None:
        result = self.run_guarded("git", "status",
                                  env_extra={"GIT_DIR": str(self.clone / ".git")})
        self.assert_refused(result)

    def test_running_git_in_the_engineers_checkout(self) -> None:
        result = self.run_guarded("git", "status", cwd=self.clone)
        self.assert_refused(result)

    def test_shell_indirection_does_not_help(self) -> None:
        result = self.run_guarded("sh", "-c", "git push origin HEAD:main")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("coder-ai guard: refused", result.stderr)

    def test_denials_are_recorded_with_their_code(self) -> None:
        self.run_guarded("git", "push", "origin", "HEAD:main")
        denials = [item for item in audit.read(self.sessions) if item["event"] == "guard_deny"]
        self.assertTrue(denials)
        self.assertEqual(denials[-1]["code"], "DENY_PUSH_REF")
        self.assertIn("push", denials[-1]["command"])

    def test_guard_without_scope_refuses_everything(self) -> None:
        env = {**os.environ, **self.guard.env}
        env.pop("CODER_AI_GUARD_CONFIG")
        result = subprocess.run(["git", "status"], cwd=str(self.tree.path), env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, DENY_EXIT)
        self.assertIn("no PR-automation scope", result.stderr)


class HookCatchesPathBypass(GuardedEnvironment):
    """Layer 2: a git invoked by absolute path never sees the PATH shim."""

    def setUp(self) -> None:
        super().setUp()
        self.real_git = self.guard.env["CODER_AI_REAL_GIT"]

    def run_real_git(self, *args: str) -> subprocess.CompletedProcess:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("CODER_AI_")}
        return subprocess.run([self.real_git, *args], cwd=str(self.tree.path), env=env,
                              capture_output=True, text=True, timeout=120)

    def test_hook_is_installed(self) -> None:
        self.assertIsNotNone(self.guard.hook_path)
        assert self.guard.hook_path is not None
        self.assertTrue(self.guard.hook_path.is_file())
        self.assertTrue(os.access(self.guard.hook_path, os.X_OK))

    def test_absolute_path_git_cannot_push_main(self) -> None:
        before = self.origin_sha("main")
        result = self.run_real_git("push", "origin", "HEAD:main")
        self.assertNotEqual(result.returncode, 0,
                            f"BYPASS — pushed main:\n{result.stdout}{result.stderr}")
        self.assertIn("coder-ai guard: refused push", result.stderr)
        self.assertEqual(self.origin_sha("main"), before)

    def test_absolute_path_git_cannot_delete_the_branch(self) -> None:
        result = self.run_real_git("push", "origin", "--delete", HEAD_BRANCH)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.origin_sha(HEAD_BRANCH))

    def test_absolute_path_git_cannot_force_push_a_rewrite(self) -> None:
        pr_repo.add_remote_commit(self.origin, self.root, HEAD_BRANCH, "human work")
        remote_sha = self.origin_sha(HEAD_BRANCH)
        # Local history now diverges from the remote: only a force push could land it.
        (self.tree.path / "worker" / "retry.py").write_text("rewrite\n")
        pr_repo.git(self.tree.path, "add", "-A")
        pr_repo.git(self.tree.path, "commit", "-qm", "rewrite")
        result = self.run_real_git("push", "--force", "origin",
                                   f"HEAD:refs/heads/{HEAD_BRANCH}")
        self.assertNotEqual(result.returncode, 0,
                            f"BYPASS — force pushed:\n{result.stdout}{result.stderr}")
        self.assertEqual(self.origin_sha(HEAD_BRANCH), remote_sha)

    def test_allowed_fast_forward_still_works_through_the_hook(self) -> None:
        (self.tree.path / "worker" / "retry.py").write_text("ff\n")
        pr_repo.git(self.tree.path, "add", "-A")
        pr_repo.git(self.tree.path, "commit", "-qm", "ff")
        result = self.run_real_git("push", "origin", f"HEAD:refs/heads/{HEAD_BRANCH}")
        self.assertEqual(result.returncode, 0, result.stderr)


class Uninstall(GuardedEnvironment):
    def test_removing_the_guard_removes_every_artifact(self) -> None:
        guard_install.uninstall(self.guard, self.tree.path)
        self.assertFalse(self.guard.bin_dir.exists())
        self.assertFalse(self.guard.config_path.exists())
        assert self.guard.hook_path is not None
        self.assertFalse(self.guard.hook_path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
