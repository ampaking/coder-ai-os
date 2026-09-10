"""Task 18 — a session transcript reads like an engineer, not like a daemon log."""

from __future__ import annotations

import re
import time
import unittest

from coderai.pr_automation import ui
from coderai.pr_automation.findings import Finding, Ledger
from coderai.pr_automation.snapshot import Check, Snapshot
from coderai.pr_automation.state import Session

ANSI = re.compile(r"\x1b\[")


def a_session(state: str = "WAITING_FOR_REVIEW") -> Session:
    session = Session(host="github.com", owner="org", repo="aiila", number=1420,
                      head_branch="feature/worker-retry", base_branch="main",
                      remote="origin", provider="claude", provider_argv=["claude"])
    session.state = state
    session.head_sha = "91ad7734ee0000000000000000000000000000aa"
    session.last_ci_state = "passed"
    session.updated_at = time.time()
    return session


class WorkingPane(unittest.TestCase):
    def test_matches_the_specified_fields(self) -> None:
        text = ui.working_pane(a_session("WORKING"), task="Fix retry idempotency issue",
                               changed=["worker/retry.py", "tests/test_retry.py"],
                               validation=[("worker tests", "running")])
        self.assertIn("coder-ai · PR #1420", text)
        self.assertIn("State      WORKING", text)
        self.assertIn("AI         Claude", text)
        self.assertIn("Task       Fix retry idempotency issue", text)
        self.assertIn("HEAD       91ad773", text)
        self.assertIn("worker/retry.py", text)
        self.assertIn("worker tests", text)

    def test_empty_sections_are_omitted(self) -> None:
        text = ui.working_pane(a_session("WORKING"), task="triage")
        self.assertNotIn("Changed", text)
        self.assertNotIn("Validation", text)


class WaitingPane(unittest.TestCase):
    def test_matches_the_specified_fields(self) -> None:
        ledger = Ledger()
        ledger.upsert(Finding(id="F-1", state="FIXED", kind="ai_review"))
        snapshot = Snapshot(number=1420, state="OPEN", head_sha="91ad773",
                            checks=[Check(name="tests", status="COMPLETED",
                                          conclusion="SUCCESS")])
        session = a_session()
        session.watch_deadline = time.time() + 1800
        text = ui.waiting_pane(session, ledger=ledger, snapshot=snapshot,
                               last_action="Claude fixed and pushed retry handling.")
        self.assertIn("State      WAITING_FOR_REVIEW", text)
        self.assertIn("CI         passed", text)
        self.assertIn("Findings   0 open", text)
        self.assertIn("AI         sleeping", text)
        self.assertIn("Claude fixed and pushed retry handling.", text)
        self.assertIn("Watching for new PR activity", text)
        self.assertIn("29m left", text)

    def test_open_findings_are_counted(self) -> None:
        ledger = Ledger()
        ledger.upsert(Finding(id="F-1", state="OPEN", kind="human_review"))
        ledger.upsert(Finding(id="F-2", state="VERIFIED", kind="ai_review"))
        ledger.upsert(Finding(id="F-3", state="RESOLVED", kind="ai_review"))
        text = ui.waiting_pane(a_session(), ledger=ledger)
        self.assertIn("Findings   2 open", text)

    def test_an_expired_window_says_so(self) -> None:
        session = a_session()
        session.watch_deadline = time.time() - 10
        self.assertIn("Watch window ended.", ui.waiting_pane(session))


class SupervisorTable(unittest.TestCase):
    def test_lists_every_pr_with_its_state(self) -> None:
        sessions = []
        for number, state in ((1420, "WAITING_FOR_REVIEW"), (1421, "WAITING_FOR_CI"),
                              (1430, "READY"), (1435, "HUMAN_NEEDED")):
            session = a_session(state)
            session.number = number
            sessions.append(session)
        text = ui.status_table(sessions)
        for number, state in ((1420, "WAITING_FOR_REVIEW"), (1421, "WAITING_FOR_CI"),
                              (1430, "READY"), (1435, "HUMAN_NEEDED")):
            self.assertIn(f"#{number}", text)
            self.assertIn(state, text)

    def test_terminal_sessions_are_marked_differently(self) -> None:
        active, done = a_session("READY"), a_session("MERGED")
        done.number = 1421
        text = ui.status_table([active, done])
        self.assertIn("▸ org/aiila#1420", text)
        self.assertIn("· org/aiila#1421", text)

    def test_empty(self) -> None:
        self.assertIn("no supervised pull requests", ui.status_table([]))


class SignalNotNoise(unittest.TestCase):
    def test_panes_contain_no_polling_or_debug_detail(self) -> None:
        panes = [ui.working_pane(a_session("WORKING"), task="fix"),
                 ui.waiting_pane(a_session()),
                 ui.status_table([a_session()])]
        for text in panes:
            for noise in ("poll", "digest", "sha256", "backoff", "DEBUG", "snapshot.json"):
                self.assertNotIn(noise, text.lower() if noise.islower() else text)

    def test_the_audit_view_keeps_what_the_panes_drop(self) -> None:
        records = [{"seq": 1, "at": time.time(), "event": "delta",
                    "summary": "1 new comment", "wake_reason": "NEW_DISCUSSION"},
                   {"seq": 2, "at": time.time(), "event": "guard_deny",
                    "code": "DENY_PUSH_REF", "reason": "scoped to the PR head"}]
        text = ui.render_audit(records)
        self.assertIn("delta", text)
        self.assertIn("NEW_DISCUSSION", text)
        self.assertIn("DENY_PUSH_REF", text)

    def test_no_escape_codes_anywhere(self) -> None:
        for text in (ui.working_pane(a_session("WORKING"), task="fix"),
                     ui.waiting_pane(a_session()),
                     ui.status_table([a_session()]),
                     ui.render_audit([{"seq": 1, "at": 0, "event": "x"}])):
            self.assertIsNone(ANSI.search(text), "output must be pipe-safe")

    def test_width_is_bounded_for_narrow_terminals(self) -> None:
        self.assertGreaterEqual(ui.width(), ui.MIN_WIDTH)
        self.assertLessEqual(ui.width(), ui.MAX_WIDTH)

    def test_long_values_do_not_break_the_layout(self) -> None:
        session = a_session("WORKING")
        text = ui.working_pane(session, task="x" * 500,
                               changed=[f"very/long/path/{index}.py" for index in range(50)])
        self.assertLessEqual(len(text.splitlines()), 30)

    def test_audit_rendering_is_bounded(self) -> None:
        records = [{"seq": index, "at": 0, "event": "tick", "blob": "y" * 5000}
                   for index in range(500)]
        text = ui.render_audit(records, limit=50)
        self.assertEqual(len(text.splitlines()), 50)
        self.assertTrue(all(len(line) <= 400 for line in text.splitlines()))

    def test_rendering_is_pure(self) -> None:
        session = a_session()
        before = session.to_dict()
        ui.waiting_pane(session)
        ui.status_table([session])
        self.assertEqual(session.to_dict(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
