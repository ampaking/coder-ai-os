"""Task 11 — the zero-idle-cost claim is asserted, not intended."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from pr_github import FakeGitHub  # noqa: E402

from coderai.pr_automation import audit, states  # noqa: E402
from coderai.pr_automation.findings import Ledger  # noqa: E402
from coderai.pr_automation.github import Gh  # noqa: E402
from coderai.pr_automation.snapshot import SnapshotError, collect  # noqa: E402
from coderai.pr_automation.state import Session, ensure_dir, session_dir  # noqa: E402
from coderai.pr_automation.watch import (  # noqa: E402
    POLL_ACTIVE, POLL_READY, POLL_REVIEW, SINGLE_PASS, poll_interval, run_watch,
)


class FakeClock:
    """Time only moves when the loop sleeps — an hour of watching takes no time."""

    def __init__(self, start: float = 1000.0) -> None:
        self.value = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = FakeGitHub()
        self.gh = Gh(Path("."), runner=self.hub)
        self.clock = FakeClock()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ledger = Ledger()
        self.session = Session(
            host="github.com", owner="org", repo="aiila", number=1420,
            head_branch="feature/worker-retry", base_branch="main", remote="origin",
            provider="claude", provider_argv=["claude"], watch_mode="bounded",
            watch_seconds=3600, watch_deadline=self.clock.now() + 3600,
        )
        self.directory = ensure_dir(session_dir("org", "aiila", 1420, root=self.root),
                                    self.root)
        self.model_calls = 0

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def collect(self):
        return collect(self.gh, "org", "aiila", 1420)

    def wake(self, snapshot, delta) -> str:
        self.model_calls += 1
        return "NO_ACTION"

    def watch(self, **kwargs):
        options = dict(collect=self.collect, wake=self.wake, clock=self.clock,
                       directory=self.directory, ledger=self.ledger,
                       state_root=self.root)
        options.update(kwargs)
        return run_watch(self.session, **options)


class IdleCost(Base):
    def test_an_idle_hour_costs_no_model_calls(self) -> None:
        result = self.watch()
        self.assertEqual(result.reason, states.WATCH_TIMEOUT)
        self.assertEqual(self.model_calls, 1, "only the first-snapshot triage")
        self.assertEqual(result.wakes, 1)
        self.assertGreater(result.polls, 1)
        # Cheap GitHub polls only: two gh calls per poll, nothing else.
        self.assertEqual(len(self.hub.calls), result.polls * 2)

    def test_polls_follow_the_adaptive_schedule(self) -> None:
        self.watch()
        self.assertTrue(self.clock.sleeps)
        self.assertTrue(all(item <= POLL_READY for item in self.clock.sleeps))
        self.assertIn(POLL_REVIEW, self.clock.sleeps)

    def test_an_idle_hour_makes_a_bounded_number_of_polls(self) -> None:
        result = self.watch()
        self.assertLessEqual(result.polls, 3600 // POLL_ACTIVE)
        self.assertLessEqual(result.polls, 25)

    def test_poll_intervals_by_state(self) -> None:
        self.assertEqual(poll_interval(states.WAITING_FOR_CI), POLL_ACTIVE)
        self.assertEqual(poll_interval(states.READY), POLL_READY)
        self.assertEqual(poll_interval(states.WAITING_FOR_REVIEW), POLL_REVIEW)
        self.assertEqual(poll_interval(states.READY, recently_active=True), POLL_ACTIVE)


class WatchWindow(Base):
    def test_single_pass_runs_once_and_exits(self) -> None:
        self.session.watch_mode = "single"
        self.session.watch_seconds = 0
        self.session.watch_deadline = None
        result = self.watch()
        self.assertEqual(result.reason, SINGLE_PASS)
        self.assertEqual(result.polls, 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_deadline_is_honoured_exactly(self) -> None:
        result = self.watch()
        self.assertEqual(result.reason, states.WATCH_TIMEOUT)
        self.assertGreaterEqual(self.clock.now(), 1000.0 + 3600)
        self.assertLess(self.clock.now(), 1000.0 + 3600 + POLL_READY)

    def test_until_close_runs_to_a_terminal_state(self) -> None:
        self.session.watch_mode = "until-close"
        self.session.watch_deadline = None
        calls = {"count": 0}

        def collect_then_merge():
            calls["count"] += 1
            if calls["count"] == 4:
                self.hub.merge()
            return self.collect()

        result = self.watch(collect=collect_then_merge)
        self.assertEqual(result.reason, states.MERGED)
        self.assertEqual(self.session.state, states.MERGED)

    def test_resume_uses_the_remaining_window_not_a_fresh_one(self) -> None:
        self.session.watch_deadline = self.clock.now() + 120
        result = self.watch()
        self.assertEqual(result.reason, states.WATCH_TIMEOUT)
        self.assertLess(self.clock.now(), 1000.0 + 300)

    def test_the_deadline_never_interrupts_an_active_run(self) -> None:
        """A wake in progress finishes; the deadline is checked at a safe boundary."""
        self.session.watch_deadline = self.clock.now() + 1

        def slow_wake(snapshot, delta) -> str:
            self.model_calls += 1
            self.clock.value += 5000  # the AI worked past the deadline
            return "FIX_NEEDED"

        result = self.watch(wake=slow_wake)
        self.assertEqual(self.model_calls, 1)
        self.assertEqual(result.wakes, 1)
        self.assertEqual(result.reason, states.WATCH_TIMEOUT)


class Terminal(Base):
    def test_merged_stops_the_watch(self) -> None:
        self.hub.merge()
        result = self.watch()
        self.assertEqual(result.reason, states.MERGED)
        self.assertEqual(result.polls, 1)
        self.assertEqual(self.model_calls, 0, "a merged PR needs no triage")

    def test_closed_stops_the_watch(self) -> None:
        self.hub.close()
        self.assertEqual(self.watch().reason, states.CLOSED)

    def test_user_stop(self) -> None:
        result = self.watch(stop=lambda: True)
        self.assertEqual(result.reason, states.USER_STOP)
        self.assertEqual(result.polls, 0)

    def test_terminal_reason_is_persisted(self) -> None:
        self.hub.merge()
        self.watch()
        self.assertEqual(self.session.terminal_reason, states.MERGED)
        self.assertTrue(self.session.is_terminal)


class StatesAndWakes(Base):
    def test_ready_is_not_terminal_and_keeps_watching(self) -> None:
        self.hub.set_check("worker-tests", "COMPLETED", "SUCCESS")
        self.hub.add_review("reviewer", "APPROVED", "LGTM")
        result = self.watch()
        self.assertIn(states.READY, result.history)
        self.assertEqual(result.reason, states.WATCH_TIMEOUT)
        self.assertGreater(result.polls, 1, "READY must keep watching")

    def test_ci_pending_waits_without_waking(self) -> None:
        self.hub.set_check("worker-tests", "IN_PROGRESS")
        result = self.watch()
        self.assertIn(states.WAITING_FOR_CI, result.history)
        self.assertEqual(self.model_calls, 1)  # first snapshot only

    def test_ci_failure_wakes_once(self) -> None:
        polls = {"count": 0}

        def collect_then_fail():
            polls["count"] += 1
            if polls["count"] == 3:
                self.hub.set_check("worker-tests", "COMPLETED", "FAILURE")
            return self.collect()

        result = self.watch(collect=collect_then_fail)
        self.assertEqual(result.wakes, 2)  # first snapshot + the CI failure
        self.assertIn(states.OPEN, result.history)

    def test_human_needed_when_a_finding_conflicts(self) -> None:
        from coderai.pr_automation.findings import CONFLICTED, Finding
        self.ledger.upsert(Finding(id="F-1", state=CONFLICTED, kind="human_review"))
        result = self.watch()
        self.assertIn(states.HUMAN_NEEDED, result.history)

    def test_new_discussion_wakes_once_per_snapshot(self) -> None:
        polls = {"count": 0}

        def collect_with_comments():
            polls["count"] += 1
            if polls["count"] == 2:
                for index in range(4):
                    self.hub.add_comment("reviewer", f"point {index}")
                self.hub.add_review("reviewer", "CHANGES_REQUESTED", "see above")
            return self.collect()

        result = self.watch(collect=collect_with_comments)
        self.assertEqual(result.wakes, 2, "5 events in one poll => 1 extra wake")


class Robustness(Base):
    def test_gh_failure_backs_off_instead_of_spinning(self) -> None:
        polls = {"count": 0}

        def flaky():
            polls["count"] += 1
            if polls["count"] % 2 == 0:
                raise SnapshotError("API rate limit exceeded", retryable=True)
            return self.collect()

        result = self.watch(collect=flaky)
        self.assertGreater(result.errors, 0)
        self.assertEqual(result.reason, states.WATCH_TIMEOUT)
        self.assertTrue(all(item >= POLL_ACTIVE for item in self.clock.sleeps))

    def test_a_fatal_collection_error_is_raised_not_swallowed(self) -> None:
        def broken():
            raise SnapshotError("no such pull request", retryable=False)

        with self.assertRaises(SnapshotError):
            self.watch(collect=broken)

    def test_state_is_persisted_across_the_loop(self) -> None:
        self.watch()
        self.assertTrue((self.directory / "session.json").is_file())
        self.assertTrue((self.directory / "snapshot.json").is_file())
        self.assertTrue((self.directory / "findings.json").is_file())
        events = [item["event"] for item in audit.read(self.directory)]
        self.assertIn("watch_started", events)
        self.assertIn("watch_finished", events)

    def test_audit_does_not_record_every_quiet_poll(self) -> None:
        result = self.watch()
        records = audit.read(self.directory)
        self.assertLess(len(records), result.polls,
                        "quiet polls must not flood the audit log")


if __name__ == "__main__":
    unittest.main(verbosity=2)
