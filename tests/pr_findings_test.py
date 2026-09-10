"""Task 10 — each concern is analyzed once, and its lifecycle is reconstructable."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from pr_github import FakeGitHub  # noqa: E402

from coderai.pr_automation.delta import compare  # noqa: E402
from coderai.pr_automation.findings import (  # noqa: E402
    CONFLICTED, FIXED, FIXING, HUMAN_REQUIRED, KIND_AI_REVIEW, KIND_CHANGE_REQUEST,
    KIND_HUMAN_REVIEW, OPEN, OUTDATED, REJECTED, RESOLVED, VERIFIED, VERIFYING,
    FindingsError, Ledger, apply_decision, apply_snapshot, apply_withdrawals,
    finding_id, load, recover_in_flight, save,
)
from coderai.pr_automation.github import Gh  # noqa: E402
from coderai.pr_automation.snapshot import collect  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = FakeGitHub()
        self.gh = Gh(Path("."), runner=self.hub)
        self.ledger = Ledger()

    def snap(self):
        return collect(self.gh, self.hub.owner, self.hub.repo, self.hub.number)

    def ingest(self, **kwargs):
        return apply_snapshot(self.ledger, self.snap(), **kwargs)


class Creation(Base):
    def test_an_inline_thread_creates_one_open_finding(self) -> None:
        thread = self.hub.add_thread("reviewer", "Please fix retry handling.",
                                     "worker/retry.py", 42)
        self.ingest()
        self.assertEqual(len(self.ledger), 1)
        finding = self.ledger.by_thread(thread)
        assert finding is not None
        self.assertEqual(finding.state, OPEN)
        self.assertEqual(finding.kind, KIND_HUMAN_REVIEW)
        self.assertEqual((finding.path, finding.line), ("worker/retry.py", 42))

    def test_an_ai_reviewer_is_marked_as_such(self) -> None:
        self.hub.add_thread("codex", "P1: retry can process the job twice.", bot=True)
        self.ingest()
        finding = next(iter(self.ledger.findings.values()))
        self.assertEqual(finding.kind, KIND_AI_REVIEW)
        self.assertTrue(finding.is_bot)

    def test_changes_requested_creates_a_finding(self) -> None:
        self.hub.add_review("reviewer", "CHANGES_REQUESTED", "retry handling needs work")
        self.ingest()
        self.assertEqual(len(self.ledger), 1)
        self.assertEqual(next(iter(self.ledger.findings.values())).kind, KIND_CHANGE_REQUEST)

    def test_plain_comments_do_not_invent_findings(self) -> None:
        self.hub.add_comment("reviewer", "Nice work on this.")
        self.hub.add_review("reviewer", "APPROVED", "LGTM")
        self.ingest()
        self.assertEqual(len(self.ledger), 0)

    def test_our_own_comments_never_create_findings(self) -> None:
        self.hub.add_thread("coder-ai-user", "I fixed the retry path.")
        self.ingest(own_authors=["coder-ai-user"])
        self.assertEqual(len(self.ledger), 0)

    def test_a_multi_comment_thread_is_one_finding(self) -> None:
        thread = self.hub.add_thread("reviewer", "Please fix retry handling.")
        self.hub.reply_in_thread(thread, "nobin", "Fixed.")
        self.hub.reply_in_thread(thread, "reviewer", "確認しました。")
        self.ingest()
        self.assertEqual(len(self.ledger), 1)


class Stability(Base):
    def test_ids_are_stable_across_three_snapshots(self) -> None:
        thread = self.hub.add_thread("reviewer", "fix retry")
        self.ingest()
        first = set(self.ledger.findings)
        self.hub.add_comment("someone", "unrelated chatter")
        self.ingest()
        self.hub.push_commit("bbb2220000000000000000000000000000000000")
        self.ingest()
        self.assertEqual(set(self.ledger.findings), first)
        self.assertIsNotNone(self.ledger.by_thread(thread))

    def test_id_is_derived_from_the_thread_not_position(self) -> None:
        self.assertEqual(finding_id("T_0001"), finding_id("T_0001"))
        self.assertNotEqual(finding_id("T_0001"), finding_id("T_0002"))

    def test_a_settled_finding_is_not_resurrected_by_the_same_evidence(self) -> None:
        self.hub.add_thread("reviewer", "fix retry")
        self.ingest()
        identifier = next(iter(self.ledger.findings))
        self.ledger.transition(identifier, FIXED, by="ai", note="fixed in 91ad773")
        for _ in range(3):
            self.ingest()
        self.assertEqual(self.ledger.get(identifier).state, FIXED)


class MechanicalTransitions(Base):
    def test_reviewer_approval_on_a_thread_closes_its_finding(self) -> None:
        thread = self.hub.add_thread("reviewer", "Please fix retry handling.")
        self.ingest()
        identifier = self.ledger.by_thread(thread).id
        self.hub.reply_in_thread(thread, "reviewer", "確認しました。このままで大丈夫です。")
        self.hub.resolve_thread(thread)
        self.ingest()
        self.assertEqual(self.ledger.get(identifier).state, RESOLVED)
        self.assertEqual(len(self.ledger), 1, "resolution must not create a new finding")

    def test_vanished_code_outdates_a_finding(self) -> None:
        thread = self.hub.add_thread("reviewer", "this line is wrong", "worker/retry.py", 42)
        self.ingest()
        identifier = self.ledger.by_thread(thread).id
        self.hub.outdate_thread(thread)
        self.ingest()
        self.assertEqual(self.ledger.get(identifier).state, OUTDATED)

    def test_withdrawn_comment_resolves_its_finding(self) -> None:
        self.hub.add_review("reviewer", "CHANGES_REQUESTED", "wrong on reflection")
        self.ingest()
        identifier = next(iter(self.ledger.findings))
        review_id = self.ledger.get(identifier).comment_id
        settled = apply_withdrawals(self.ledger, [review_id])
        self.assertEqual(settled, [identifier])
        self.assertEqual(self.ledger.get(identifier).state, RESOLVED)

    def test_a_crashed_run_leaves_no_permanent_in_flight_state(self) -> None:
        self.hub.add_thread("reviewer", "fix retry")
        self.hub.add_thread("codex", "P1: race", bot=True)
        self.ingest()
        identifiers = list(self.ledger.findings)
        self.ledger.transition(identifiers[0], FIXING, by="ai")
        self.ledger.transition(identifiers[1], VERIFYING, by="ai")
        recovered = recover_in_flight(self.ledger)
        self.assertEqual(sorted(recovered), sorted(identifiers))
        for identifier in identifiers:
            self.assertEqual(self.ledger.get(identifier).state, OPEN)
        self.assertIn("recovered", self.ledger.get(identifiers[0]).note)


class JudgmentTransitions(Base):
    def test_ai_decisions_are_recorded_with_history(self) -> None:
        self.hub.add_thread("codex", "P1: retry can process the job twice.", bot=True)
        self.ingest()
        identifier = next(iter(self.ledger.findings))
        apply_decision(self.ledger, [{"id": identifier, "state": VERIFYING,
                                      "note": "checking callers"}], by="claude")
        apply_decision(self.ledger, [{"id": identifier, "state": VERIFIED,
                                      "note": "reproduced with a failing test"}], by="claude")
        apply_decision(self.ledger, [{"id": identifier, "state": FIXED,
                                      "note": "fixed in 91ad773"}], by="claude")
        finding = self.ledger.get(identifier)
        self.assertEqual(finding.state, FIXED)
        self.assertEqual([item["to"] for item in finding.history],
                         [VERIFYING, VERIFIED, FIXED])
        self.assertTrue(all(item["by"] == "claude" for item in finding.history))

    def test_a_rejected_ai_claim_needs_no_commit(self) -> None:
        self.hub.add_thread("codex", "P1: race condition", bot=True)
        self.ingest()
        identifier = next(iter(self.ledger.findings))
        apply_decision(self.ledger, [{"id": identifier, "state": REJECTED,
                                      "note": "already guarded by the queue lock"}])
        self.assertEqual(self.ledger.get(identifier).state, REJECTED)
        self.assertTrue(self.ledger.get(identifier).settled)
        self.assertEqual(self.ledger.open_findings(), [])

    def test_conflicting_humans_escalate(self) -> None:
        self.hub.add_thread("reviewer-a", "return 404 here")
        self.hub.add_thread("reviewer-b", "do not change this API; clients require 200")
        self.ingest()
        identifiers = list(self.ledger.findings)
        apply_decision(self.ledger, [{"id": identifiers[0], "state": CONFLICTED,
                                      "note": "reviewers disagree on the contract"}])
        self.assertEqual(self.ledger.get(identifiers[0]).state, CONFLICTED)
        self.assertIn(self.ledger.get(identifiers[0]), self.ledger.open_findings())

    def test_human_required_is_available(self) -> None:
        self.hub.add_thread("reviewer", "should we break compatibility?")
        self.ingest()
        identifier = next(iter(self.ledger.findings))
        apply_decision(self.ledger, [{"id": identifier, "state": HUMAN_REQUIRED,
                                      "note": "product decision"}])
        self.assertEqual(self.ledger.get(identifier).state, HUMAN_REQUIRED)

    def test_invalid_states_and_ids_are_ignored_not_crashed(self) -> None:
        apply_decision(self.ledger, [{"id": "", "state": OPEN},
                                     {"id": "F-x", "state": "NONSENSE"},
                                     "not a dict"])
        self.assertEqual(len(self.ledger), 0)
        with self.assertRaises(FindingsError):
            self.ledger.transition("F-missing", OPEN)

    def test_ai_may_promote_a_finding_that_rig_would_not_invent(self) -> None:
        apply_decision(self.ledger, [{"id": "F-selfcheck", "state": VERIFIED,
                                      "kind": "ci", "note": "flaky worker test"}])
        self.assertEqual(self.ledger.get("F-selfcheck").state, VERIFIED)


class Persistence(Base):
    def test_round_trip(self) -> None:
        self.hub.add_thread("reviewer", "fix retry", "worker/retry.py", 42)
        self.ingest()
        identifier = next(iter(self.ledger.findings))
        self.ledger.transition(identifier, VERIFIED, by="claude", note="reproduced")
        with tempfile.TemporaryDirectory() as tmp:
            save(Path(tmp), self.ledger)
            restored = load(Path(tmp))
        finding = restored.get(identifier)
        assert finding is not None
        self.assertEqual(finding.state, VERIFIED)
        self.assertEqual(finding.path, "worker/retry.py")
        self.assertEqual(len(finding.history), 1)

    def test_missing_file_yields_an_empty_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(len(load(Path(tmp))), 0)

    def test_future_schema_is_refused(self) -> None:
        with self.assertRaises(FindingsError):
            Ledger.from_dict({"schema_version": 99, "findings": {}})

    def test_view_is_compact(self) -> None:
        self.hub.add_thread("reviewer", "fix retry handling in the worker")
        self.ingest()
        view = self.ledger.view()
        self.assertIn("OPEN", view)
        self.assertEqual(len(view.splitlines()), 1)


class FullConversation(Base):
    def test_ten_snapshots_analyze_each_concern_once(self) -> None:
        """A realistic thread: raised → fixed → confirmed → resolved."""
        thread = self.hub.add_thread("reviewer", "Please fix retry handling.")
        previous = None
        wakes = 0
        for step in range(10):
            if step == 2:
                self.hub.add_comment("nobin", "Fixed.")
            if step == 4:
                self.hub.push_commit("ccc3330000000000000000000000000000000000")
            if step == 6:
                self.hub.reply_in_thread(thread, "reviewer", "確認しました。大丈夫です。")
            if step == 7:
                self.hub.resolve_thread(thread)
            current = self.snap()
            delta = compare(previous, current)
            if delta.should_wake:
                wakes += 1
            apply_snapshot(self.ledger, current)
            previous = current
        self.assertEqual(len(self.ledger), 1, "one concern, one finding")
        self.assertEqual(next(iter(self.ledger.findings.values())).state, RESOLVED)
        self.assertLessEqual(wakes, 4, f"too many wakes for one conversation: {wakes}")
        history = next(iter(self.ledger.findings.values())).history
        self.assertTrue(history, "the lifecycle must be reconstructable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
