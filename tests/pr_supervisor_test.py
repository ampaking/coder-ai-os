"""Direct coverage for the supervisor — the module every wiring bug lived in.

Until now it was only exercised end to end, which is why four defects in it were
invisible: session merging, terminal cleanup, lock behaviour, failure handling.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import pr_repo  # noqa: E402
from pr_github import FakeGitHub  # noqa: E402

from coderai.pr_automation import audit, states  # noqa: E402
from coderai.pr_automation.cli import PrRun, parse_args  # noqa: E402
from coderai.pr_automation.github import Gh, GhResult  # noqa: E402
from coderai.pr_automation.state import (  # noqa: E402
    Session, SessionLock, ensure_dir, load_session, save_session, session_dir,
)
from coderai.pr_automation.supervisor import (  # noqa: E402
    SessionReport, SupervisorError, _merge_session, run_session, status_rows,
)
from coderai.pr_automation.wake import CommandResult  # noqa: E402

HEAD_BRANCH = "feature/worker-retry"


class Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class SessionMerging(unittest.TestCase):
    """Resuming must carry context forward, not silently start from zero."""

    class FakePR:
        host, owner, repo, number = "github.com", "org", "aiila", 1420
        title, author, url = "Fix retry", "nobin", "https://example.invalid/1420"
        head_branch, base_branch, remote = HEAD_BRANCH, "main", "origin"
        head_sha = "abc123"
        cross_repository = False

    def run_request(self, **kwargs) -> PrRun:
        argv = kwargs.pop("argv", ["1420", "--watch", "1h", "--", "claude"])
        run = parse_args(argv)
        assert isinstance(run, PrRun)
        return run

    def test_a_fresh_session_starts_clean(self) -> None:
        session = _merge_session(None, self.FakePR(), self.run_request(), now=500.0)
        self.assertEqual(session.created_at, 500.0)
        self.assertEqual(session.wake_count, 0)
        self.assertEqual(session.watch_deadline, 500.0 + 3600)

    def test_resuming_carries_everything_that_cost_something(self) -> None:
        previous = Session(host="github.com", owner="org", repo="aiila", number=1420,
                           head_branch=HEAD_BRANCH, base_branch="main", remote="origin",
                           provider="codex", provider_argv=["codex"])
        previous.created_at = 100.0
        previous.wake_count = 7
        previous.last_push_sha = "91ad773"
        previous.last_push_at = 250.0
        previous.last_snapshot_digest = "sha256:abc"
        previous.last_ci_state = "failed"
        previous.ci_attempts = {"abc:worker-tests": 2}
        previous.source_repo = "/home/engineer/work/aiila"

        session = _merge_session(previous, self.FakePR(), self.run_request(), now=900.0)
        self.assertEqual(session.created_at, 100.0, "the session's age was reset")
        self.assertEqual(session.wake_count, 7)
        self.assertEqual(session.last_push_sha, "91ad773")
        self.assertEqual(session.last_snapshot_digest, "sha256:abc")
        self.assertEqual(session.last_ci_state, "failed")
        self.assertEqual(session.ci_attempts, {"abc:worker-tests": 2})
        self.assertEqual(session.source_repo, "/home/engineer/work/aiila")

    def test_a_new_watch_window_starts_from_now(self) -> None:
        previous = Session(host="github.com", owner="org", repo="aiila", number=1420,
                           head_branch=HEAD_BRANCH, base_branch="main", remote="origin",
                           provider="claude", provider_argv=["claude"])
        previous.watch_deadline = 200.0
        session = _merge_session(previous, self.FakePR(),
                                 self.run_request(argv=["1420", "--watch", "2h",
                                                        "--", "claude"]), now=1000.0)
        self.assertEqual(session.watch_deadline, 1000.0 + 7200)

    def test_the_provider_can_change_between_sessions(self) -> None:
        previous = Session(host="github.com", owner="org", repo="aiila", number=1420,
                           head_branch=HEAD_BRANCH, base_branch="main", remote="origin",
                           provider="codex", provider_argv=["codex"])
        session = _merge_session(previous, self.FakePR(),
                                 self.run_request(argv=["1420", "--", "claude",
                                                        "--model", "x"]), now=1.0)
        self.assertEqual(session.provider, "claude")
        self.assertEqual(session.provider_argv, ["claude", "--model", "x"])


class Lifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.origin, self.clone = pr_repo.build(self.root)
        self.hub = FakeGitHub()
        self.hub.pr["headRefOid"] = pr_repo.git(self.clone, "rev-parse",
                                                f"origin/{HEAD_BRANCH}")
        self.gh = Gh(self.clone, runner=self.hub)
        self.state = self.root / "sessions"
        self.trees = self.root / "worktrees"
        self.lines: list[str] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def agent(self, payload: dict | None = None):
        def runner(argv, cwd, env, timeout, stdin):
            body = json.dumps(payload or {"state": "NO_ACTION", "summary": "ok"})
            if "--output-last-message" in argv:
                Path(argv[argv.index("--output-last-message") + 1]).write_text(body)
                body = ""
            return CommandResult(tuple(argv), 0, body, "", 5)
        return runner

    def supervise(self, argv: list[str], runner=None) -> SessionReport:
        run = parse_args(argv)
        assert isinstance(run, PrRun)
        return run_session(run, self.clone, gh=self.gh, state_root=self.state,
                           worktree_root=self.trees, clock=Clock(),
                           out=self.lines.append, runner=runner or self.agent())

    def directory(self) -> Path:
        return session_dir("org", "aiila", 1420, root=self.state)

    def test_a_single_pass_leaves_a_complete_session_record(self) -> None:
        report = self.supervise(["1420", "--watch", "0", "--", "codex"])
        self.assertEqual(report.reason, "SINGLE_PASS")
        for name in ("session.json", "snapshot.json", "findings.json",
                     "provider-health.json", "audit.jsonl"):
            with self.subTest(file=name):
                self.assertTrue((self.directory() / name).is_file(), name)

    def test_the_elevated_environment_is_gone_afterwards(self) -> None:
        self.supervise(["1420", "--watch", "0", "--", "codex"])
        self.assertFalse((self.directory() / "guard").exists())
        self.assertFalse((self.directory() / "guard.json").exists())
        self.assertFalse((self.directory() / "claude-settings.json").exists())

    def test_the_worktree_survives_a_non_terminal_exit(self) -> None:
        """A timed-out watch must not throw away work in progress."""
        self.supervise(["1420", "--watch", "0", "--", "codex"])
        self.assertTrue((self.trees / "org" / "aiila" / "pr-1420").is_dir())

    def test_a_merged_pr_releases_the_worktree(self) -> None:
        """It must merge DURING the session — a PR already merged never starts one."""
        import coderai.pr_automation.supervisor as supervisor_module
        from coderai.pr_automation.snapshot import collect

        polls = {"count": 0}

        def collect_then_merge(*_args, **_kwargs):
            polls["count"] += 1
            if polls["count"] >= 2:
                self.hub.merge()
            return collect(self.gh, "org", "aiila", 1420)

        saved = supervisor_module.collect
        supervisor_module.collect = collect_then_merge
        try:
            report = self.supervise(["1420", "--watch", "30m", "--", "codex"])
        finally:
            supervisor_module.collect = saved
        self.assertEqual(report.reason, states.MERGED)
        self.assertFalse((self.trees / "org" / "aiila" / "pr-1420").exists())

    def test_the_lock_is_released_so_the_next_run_can_start(self) -> None:
        self.supervise(["1420", "--watch", "0", "--", "codex"])
        SessionLock(self.directory(), root=self.state).acquire().release()

    def test_a_second_supervisor_is_refused_while_one_holds_the_lock(self) -> None:
        from coderai.pr_automation.state import StateError

        ensure_dir(self.directory(), self.state)
        with SessionLock(self.directory(), root=self.state):
            with self.assertRaises(StateError):
                self.supervise(["1420", "--watch", "0", "--", "codex"])

    def test_a_stale_stop_request_does_not_stop_the_next_session(self) -> None:
        from coderai.pr_automation import daemon

        ensure_dir(self.directory(), self.state)
        daemon.request_stop(self.directory())
        report = self.supervise(["1420", "--watch", "0", "--", "codex"])
        self.assertEqual(report.reason, "SINGLE_PASS")
        self.assertFalse(daemon.stop_requested(self.directory()))

    def test_the_supervisor_pid_is_cleared_on_exit(self) -> None:
        from coderai.pr_automation import daemon

        self.supervise(["1420", "--watch", "0", "--", "codex"])
        self.assertIsNone(daemon.read_pid(self.directory()))

    def test_an_unresolvable_target_fails_before_anything_is_created(self) -> None:
        with self.assertRaises(SupervisorError) as caught:
            self.supervise(["9999", "--watch", "0", "--", "codex"])
        self.assertIn("PR_NOT_FOUND", str(caught.exception))
        self.assertFalse(self.trees.exists())

    def test_the_permission_notice_names_the_real_worktree(self) -> None:
        report = self.supervise(["1420", "--watch", "0", "--", "codex"])
        self.assertIn(str(self.trees / "org" / "aiila" / "pr-1420"), report.notice)
        self.assertIn(HEAD_BRANCH, report.notice)

    def test_a_provider_that_returns_nothing_usable_is_not_fatal(self) -> None:
        def broken(argv, cwd, env, timeout, stdin):
            return CommandResult(tuple(argv), 0, "not json at all", "", 5)

        report = self.supervise(["1420", "--watch", "0", "--", "codex"], broken)
        self.assertEqual(report.reason, "SINGLE_PASS")
        self.assertEqual(len(report.outcomes), 1)
        self.assertIsNone(report.outcomes[0].decision)
        self.assertIn("ai_failed", [item["event"] for item in audit.read(self.directory())])

    def test_findings_survive_across_two_sessions(self) -> None:
        self.hub.add_thread("reviewer", "please fix retry handling")
        self.supervise(["1420", "--watch", "0", "--", "codex"])
        first = json.loads((self.directory() / "findings.json").read_text())
        self.supervise(["1420", "--watch", "0", "--", "codex"])
        second = json.loads((self.directory() / "findings.json").read_text())
        self.assertEqual(set(first["findings"]), set(second["findings"]))
        self.assertEqual(len(second["findings"]), 1)

    def test_status_rows_sees_the_session_it_just_ran(self) -> None:
        self.supervise(["1420", "--watch", "0", "--", "codex"])
        rows = status_rows(self.state)
        self.assertEqual([item.number for item in rows], [1420])


if __name__ == "__main__":
    unittest.main(verbosity=2)
