"""Task 09 — one snapshot transition, at most one wake, always with a reason."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from pr_github import FakeGitHub  # noqa: E402

from coderai.pr_automation.delta import (  # noqa: E402
    CI_FAILED, CI_PROGRESS_ONLY, FIRST_SNAPSHOT, NEW_DISCUSSION, NEW_HEAD, NO_CHANGE,
    OWN_UPDATE_ONLY, PARTIAL_SNAPSHOT, RESOLUTION_ONLY, TERMINAL, compare, is_stale,
)
from coderai.pr_automation.github import Gh  # noqa: E402
from coderai.pr_automation.snapshot import collect  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = FakeGitHub()
        self.gh = Gh(Path("."), runner=self.hub)

    def snap(self):
        return collect(self.gh, self.hub.owner, self.hub.repo, self.hub.number)


class WakeDecisions(Base):
    def test_first_snapshot_triages_once(self) -> None:
        delta = compare(None, self.snap())
        self.assertEqual(delta.wake_reason, FIRST_SNAPSHOT)
        self.assertTrue(delta.meaningful)

    def test_identical_snapshot_sleeps(self) -> None:
        before = self.snap()
        delta = compare(before, self.snap())
        self.assertFalse(delta.should_wake)
        self.assertEqual(delta.sleep_reason, NO_CHANGE)
        self.assertFalse(delta.meaningful)

    def test_five_comments_and_one_review_produce_exactly_one_wake(self) -> None:
        before = self.snap()
        for index in range(5):
            self.hub.add_comment("reviewer", f"point {index}")
        self.hub.add_review("reviewer", "CHANGES_REQUESTED", "please address the above")
        delta = compare(before, self.snap())
        self.assertEqual(delta.wake_reason, NEW_DISCUSSION)
        self.assertEqual(len(delta.new_comments), 5)
        self.assertEqual(len(delta.new_reviews), 1)
        self.assertEqual(len([reason for reason in [delta.wake_reason] if reason]), 1)

    def test_inline_review_comment_wakes(self) -> None:
        before = self.snap()
        self.hub.add_thread("codex", "P1: retry can process the job twice.", bot=True)
        delta = compare(before, self.snap())
        self.assertEqual(delta.wake_reason, NEW_DISCUSSION)
        self.assertEqual(len(delta.new_review_comments), 1)

    def test_new_head_takes_priority_over_discussion(self) -> None:
        before = self.snap()
        self.hub.add_comment("reviewer", "also this")
        self.hub.push_commit("def4560000000000000000000000000000000000")
        delta = compare(before, self.snap())
        self.assertEqual(delta.wake_reason, NEW_HEAD)
        self.assertTrue(delta.head_changed)

    def test_ci_failure_wakes(self) -> None:
        self.hub.set_check("worker-tests", "IN_PROGRESS")
        before = self.snap()
        self.hub.set_check("worker-tests", "COMPLETED", "FAILURE")
        delta = compare(before, self.snap())
        self.assertEqual(delta.wake_reason, CI_FAILED)
        self.assertEqual((delta.ci_from, delta.ci_to), ("pending", "failed"))

    def test_ci_passing_does_not_wake(self) -> None:
        self.hub.set_check("worker-tests", "IN_PROGRESS")
        before = self.snap()
        self.hub.set_check("worker-tests", "COMPLETED", "SUCCESS")
        delta = compare(before, self.snap())
        self.assertFalse(delta.should_wake)
        self.assertEqual(delta.sleep_reason, CI_PROGRESS_ONLY)
        self.assertTrue(delta.meaningful)

    def test_terminal_state_never_wakes(self) -> None:
        before = self.snap()
        self.hub.merge()
        delta = compare(before, self.snap())
        self.assertFalse(delta.should_wake)
        self.assertEqual(delta.sleep_reason, TERMINAL)
        self.assertTrue(delta.terminal)

    def test_first_snapshot_of_a_closed_pr_does_not_wake(self) -> None:
        self.hub.close()
        delta = compare(None, self.snap())
        self.assertFalse(delta.should_wake)
        self.assertEqual(delta.sleep_reason, TERMINAL)


class MechanicalFilters(Base):
    def test_our_own_reply_does_not_wake_us(self) -> None:
        before = self.snap()
        own = self.hub.add_comment("coder-ai-user", "Fixed in 91ad773.")
        delta = compare(before, self.snap(), own_comment_ids={own})
        self.assertFalse(delta.should_wake)
        self.assertEqual(delta.sleep_reason, OWN_UPDATE_ONLY)
        self.assertTrue(delta.meaningful)

    def test_our_own_push_does_not_wake_us(self) -> None:
        before = self.snap()
        self.hub.push_commit("91ad7730000000000000000000000000000000000")
        delta = compare(before, self.snap(),
                        own_head_shas={"91ad7730000000000000000000000000000000000"})
        self.assertFalse(delta.should_wake)
        self.assertTrue(delta.head_changed)

    def test_a_reply_from_someone_else_still_wakes(self) -> None:
        before = self.snap()
        own = self.hub.add_comment("coder-ai-user", "Fixed in 91ad773.")
        self.hub.add_comment("reviewer", "Thanks, but see line 42.")
        delta = compare(before, self.snap(), own_comment_ids={own})
        self.assertEqual(delta.wake_reason, NEW_DISCUSSION)

    def test_whitespace_only_edit_is_not_material(self) -> None:
        identifier = self.hub.add_comment("reviewer", "Please fix the retry path.")
        before = self.snap()
        self.hub.edit_comment(identifier, "Please fix   the retry path.\n\n")
        delta = compare(before, self.snap())
        self.assertEqual(delta.edited_comments, [])
        self.assertFalse(delta.should_wake)

    def test_material_edit_is_reconsidered(self) -> None:
        identifier = self.hub.add_comment("reviewer", "Looks fine.")
        before = self.snap()
        self.hub.edit_comment(identifier, "Actually this breaks production — please fix.")
        delta = compare(before, self.snap())
        self.assertEqual(delta.edited_comments, [identifier])
        self.assertEqual(delta.wake_reason, NEW_DISCUSSION)

    def test_deleted_comment_is_noticed(self) -> None:
        identifier = self.hub.add_comment("reviewer", "withdraw me")
        before = self.snap()
        self.hub.delete_comment(identifier)
        delta = compare(before, self.snap())
        self.assertEqual(delta.deleted_comments, [identifier])

    def test_thread_resolution_records_without_waking(self) -> None:
        thread = self.hub.add_thread("reviewer", "please fix retry")
        before = self.snap()
        self.hub.resolve_thread(thread)
        delta = compare(before, self.snap())
        self.assertEqual(delta.resolved_threads, [thread])
        self.assertFalse(delta.should_wake)
        self.assertEqual(delta.sleep_reason, RESOLUTION_ONLY)
        self.assertTrue(delta.meaningful)

    def test_outdated_thread_is_recorded(self) -> None:
        thread = self.hub.add_thread("reviewer", "old concern")
        before = self.snap()
        self.hub.outdate_thread(thread)
        delta = compare(before, self.snap())
        self.assertEqual(delta.outdated_threads, [thread])

    def test_partial_snapshot_never_reads_as_deletions(self) -> None:
        self.hub.add_thread("codex", "P1", bot=True)
        before = self.snap()
        self.hub.failures["api graphql"] = (1, "server error")
        delta = compare(before, self.snap())
        self.assertFalse(delta.should_wake)
        self.assertEqual(delta.sleep_reason, PARTIAL_SNAPSHOT)

    def test_bot_review_still_wakes(self) -> None:
        """An AI reviewer's claim needs judgment — it is not noise (§33)."""
        before = self.snap()
        self.hub.add_review("codex[bot]", "COMMENTED", "P1: race condition", bot=True)
        self.assertEqual(compare(before, self.snap()).wake_reason, NEW_DISCUSSION)


