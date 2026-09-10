"""Task 08 — the snapshot is complete, bounded, deterministic, and honest."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from pr_github import FakeGitHub  # noqa: E402

from coderai.pr_automation.github import Gh  # noqa: E402
from coderai.pr_automation.snapshot import (  # noqa: E402
    MAX_COMMENTS, Snapshot, SnapshotError, collect,
)


def collected(hub: FakeGitHub, **kwargs) -> Snapshot:
    return collect(Gh(Path("."), runner=hub), hub.owner, hub.repo, hub.number, **kwargs)


class Completeness(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = FakeGitHub()
        self.hub.add_comment("reviewer", "Please check the retry path.")
        self.hub.add_review("reviewer", "CHANGES_REQUESTED", "retry handling needs work")
        self.thread = self.hub.add_thread("codex", "P1: retry can process the job twice.",
                                          "worker/retry.py", 42, bot=True)
        self.hub.set_check("worker-tests", "COMPLETED", "SUCCESS")
        self.hub.set_check("lint", "IN_PROGRESS")

    def test_every_section_of_the_context_tree(self) -> None:
        snapshot = collected(self.hub)
        self.assertEqual(snapshot.number, 1420)
        self.assertEqual(snapshot.state, "OPEN")
        self.assertEqual(snapshot.head_branch, "feature/worker-retry")
        self.assertEqual(snapshot.base_branch, "main")
        self.assertEqual(snapshot.author, "nobin")
        self.assertEqual(len(snapshot.comments), 1)
        self.assertEqual(len(snapshot.reviews), 1)
        self.assertEqual(len(snapshot.review_comments), 1)
        self.assertEqual(len(snapshot.commits), 1)
        self.assertEqual(snapshot.changed_files, ["worker/retry.py"])
        self.assertEqual(len(snapshot.checks), 2)
        self.assertFalse(snapshot.partial)

    def test_review_thread_identity_and_state(self) -> None:
        comment = collected(self.hub).review_comments[0]
        self.assertEqual(comment.thread_id, self.thread)
        self.assertEqual(comment.path, "worker/retry.py")
        self.assertEqual(comment.line, 42)
        self.assertFalse(comment.resolved)
        self.assertTrue(comment.is_bot)

    def test_resolution_and_outdating_are_reflected(self) -> None:
        self.hub.resolve_thread(self.thread)
        self.assertTrue(collected(self.hub).review_comments[0].resolved)
        self.hub.outdate_thread(self.thread)
        self.assertTrue(collected(self.hub).review_comments[0].outdated)
        self.assertEqual(collected(self.hub).open_threads, [])

    def test_ci_state_rollup(self) -> None:
        self.assertEqual(collected(self.hub).ci_state, "pending")
        self.hub.set_check("lint", "COMPLETED", "SUCCESS")
        self.assertEqual(collected(self.hub).ci_state, "passed")
        self.hub.set_check("lint", "COMPLETED", "FAILURE")
        snapshot = collected(self.hub)
        self.assertEqual(snapshot.ci_state, "failed")
        self.assertEqual([check.name for check in snapshot.failed_checks], ["lint"])

    def test_linked_issues_are_extracted_locally(self) -> None:
        self.hub.add_comment("reviewer", "See https://github.com/org/aiila/issues/99 and #12")
        issues = collected(self.hub).linked_issues
        self.assertIn("org/aiila#77", issues)   # from the PR body
        self.assertIn("org/aiila#99", issues)
        self.assertIn("org/aiila#12", issues)

    def test_bot_authors_are_marked(self) -> None:
        self.hub.add_comment("github-actions[bot]", "CI failed", bot=True)
        marked = [item.is_bot for item in collected(self.hub).comments]
        self.assertEqual(marked, [False, True])

    def test_the_diff_is_not_fetched(self) -> None:
        """The agent reads the diff from its worktree; the API call would be waste."""
        collected(self.hub)
        for call in self.hub.calls:
            self.assertNotIn("diff", call)


class Determinism(unittest.TestCase):
    def test_identical_state_produces_an_identical_digest(self) -> None:
        hub = FakeGitHub()
        hub.add_comment("reviewer", "one")
        hub.add_comment("other", "two")
        first = collected(hub, now=100.0)
        second = collected(hub, now=999.0)
        self.assertEqual(first.digest(), second.digest())

    def test_any_meaningful_change_changes_the_digest(self) -> None:
        hub = FakeGitHub()
        before = collected(hub).digest()
        hub.add_comment("reviewer", "new")
        self.assertNotEqual(collected(hub).digest(), before)

    def test_edited_body_changes_the_digest(self) -> None:
        hub = FakeGitHub()
        identifier = hub.add_comment("reviewer", "original")
        before = collected(hub).digest()
        hub.edit_comment(identifier, "materially different")
        self.assertNotEqual(collected(hub).digest(), before)

    def test_round_trip_through_json(self) -> None:
        hub = FakeGitHub()
        hub.add_comment("reviewer", "hello")
        hub.add_thread("codex", "P1", bot=True)
        hub.set_check("tests", "COMPLETED", "FAILURE")
        original = collected(hub)
        restored = Snapshot.from_dict(json.loads(json.dumps(original.to_dict())))
        self.assertEqual(restored.digest(), original.digest())
        self.assertEqual(restored.ci_state, "failed")
        self.assertEqual(restored.review_comments[0].thread_id,
                         original.review_comments[0].thread_id)

    def test_future_schema_is_refused(self) -> None:
        hub = FakeGitHub()
        value = collected(hub).to_dict()
        value["schema_version"] = 99
        with self.assertRaises(SnapshotError):
            Snapshot.from_dict(value)


class BoundsAndFailures(unittest.TestCase):
    def test_comment_volume_is_capped_and_marked(self) -> None:
        hub = FakeGitHub()
        for index in range(MAX_COMMENTS + 25):
            hub.add_comment("reviewer", f"comment {index}")
        snapshot = collected(hub)
        self.assertEqual(len(snapshot.comments), MAX_COMMENTS)
        self.assertTrue(any(item.startswith("comments:") for item in snapshot.truncated))
        # The most recent comments are what matters for triage.
        self.assertIn(f"comment {MAX_COMMENTS + 24}", snapshot.comments[-1].body)

    def test_a_failed_sub_request_marks_the_snapshot_partial(self) -> None:
        hub = FakeGitHub()
        hub.failures["api graphql"] = (1, "server error")
        snapshot = collected(hub)
        self.assertTrue(snapshot.partial)
        self.assertTrue(snapshot.partial_reasons)
        self.assertEqual(snapshot.review_comments, [])
        self.assertEqual(snapshot.number, 1420)  # what did arrive is still usable

    def test_a_failed_primary_request_raises(self) -> None:
        hub = FakeGitHub()
        hub.failures["pr view"] = (1, "could not resolve to a PullRequest")
        with self.assertRaises(SnapshotError) as caught:
            collected(hub)
        self.assertFalse(caught.exception.retryable)

    def test_rate_limiting_is_retryable_not_fatal(self) -> None:
        hub = FakeGitHub()
        hub.failures["pr view"] = (1, "API rate limit exceeded")
        with self.assertRaises(SnapshotError) as caught:
            collected(hub)
        self.assertTrue(caught.exception.retryable)

    def test_bodies_are_clipped(self) -> None:
        hub = FakeGitHub()
        hub.add_comment("reviewer", "x" * 100_000)
        self.assertLessEqual(len(collected(hub).comments[0].body), 20_000)

    def test_terminal_states(self) -> None:
        hub = FakeGitHub()
        hub.merge()
        self.assertTrue(collected(hub).is_terminal)
        hub.close()
        self.assertTrue(collected(hub).is_terminal)

    def test_collection_costs_two_calls(self) -> None:
        hub = FakeGitHub()
        collected(hub)
        self.assertEqual(len(hub.calls), 2, "an idle poll must stay cheap")


if __name__ == "__main__":
    unittest.main(verbosity=2)
