"""Task 04 — isolated PR worktree: the engineer's checkout is never touched."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import pr_repo  # noqa: E402

from coderai.pr_automation import audit  # noqa: E402
from coderai.pr_automation.gitcmd import Git  # noqa: E402
from coderai.pr_automation.state import Session, StateError  # noqa: E402
from coderai.pr_automation.worktree import (  # noqa: E402
    CREATED, KEPT_DIRTY, RECREATED, REMOVED, REUSED, WorktreeError, ensure_worktree,
    local_branch, refresh_to_head, release_worktree, worktree_path,
)

HEAD_BRANCH = "feature/worker-retry"


def a_session(**kwargs) -> Session:
    values = dict(host="github.com", owner="org", repo="aiila", number=1420,
                  head_branch=HEAD_BRANCH, base_branch="main", remote="origin",
                  provider="claude", provider_argv=["claude"])
    values.update(kwargs)
    return Session(**values)


class WorktreeLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.origin, self.clone = pr_repo.build(self.root)
        self.trees = self.root / "worktrees"
        self.session = a_session()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def ensure(self, **kwargs):
        return ensure_worktree(self.session, self.clone, root=self.trees, **kwargs)

    def test_creates_an_isolated_workspace_on_the_pr_head(self) -> None:
        tree = self.ensure()
        self.assertEqual(tree.status, CREATED)
        self.assertEqual(tree.branch, "coder-ai/pr-1420")
        self.assertTrue(tree.path.is_dir())
        self.assertEqual(tree.path, self.trees / "org" / "aiila" / "pr-1420")
        # It really is the PR head content, not the base branch.
        self.assertIn("PR change", (tree.path / "worker" / "retry.py").read_text())
        remote_sha = pr_repo.git(self.clone, "rev-parse", f"origin/{HEAD_BRANCH}")
        self.assertEqual(tree.sha, remote_sha)

    def test_human_checkout_is_untouched(self) -> None:
        before_head = pr_repo.git(self.clone, "rev-parse", "HEAD")
        before_branch = pr_repo.git(self.clone, "rev-parse", "--abbrev-ref", "HEAD")
        before_status = pr_repo.git(self.clone, "status", "--porcelain")
        self.ensure()
        self.assertEqual(pr_repo.git(self.clone, "rev-parse", "HEAD"), before_head)
        self.assertEqual(pr_repo.git(self.clone, "rev-parse", "--abbrev-ref", "HEAD"),
                         before_branch)
        self.assertEqual(pr_repo.git(self.clone, "status", "--porcelain"), before_status)

    def test_works_while_the_human_has_the_pr_branch_checked_out(self) -> None:
        """The engineer may be sitting on the very branch the PR uses."""
        pr_repo.git(self.clone, "checkout", "-q", "-b", HEAD_BRANCH,
                    f"origin/{HEAD_BRANCH}")
        tree = self.ensure()
        self.assertTrue(tree.path.is_dir())
        self.assertEqual(pr_repo.git(self.clone, "rev-parse", "--abbrev-ref", "HEAD"),
                         HEAD_BRANCH)

    def test_human_can_switch_branches_during_a_session(self) -> None:
        tree = self.ensure()
        pr_repo.git(self.clone, "checkout", "-q", "-b", "unrelated/work")
        (self.clone / "scratch.txt").write_text("human work\n")
        git = Git(tree.path)
        self.assertEqual(git.current_branch(), "coder-ai/pr-1420")
        self.assertIn("PR change", (tree.path / "worker" / "retry.py").read_text())

    def test_reuse_is_idempotent(self) -> None:
        first = self.ensure()
        second = self.ensure()
        self.assertEqual(second.status, REUSED)
        self.assertEqual(first.path, second.path)

    def test_stale_worktree_from_a_killed_run_is_recovered(self) -> None:
        tree = self.ensure()
        # Simulate a killed run: the directory survives but its git link is broken.
        (tree.path / ".git").unlink()
        (tree.path / ".git").write_text("gitdir: /nowhere/at/all\n")
        recovered = self.ensure()
        self.assertEqual(recovered.status, RECREATED)
        self.assertEqual(Git(recovered.path).current_branch(), "coder-ai/pr-1420")
        listing = pr_repo.git(self.clone, "worktree", "list", "--porcelain")
        self.assertEqual(listing.count("worktree "), 2)  # main + one PR worktree

    def test_push_default_is_scoped_to_the_pr_head(self) -> None:
        tree = self.ensure()
        git = Git(tree.path)
        self.assertEqual(git.text("config", "--get", "push.default"), "upstream")
        self.assertEqual(
            git.text("rev-parse", "--abbrev-ref", f"{local_branch(1420)}@{{upstream}}"),
            f"origin/{HEAD_BRANCH}",
        )

    def test_plain_git_push_reaches_only_the_pr_head_ref(self) -> None:
        tree = self.ensure()
        git = Git(tree.path)
        (tree.path / "worker" / "retry.py").write_text("def retry():\n    return 3\n")
        git.check("add", "-A")
        git.check("commit", "-qm", "fix(worker): idempotent retry")
        before_main = pr_repo.git(self.origin, "rev-parse", "main")
        git.check("push", "-q")
        after_main = pr_repo.git(self.origin, "rev-parse", "main")
        self.assertEqual(before_main, after_main)  # main untouched
        self.assertEqual(pr_repo.git(self.origin, "rev-parse", HEAD_BRANCH),
                         git.head_sha())
        # No stray branch named coder-ai/pr-1420 was published.
        refs = pr_repo.git(self.origin, "for-each-ref", "--format=%(refname)")
        self.assertNotIn("coder-ai/pr-1420", refs)

    def test_hostile_names_cannot_escape_the_worktree_root(self) -> None:
        for owner, repo in (("../../etc", "aiila"), ("org", "../.."), ("org", "a/b")):
            with self.subTest(owner=owner, repo=repo), self.assertRaises(StateError):
                worktree_path(owner, repo, 1420, root=self.trees)

    def test_not_a_repository(self) -> None:
        with self.assertRaises(WorktreeError):
            ensure_worktree(self.session, self.root / "origin.git" / "nope", root=self.trees)

    def test_missing_remote_branch_is_a_named_failure(self) -> None:
        session = a_session(head_branch="feature/does-not-exist")
        with self.assertRaises(WorktreeError) as caught:
            ensure_worktree(session, self.clone, root=self.trees)
        self.assertIn("could not fetch", str(caught.exception))


class RefreshToHead(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.origin, self.clone = pr_repo.build(self.root)
        self.trees = self.root / "worktrees"
        self.session = a_session()
        self.tree = ensure_worktree(self.session, self.clone, root=self.trees)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_up_to_date(self) -> None:
        self.assertEqual(
            refresh_to_head(self.session, self.clone, self.tree.sha, root=self.trees),
            "UP_TO_DATE")

    def test_fast_forwards_onto_a_human_push(self) -> None:
        new_sha = pr_repo.add_remote_commit(self.origin, self.root, HEAD_BRANCH)
        Git(self.clone).check("fetch", "--quiet", "origin", HEAD_BRANCH)
        self.assertEqual(
            refresh_to_head(self.session, self.clone, new_sha, root=self.trees),
            "FAST_FORWARDED")
        self.assertEqual(Git(self.tree.path).head_sha(), new_sha)

    def test_dirty_worktree_is_never_clobbered(self) -> None:
        (self.tree.path / "worker" / "retry.py").write_text("work in progress\n")
        new_sha = pr_repo.add_remote_commit(self.origin, self.root, HEAD_BRANCH)
        Git(self.clone).check("fetch", "--quiet", "origin", HEAD_BRANCH)
        self.assertEqual(
            refresh_to_head(self.session, self.clone, new_sha, root=self.trees), "DIRTY")
        self.assertEqual((self.tree.path / "worker" / "retry.py").read_text(),
                         "work in progress\n")

    def test_rewritten_history_is_reported_not_papered_over(self) -> None:
        git = Git(self.tree.path)
        (self.tree.path / "local.txt").write_text("local commit\n")
        git.check("add", "-A")
        git.check("commit", "-qm", "local")
        base = pr_repo.git(self.clone, "rev-parse", "main")
        self.assertEqual(
            refresh_to_head(self.session, self.clone, base, root=self.trees), "DIVERGED")


class Release(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.origin, self.clone = pr_repo.build(self.root)
        self.trees = self.root / "worktrees"
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.session = a_session()
        self.tree = ensure_worktree(self.session, self.clone, root=self.trees)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_clean_worktree_is_removed_and_session_state_survives(self) -> None:
        audit.append(self.sessions, "session_started")
        status = release_worktree(self.session, self.clone, "MERGED", root=self.trees,
                                  session_directory=self.sessions)
        self.assertEqual(status, REMOVED)
        self.assertFalse(self.tree.path.exists())
        self.assertTrue((self.sessions / "audit.jsonl").is_file())
        events = [item["event"] for item in audit.read(self.sessions)]
        self.assertIn("worktree_removed", events)

    def test_uncommitted_work_is_recorded_and_kept(self) -> None:
        (self.tree.path / "worker" / "retry.py").write_text("half-finished repair\n")
        status = release_worktree(self.session, self.clone, "WATCH_TIMEOUT", root=self.trees,
                                  session_directory=self.sessions)
        self.assertEqual(status, KEPT_DIRTY)
        self.assertTrue(self.tree.path.exists())
        records = [item for item in audit.read(self.sessions)
                   if item["event"] == "worktree_uncommitted"]
        self.assertEqual(len(records), 1)
        self.assertIn("worker/retry.py", records[0]["files"])

    def test_forced_release_removes_a_dirty_tree_after_recording_it(self) -> None:
        (self.tree.path / "scratch").write_text("x")
        status = release_worktree(self.session, self.clone, "USER_STOP", root=self.trees,
                                  session_directory=self.sessions, force=True)
        self.assertEqual(status, REMOVED)
        self.assertFalse(self.tree.path.exists())
        events = [item["event"] for item in audit.read(self.sessions)]
        self.assertIn("worktree_uncommitted", events)

    def test_release_is_idempotent(self) -> None:
        release_worktree(self.session, self.clone, "MERGED", root=self.trees)
        self.assertEqual(release_worktree(self.session, self.clone, "MERGED",
                                          root=self.trees), REMOVED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
