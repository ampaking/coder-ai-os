"""`--dry-run` — safe first contact with a real pull request.

The point of this mode is what it does NOT do: no worktree, no guard, no elevated
environment, no model call, nothing written anywhere.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import pr_repo  # noqa: E402
from pr_github import FakeGitHub  # noqa: E402

from coderai.pr_automation.cli import CliError, PrRun, parse_args  # noqa: E402
from coderai.pr_automation.github import Gh  # noqa: E402
from coderai.pr_automation.rehearsal import rehearse  # noqa: E402
from coderai.pr_automation.resolve import ResolveError  # noqa: E402


class Parsing(unittest.TestCase):
    def test_the_flag_is_understood(self) -> None:
        run = parse_args(["1420", "--dry-run", "--", "claude"])
        assert isinstance(run, PrRun)
        self.assertTrue(run.dry_run)
        self.assertFalse(parse_args(["1420", "--", "claude"]).dry_run)

    def test_it_cannot_be_combined_with_background(self) -> None:
        with self.assertRaises(CliError) as caught:
            parse_args(["1420", "--dry-run", "--bg", "--", "claude"])
        self.assertIn("nothing to run in the background", str(caught.exception))

    def test_it_is_documented(self) -> None:
        from coderai.pr_automation.cli import USAGE
        self.assertIn("--dry-run", USAGE)


class Report(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.origin, self.clone = pr_repo.build(self.root)
        self.hub = FakeGitHub()
        self.gh = Gh(self.clone, runner=self.hub)
        self.state = self.root / "sessions"
        self.trees = self.root / "worktrees"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def report(self, argv: list[str] | None = None) -> str:
        run = parse_args(argv or ["1420", "--watch", "2h", "--dry-run", "--", "claude"])
        assert isinstance(run, PrRun)
        return rehearse(run, self.clone, gh=self.gh, state_root=self.state,
                        worktree_root=self.trees)

    def test_it_says_plainly_that_nothing_happened(self) -> None:
        self.assertIn("nothing was created, changed, or pushed", self.report())

    def test_it_shows_what_it_resolved(self) -> None:
        text = self.report()
        self.assertIn("#1420", text)
        self.assertIn("feature/worker-retry → main", text)
        self.assertIn("origin", text)

    def test_it_shows_where_it_would_work_and_where_it_would_push(self) -> None:
        text = self.report()
        self.assertIn(str(self.trees / "org" / "aiila" / "pr-1420"), text)
        self.assertIn("coder-ai/pr-1420 → pushes to feature/worker-retry", text)

    def test_it_shows_the_watch_window_and_provider(self) -> None:
        text = self.report(["1420", "--watch", "3h", "--dry-run", "--",
                            "codex", "--model", "gpt-5.6-sol"])
        self.assertIn("3h", text)
        self.assertIn("codex --model gpt-5.6-sol", text)

    def test_it_reports_findings_it_would_track(self) -> None:
        self.hub.add_thread("codex", "P1: retry can run twice", "worker/retry.py", 42,
                            bot=True)
        text = self.report()
        self.assertIn("worker/retry.py:42", text)
        self.assertIn("OPEN", text)

    def test_it_names_the_first_action_it_would_take(self) -> None:
        self.assertIn("wake claude", self.report())

    def test_it_names_failing_checks(self) -> None:
        self.hub.set_check("worker-tests", "COMPLETED", "FAILURE", required=True)
        text = self.report()
        self.assertIn("failing: worker-tests", text)

    def test_it_flags_a_fork_as_read_only(self) -> None:
        self.hub.pr["isCrossRepository"] = True
        self.assertIn("read-only", self.report())

    def test_it_reports_a_prior_session(self) -> None:
        from coderai.pr_automation.state import (
            Session, ensure_dir, save_session, session_dir,
        )
        session = Session(host="github.com", owner="org", repo="aiila", number=1420,
                          head_branch="feature/worker-retry", base_branch="main",
                          remote="origin", provider="claude", provider_argv=["claude"])
        session.state = "WAITING_FOR_REVIEW"
        session.wake_count = 3
        ensure_dir(session_dir("org", "aiila", 1420, root=self.state), self.state)
        save_session(session, root=self.state)
        text = self.report()
        self.assertIn("RESUMED", text)
        self.assertIn("3 wakes", text)

    def test_it_ends_with_the_real_command(self) -> None:
        self.assertIn("coder-ai pr 1420 --watch 2h -- claude", self.report())

    def test_a_closed_pr_is_refused_here_too(self) -> None:
        self.hub.close()
        with self.assertRaises(ResolveError):
            self.report()


class ItReallyDoesNothing(Report):
    def test_no_worktree_is_created(self) -> None:
        self.report()
        self.assertFalse(self.trees.exists(), "a dry run created a worktree")

    def test_no_session_state_is_written(self) -> None:
        self.report()
        self.assertFalse(self.state.exists(), "a dry run wrote session state")

    def test_the_repository_is_untouched(self) -> None:
        before = (pr_repo.git(self.clone, "rev-parse", "HEAD"),
                  pr_repo.git(self.clone, "status", "--porcelain"),
                  pr_repo.git(self.clone, "worktree", "list", "--porcelain"))
        self.report()
        after = (pr_repo.git(self.clone, "rev-parse", "HEAD"),
                 pr_repo.git(self.clone, "status", "--porcelain"),
                 pr_repo.git(self.clone, "worktree", "list", "--porcelain"))
        self.assertEqual(after, before)

    def test_nothing_is_pushed(self) -> None:
        before = pr_repo.git(self.origin, "for-each-ref", "--format=%(refname) %(objectname)")
        self.report()
        after = pr_repo.git(self.origin, "for-each-ref", "--format=%(refname) %(objectname)")
        self.assertEqual(after, before)

    def test_only_read_calls_are_made_to_github(self) -> None:
        self.report()
        for call in self.hub.calls:
            with self.subTest(call=call):
                self.assertNotIn("-X", call)
                self.assertNotIn("--method", call)
                self.assertNotIn("merge", call)
                self.assertNotIn("comment", call)

    def test_it_costs_no_model_call(self) -> None:
        """There is no provider runner here at all — the report proves the plan."""
        import coderai.pr_automation.rehearsal as module
        source = Path(module.__file__).read_text()
        for forbidden in ("subprocess", "wake(", "build_profile", "guard_install"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
