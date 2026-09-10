"""Is PR automation isolated per project? Prove it, per repository and per PR."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import pr_repo  # noqa: E402
from pr_github import FakeGitHub  # noqa: E402

from coderai.pr_automation import audit  # noqa: E402
from coderai.pr_automation.findings import Finding, Ledger  # noqa: E402
from coderai.pr_automation.findings import load as load_ledger, save as save_ledger  # noqa: E402
from coderai.pr_automation.gitcmd import Git  # noqa: E402
from coderai.pr_automation.guard import install as guard_install  # noqa: E402
from coderai.pr_automation.state import (  # noqa: E402
    Session, SessionLock, StateError, ensure_dir, list_sessions, load_session,
    save_session, session_dir,
)
from coderai.pr_automation.worktree import ensure_worktree, worktree_path  # noqa: E402

HEAD_BRANCH = "feature/worker-retry"


def a_session(owner: str, repo: str, number: int, branch: str = HEAD_BRANCH) -> Session:
    return Session(host="github.com", owner=owner, repo=repo, number=number,
                   head_branch=branch, base_branch="main", remote="origin",
                   provider="claude", provider_argv=["claude"])


class TwoProjects(unittest.TestCase):
    """Two different repositories, deliberately with the SAME pull request number."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state = self.root / "sessions"
        self.trees = self.root / "worktrees"
        self.origin_a, self.clone_a = pr_repo.build(self.root / "a")
        self.origin_b, self.clone_b = pr_repo.build(self.root / "b")
        self.session_a = a_session("org", "aiila", 1420)
        self.session_b = a_session("other", "service", 1420)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_state_directories_do_not_collide(self) -> None:
        first = session_dir("org", "aiila", 1420, root=self.state)
        second = session_dir("other", "service", 1420, root=self.state)
        self.assertNotEqual(first, second)
        self.assertNotIn(str(first), str(second))

    def test_worktrees_do_not_collide(self) -> None:
        first = ensure_worktree(self.session_a, self.clone_a, root=self.trees)
        second = ensure_worktree(self.session_b, self.clone_b, root=self.trees)
        self.assertNotEqual(first.path, second.path)
        self.assertTrue(first.path.is_dir())
        self.assertTrue(second.path.is_dir())

    def test_sessions_do_not_read_each_others_state(self) -> None:
        ensure_dir(session_dir("org", "aiila", 1420, root=self.state), self.state)
        ensure_dir(session_dir("other", "service", 1420, root=self.state), self.state)
        save_session(self.session_a, root=self.state)
        self.session_b.state = "HUMAN_NEEDED"
        save_session(self.session_b, root=self.state)
        first = load_session("org", "aiila", 1420, root=self.state)
        second = load_session("other", "service", 1420, root=self.state)
        assert first is not None and second is not None
        self.assertEqual(first.state, "OPEN")
        self.assertEqual(second.state, "HUMAN_NEEDED")

    def test_findings_and_audit_do_not_bleed(self) -> None:
        first = ensure_dir(session_dir("org", "aiila", 1420, root=self.state), self.state)
        second = ensure_dir(session_dir("other", "service", 1420, root=self.state), self.state)
        ledger = Ledger()
        ledger.upsert(Finding(id="F-a", state="OPEN", kind="human_review",
                              excerpt="project A concern"))
        save_ledger(first, ledger)
        audit.append(first, "ai_started", provider="claude")
        self.assertEqual(len(load_ledger(second)), 0)
        self.assertEqual(audit.read(second), [])
        self.assertEqual(len(load_ledger(first)), 1)

    def test_locks_are_per_project(self) -> None:
        first = ensure_dir(session_dir("org", "aiila", 1420, root=self.state), self.state)
        second = ensure_dir(session_dir("other", "service", 1420, root=self.state), self.state)
        with SessionLock(first, root=self.state):
            # A different project must not be blocked by this one's lock.
            SessionLock(second, root=self.state).acquire().release()

    def test_a_guard_scoped_to_one_project_refuses_the_other(self) -> None:
        tree_a = ensure_worktree(self.session_a, self.clone_a, root=self.trees)
        tree_b = ensure_worktree(self.session_b, self.clone_b, root=self.trees)
        directory = ensure_dir(session_dir("org", "aiila", 1420, root=self.state), self.state)
        guard = guard_install.install(directory, tree_a.path, remote="origin",
                                      head_branch=HEAD_BRANCH, local_branch=tree_a.branch)
        env = {**os.environ, **guard.env}
        allowed = subprocess.run(["git", "status"], cwd=str(tree_a.path), env=env,
                                 capture_output=True, text=True)
        refused = subprocess.run(["git", "status"], cwd=str(tree_b.path), env=env,
                                 capture_output=True, text=True)
        self.assertEqual(allowed.returncode, 0)
        self.assertEqual(refused.returncode, 13, "project A's scope reached project B")
        self.assertIn("DENY_OUTSIDE_WORKTREE", refused.stderr)

    def test_pushes_from_one_project_cannot_reach_the_other_remote(self) -> None:
        tree_a = ensure_worktree(self.session_a, self.clone_a, root=self.trees)
        directory = ensure_dir(session_dir("org", "aiila", 1420, root=self.state), self.state)
        guard = guard_install.install(directory, tree_a.path, remote="origin",
                                      head_branch=HEAD_BRANCH, local_branch=tree_a.branch)
        env = {**os.environ, **guard.env}
        result = subprocess.run(["git", "push", str(self.origin_b), "HEAD:main"],
                                cwd=str(tree_a.path), env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 13)
        self.assertIn("DENY_PUSH_REMOTE", result.stderr)

    def test_status_lists_both_projects_separately(self) -> None:
        ensure_dir(session_dir("org", "aiila", 1420, root=self.state), self.state)
        ensure_dir(session_dir("other", "service", 1420, root=self.state), self.state)
        save_session(self.session_a, root=self.state)
        save_session(self.session_b, root=self.state)
        rows = list_sessions(self.state)
        self.assertEqual({(item.owner, item.repo) for item in rows},
                         {("org", "aiila"), ("other", "service")})


