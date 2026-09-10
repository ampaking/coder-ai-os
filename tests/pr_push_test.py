"""Task 14 — every push in the audit log is provably a fast-forward of the PR head."""

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
from coderai.pr_automation.gitcmd import Git  # noqa: E402
from coderai.pr_automation.guard import install as guard_install  # noqa: E402
from coderai.pr_automation.push import (  # noqa: E402
    NO_CHANGE, NON_FAST_FORWARD, PushTransaction, TAG_CREATED, UNEXPECTED_REF,
    UNPUSHED_COMMITS, VERIFIED, capture_refs, verify,
)
from coderai.pr_automation.state import Session  # noqa: E402
from coderai.pr_automation.worktree import ensure_worktree  # noqa: E402

HEAD_BRANCH = "feature/worker-retry"


class Transactions(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.origin, self.clone = pr_repo.build(self.root)
        self.sessions = self.root / "session"
        self.sessions.mkdir()
        self.session = Session(host="github.com", owner="org", repo="aiila", number=1420,
                               head_branch=HEAD_BRANCH, base_branch="main", remote="origin",
                               provider="claude", provider_argv=["claude"])
        self.tree = ensure_worktree(self.session, self.clone, root=self.root / "wt")
        self.git = Git(self.tree.path)
        self.guard = guard_install.install(self.sessions, self.tree.path, remote="origin",
                                           head_branch=HEAD_BRANCH,
                                           local_branch=self.tree.branch)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def transaction(self) -> PushTransaction:
        return PushTransaction(self.git, "origin", HEAD_BRANCH,
                               directory=self.sessions).open()

    def agent(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        """Run a command the way the AI would: inside the guarded environment."""
        env = {**os.environ, **self.guard.env}
        return subprocess.run(list(args), cwd=str(cwd or self.tree.path), env=env,
                              capture_output=True, text=True, timeout=120)

    def commit(self, text: str = "fixed\n", message: str = "fix(worker): retry") -> str:
        (self.tree.path / "worker" / "retry.py").write_text(text, encoding="utf-8")
        self.agent("git", "add", "-A")
        self.agent("git", "commit", "-m", message)
        return self.git.head_sha() or ""

    def test_a_normal_push_is_verified_and_recorded(self) -> None:
        transaction = self.transaction()
        expected = self.commit()
        self.assertEqual(self.agent("git", "push").returncode, 0)
        result = transaction.close()
        self.assertEqual(result.status, VERIFIED)
        self.assertTrue(result.pushed)
        self.assertEqual(result.after_sha, expected)
        self.assertEqual(result.moved_refs, [f"refs/heads/{HEAD_BRANCH}"])
        self.assertEqual(result.unpushed, 0)
        pushes = [item for item in audit.read(self.sessions)
                  if item["event"] == "push_complete"]
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0]["commit"], expected)

    def test_no_change_when_the_ai_did_nothing(self) -> None:
        result = self.transaction().close()
        self.assertEqual(result.status, NO_CHANGE)
        self.assertTrue(result.ok)

    def test_local_commits_that_were_never_pushed_are_reported(self) -> None:
        transaction = self.transaction()
        self.commit()
        result = transaction.close()
        self.assertEqual(result.status, UNPUSHED_COMMITS)
        self.assertEqual(result.unpushed, 1)
        self.assertFalse(result.pushed)

    def test_a_concurrent_human_push_is_detected_as_non_fast_forward(self) -> None:
        """A human pushed while the AI worked; coder-ai-os must never force over it."""
        transaction = self.transaction()
        pr_repo.add_remote_commit(self.origin, self.root, HEAD_BRANCH, "human work")
        self.commit()
        # The agent's push is refused by git itself (non-fast-forward)…
        push = self.agent("git", "push")
        self.assertNotEqual(push.returncode, 0)
        # …and the force-push escape is refused by the guard.
        self.assertEqual(self.agent("git", "push", "--force").returncode, 13)
        result = transaction.close()
        self.assertEqual(result.status, NON_FAST_FORWARD)
        self.assertTrue(result.needs_human)
        failures = [item for item in audit.read(self.sessions)
                    if item["event"] == "push_verification_failed"]
        self.assertEqual(failures[-1]["status"], NON_FAST_FORWARD)

    def test_another_branch_moving_is_an_unexpected_ref(self) -> None:
        before = capture_refs(self.git, "origin")
        pr_repo.add_remote_commit(self.origin, self.root, "main", "someone touched main")
        result = verify(self.git, remote="origin", head_branch=HEAD_BRANCH, before=before,
                        directory=self.sessions)
        self.assertEqual(result.status, UNEXPECTED_REF)
        self.assertIn("refs/heads/main", result.moved_refs)
        self.assertTrue(result.needs_human)

    def test_a_new_tag_is_refused(self) -> None:
        before = capture_refs(self.git, "origin")
        scratch = self.root / "tagger"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(scratch)], check=True)
        pr_repo.git(scratch, "tag", "v9.9.9")
        pr_repo.git(scratch, "push", "-q", "origin", "v9.9.9")
        result = verify(self.git, remote="origin", head_branch=HEAD_BRANCH, before=before,
                        directory=self.sessions)
        self.assertEqual(result.status, TAG_CREATED)
        self.assertTrue(result.needs_human)

    def test_verification_never_pushes_anything_itself(self) -> None:
        transaction = self.transaction()
        self.commit()
        before_remote = pr_repo.git(self.origin, "rev-parse", HEAD_BRANCH)
        transaction.close()
        self.assertEqual(pr_repo.git(self.origin, "rev-parse", HEAD_BRANCH), before_remote)

    def test_a_failed_push_leaves_a_resumable_session(self) -> None:
        transaction = self.transaction()
        pr_repo.add_remote_commit(self.origin, self.root, HEAD_BRANCH, "human work")
        expected = self.commit()
        self.agent("git", "push")
        transaction.close()
        # The AI's commit still exists locally: the next wake can rebuild on it.
        self.assertEqual(self.git.head_sha(), expected)
        self.assertTrue(self.git.is_clean())

    def test_two_commits_push_as_one_fast_forward(self) -> None:
        transaction = self.transaction()
        self.commit("one\n", "fix(worker): prevent duplicate retry execution")
        expected = self.commit("two\n", "test(worker): cover retry timeout path")
        self.assertEqual(self.agent("git", "push").returncode, 0)
        result = transaction.close()
        self.assertEqual(result.status, VERIFIED)
        self.assertEqual(result.after_sha, expected)

    def test_the_guard_hook_does_not_constrain_the_engineers_checkout(self) -> None:
        """Worktrees share .git/hooks — the guard must not leak into the human's clone."""
        pr_repo.git(self.clone, "checkout", "-q", "main")
        (self.clone / "human.txt").write_text("human work\n")
        pr_repo.git(self.clone, "add", "-A")
        pr_repo.git(self.clone, "commit", "-qm", "chore: human work on main")
        result = subprocess.run(["git", "-C", str(self.clone), "push", "origin", "main"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         f"the guard leaked into the engineer's checkout: {result.stderr}")

    def test_unreadable_remote_is_named_not_assumed_clean(self) -> None:
        result = verify(Git(self.tree.path), remote="nosuchremote",
                        head_branch=HEAD_BRANCH, before={"refs/heads/x": "a"})
        self.assertEqual(result.status, "UNREADABLE")
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