class StaleRuns(Base):
    def test_human_push_during_a_run_is_stale(self) -> None:
        running = self.snap().head_sha
        self.hub.push_commit("xyz9990000000000000000000000000000000000")
        self.assertTrue(is_stale(running, self.snap()))

    def test_unchanged_head_is_not_stale(self) -> None:
        running = self.snap().head_sha
        self.assertFalse(is_stale(running, self.snap()))


class Purity(Base):
    def test_comparison_touches_nothing(self) -> None:
        before = self.snap()
        self.hub.add_comment("reviewer", "x")
        after = self.snap()
        calls = len(self.hub.calls)
        for _ in range(5):
            compare(before, after)
        self.assertEqual(len(self.hub.calls), calls)

    def test_every_transition_names_exactly_one_reason(self) -> None:
        before = self.snap()
        self.hub.add_comment("reviewer", "x")
        delta = compare(before, self.snap())
        self.assertTrue(bool(delta.wake_reason) != bool(delta.sleep_reason))

    def test_summary_is_human_readable(self) -> None:
        before = self.snap()
        self.hub.add_comment("reviewer", "x")
        self.hub.push_commit("aaa1110000000000000000000000000000000000")
        summary = compare(before, self.snap()).summary()
        self.assertIn("new comment", summary)
        self.assertIn("HEAD", summary)


if __name__ == "__main__":
    unittest.main(verbosity=2)