class TwoPullRequestsInOneProject(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state = self.root / "sessions"
        self.trees = self.root / "worktrees"
        self.origin, self.clone = pr_repo.build(self.root)
        pr_repo.git(self.clone, "push", "-q", "origin",
                    f"origin/{HEAD_BRANCH}:refs/heads/feature/second")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_each_pr_gets_its_own_worktree_and_branch(self) -> None:
        first = ensure_worktree(a_session("org", "aiila", 1420), self.clone, root=self.trees)
        second = ensure_worktree(a_session("org", "aiila", 1421, "feature/second"),
                                 self.clone, root=self.trees)
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(first.branch, "coder-ai/pr-1420")
        self.assertEqual(second.branch, "coder-ai/pr-1421")

    def test_work_in_one_pr_is_invisible_to_the_other(self) -> None:
        first = ensure_worktree(a_session("org", "aiila", 1420), self.clone, root=self.trees)
        second = ensure_worktree(a_session("org", "aiila", 1421, "feature/second"),
                                 self.clone, root=self.trees)
        (first.path / "worker" / "retry.py").write_text("PR 1420 work\n")
        self.assertNotIn("PR 1420 work", (second.path / "worker" / "retry.py").read_text())
        self.assertTrue(Git(second.path).is_clean())


class TwoClonesOfTheSameRepository(unittest.TestCase):
    """The same PR, supervised from two different local checkouts."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state = self.root / "sessions"
        self.trees = self.root / "worktrees"
        self.origin, self.clone_one = pr_repo.build(self.root)
        self.clone_two = self.root / "second-checkout"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.clone_two)],
                       check=True)
        pr_repo._identity(self.clone_two)
        self.session = a_session("org", "aiila", 1420)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_pr_is_one_lifecycle_object_so_state_is_shared(self) -> None:
        """§8: coder-ai-os watches the pull request, not the checkout it was started from."""
        first = session_dir("org", "aiila", 1420, root=self.state)
        second = session_dir("org", "aiila", 1420, root=self.state)
        self.assertEqual(first, second)

    def test_the_worktree_is_rebuilt_for_the_checkout_that_owns_it(self) -> None:
        first = ensure_worktree(self.session, self.clone_one, root=self.trees)
        (first.path / "scratch.txt").write_text("from checkout one\n")
        second = ensure_worktree(self.session, self.clone_two, root=self.trees)
        self.assertEqual(first.path, second.path)
        registered = pr_repo.git(self.clone_two, "worktree", "list", "--porcelain")
        self.assertIn(str(second.path.resolve()), registered,
                      "the worktree must belong to the checkout now driving it")
        stale = pr_repo.git(self.clone_one, "worktree", "list", "--porcelain")
        self.assertNotIn(str(first.path.resolve()), stale,
                         "the previous checkout must not keep a live registration")

    def test_the_first_checkout_is_not_corrupted_by_the_handover(self) -> None:
        ensure_worktree(self.session, self.clone_one, root=self.trees)
        before = pr_repo.git(self.clone_one, "rev-parse", "HEAD")
        ensure_worktree(self.session, self.clone_two, root=self.trees)
        self.assertEqual(pr_repo.git(self.clone_one, "rev-parse", "HEAD"), before)
        self.assertEqual(pr_repo.git(self.clone_one, "status", "--porcelain"), "")

    def test_the_session_records_which_checkout_it_is_driving(self) -> None:
        ensure_worktree(self.session, self.clone_one, root=self.trees)
        self.assertTrue(self.session.source_repo,
                        "the session must record its source checkout")
        self.assertEqual(Path(self.session.source_repo).resolve(),
                         self.clone_one.resolve())


class StateRootIsolation(unittest.TestCase):
    def test_coder_ai_home_scopes_everything(self) -> None:
        from coderai.pr_automation.state import coder_ai_home, sessions_root, worktrees_root

        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("CODER_AI_HOME")
            os.environ["CODER_AI_HOME"] = tmp
            try:
                self.assertEqual(coder_ai_home(), Path(tmp))
                self.assertTrue(str(sessions_root()).startswith(tmp))
                self.assertTrue(str(worktrees_root()).startswith(tmp))
            finally:
                if previous is None:
                    os.environ.pop("CODER_AI_HOME", None)
                else:
                    os.environ["CODER_AI_HOME"] = previous

    def test_automation_state_never_lands_inside_the_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin, clone = pr_repo.build(root)
            session = a_session("org", "aiila", 1420)
            tree = ensure_worktree(session, clone, root=root / "worktrees")
            directory = ensure_dir(session_dir("org", "aiila", 1420, root=root / "sessions"),
                                   root / "sessions")
            guard_install.install(directory, tree.path, remote="origin",
                                  head_branch=HEAD_BRANCH, local_branch=tree.branch)
            self.assertFalse(directory.resolve().is_relative_to(clone.resolve()))
            self.assertFalse(tree.path.resolve().is_relative_to(clone.resolve()))
            self.assertEqual(pr_repo.git(clone, "status", "--porcelain"), "",
                             "no coder-ai-os artifact may appear in the engineer's checkout")


if __name__ == "__main__":
    unittest.main(verbosity=2)
