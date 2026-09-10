"""How does coder-ai-os know a comment is settled? Every signal GitHub actually gives."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from pr_github import FakeGitHub  # noqa: E402

from coderai.pr_automation.context import build_bundle  # noqa: E402
from coderai.pr_automation.delta import compare  # noqa: E402
from coderai.pr_automation.findings import (  # noqa: E402
    FIXED, OPEN, OUTDATED, RESOLVED, Ledger, apply_decision, apply_snapshot,
)
from coderai.pr_automation.github import Gh  # noqa: E402
from coderai.pr_automation.guard.policy import GuardConfig, decide_gh  # noqa: E402
from coderai.pr_automation.snapshot import collect  # noqa: E402
from coderai.pr_automation.state import Session  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = FakeGitHub()          # PR author is "nobin"
        self.gh = Gh(Path("."), runner=self.hub)
        self.ledger = Ledger()

    def snap(self):
        return collect(self.gh, "org", "aiila", 1420)

    def ingest(self):
        return apply_snapshot(self.ledger, self.snap())

    def only(self):
        return next(iter(self.ledger.findings.values()))


class ThreadResolution(Base):
    def test_a_reviewer_resolving_settles_the_finding(self) -> None:
        thread = self.hub.add_thread("reviewer", "retry can run twice")
        self.ingest()
        self.hub.resolve_thread(thread, by="reviewer")
        self.ingest()
        finding = self.only()
        self.assertEqual(finding.state, RESOLVED)
        self.assertEqual(finding.resolved_by, "reviewer")
        self.assertFalse(finding.self_resolved)
        self.assertIn("resolved by reviewer", finding.note)

    def test_the_pr_author_resolving_is_recorded_as_such(self) -> None:
        """Self-resolution is a claim, not reviewer confirmation."""
        thread = self.hub.add_thread("reviewer", "retry can run twice")
        self.ingest()
        self.hub.resolve_thread(thread, by="nobin")     # the PR author
        self.ingest()
        finding = self.only()
        self.assertEqual(finding.state, RESOLVED)
        self.assertTrue(finding.self_resolved)
        self.assertIn("not reviewer confirmation", finding.note)

    def test_re_opening_a_thread_makes_the_concern_live_again(self) -> None:
        thread = self.hub.add_thread("reviewer", "retry can run twice")
        self.ingest()
        self.hub.resolve_thread(thread, by="reviewer")
        self.ingest()
        self.assertEqual(self.only().state, RESOLVED)
        self.hub.unresolve_thread(thread)
        self.ingest()
        finding = self.only()
        self.assertEqual(finding.state, OPEN)
        self.assertEqual(finding.resolved_by, "")
        self.assertFalse(finding.self_resolved)
        self.assertIn("re-opened", finding.note)
        self.assertEqual(len(self.ledger), 1, "re-opening must not duplicate the finding")

    def test_a_deleted_thread_is_treated_as_withdrawn(self) -> None:
        thread = self.hub.add_thread("reviewer", "on reflection this is wrong")
        self.ingest()
        identifier = self.only().id
        self.hub.delete_thread(thread)
        self.ingest()
        self.assertEqual(self.ledger.get(identifier).state, RESOLVED)
        self.assertIn("deleted", self.ledger.get(identifier).note)

    def test_a_partial_snapshot_never_reads_as_deletion(self) -> None:
        thread = self.hub.add_thread("reviewer", "still open")
        self.ingest()
        identifier = self.only().id
        self.hub.failures["api graphql"] = (1, "server error")
        self.ingest()
        self.assertEqual(self.ledger.get(identifier).state, OPEN)

    def test_outdated_is_distinct_from_resolved(self) -> None:
        thread = self.hub.add_thread("reviewer", "this line is wrong")
        self.ingest()
        self.hub.outdate_thread(thread)
        self.ingest()
        self.assertEqual(self.only().state, OUTDATED)

    def test_resolution_after_a_fix_does_not_undo_the_fix(self) -> None:
        thread = self.hub.add_thread("reviewer", "retry can run twice")
        self.ingest()
        identifier = self.only().id
        apply_decision(self.ledger, [{"id": identifier, "state": FIXED,
                                      "note": "fixed in 91ad773"}], by="claude")
        self.hub.resolve_thread(thread, by="reviewer")
        self.ingest()
        self.assertEqual(self.ledger.get(identifier).state, RESOLVED)


class ReviewSupersession(Base):
    def test_a_later_approval_withdraws_an_earlier_change_request(self) -> None:
        self.hub.add_review("reviewer", "CHANGES_REQUESTED", "please fix retry",
                            at="2026-09-01T10:00:00Z")
        self.ingest()
        self.assertEqual(self.only().state, OPEN)
        self.hub.add_review("reviewer", "APPROVED", "確認しました。ありがとうございます。",
                            at="2026-09-01T12:00:00Z")
        self.ingest()
        finding = self.only()
        self.assertEqual(finding.state, RESOLVED)
        self.assertIn("APPROVED", finding.note)

    def test_a_dismissed_review_is_also_withdrawn(self) -> None:
        self.hub.add_review("reviewer", "CHANGES_REQUESTED", "fix", at="2026-09-01T10:00:00Z")
        self.ingest()
        self.hub.add_review("reviewer", "DISMISSED", "", at="2026-09-01T11:00:00Z")
        self.ingest()
        self.assertEqual(self.only().state, RESOLVED)

    def test_another_reviewers_approval_does_not_withdraw_it(self) -> None:
        self.hub.add_review("reviewer-a", "CHANGES_REQUESTED", "fix",
                            at="2026-09-01T10:00:00Z")
        self.ingest()
        self.hub.add_review("reviewer-b", "APPROVED", "looks fine to me",
                            at="2026-09-01T11:00:00Z")
        self.ingest()
        self.assertEqual(self.only().state, OPEN,
                         "only the reviewer who asked can withdraw the request")

    def test_an_earlier_approval_does_not_withdraw_a_later_request(self) -> None:
        self.hub.add_review("reviewer", "APPROVED", "lgtm", at="2026-09-01T09:00:00Z")
        self.hub.add_review("reviewer", "CHANGES_REQUESTED", "wait, this breaks retry",
                            at="2026-09-01T10:00:00Z")
        self.ingest()
        self.assertEqual(self.only().state, OPEN)

    def test_re_requesting_changes_after_approving_reopens_the_work(self) -> None:
        self.hub.add_review("reviewer", "CHANGES_REQUESTED", "fix", at="2026-09-01T10:00:00Z")
        self.ingest()
        self.hub.add_review("reviewer", "APPROVED", "ok", at="2026-09-01T11:00:00Z")
        self.ingest()
        self.assertEqual(len(self.ledger.open_findings()), 0)
        self.hub.add_review("reviewer", "CHANGES_REQUESTED", "actually no",
                            at="2026-09-01T12:00:00Z")
        self.ingest()
        self.assertEqual(len(self.ledger.open_findings()), 1)


class WhatTheAiIsTold(Base):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.worktree = Path(self._tmp.name)
        self.session = Session(host="github.com", owner="org", repo="aiila", number=1420,
                               head_branch="feature/worker-retry", base_branch="main",
                               remote="origin", provider="claude", provider_argv=["claude"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def bundle(self) -> str:
        snapshot = self.snap()
        return build_bundle(self.session, snapshot, compare(None, snapshot),
                            self.ledger, self.worktree)

    def test_the_bundle_names_who_resolved_a_thread(self) -> None:
        thread = self.hub.add_thread("reviewer", "retry can run twice")
        self.hub.resolve_thread(thread, by="reviewer")
        text = self.bundle()
        self.assertIn("[RESOLVED by reviewer]", text)

    def test_the_bundle_flags_author_self_resolution(self) -> None:
        thread = self.hub.add_thread("reviewer", "retry can run twice")
        self.hub.resolve_thread(thread, by="nobin")
        self.assertIn("(the PR author, not the reviewer)", self.bundle())

    def test_the_ledger_view_shows_how_a_finding_was_settled(self) -> None:
        thread = self.hub.add_thread("reviewer", "retry can run twice")
        self.ingest()
        self.hub.resolve_thread(thread, by="reviewer")
        self.ingest()
        self.assertIn("[resolved by reviewer]", self.ledger.view())

    def test_the_skill_explains_the_signals(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "skills" / "pr-engineer"
                / "SKILL.md").read_text(encoding="utf-8")
        flat = " ".join(text.split())
        for phrase in ("thread resolved **by the reviewer**",
                       "thread resolved **by the PR author**",
                       "thread re-opened after being resolved",
                       "thread deleted",
                       "the button is bookkeeping, the words are the decision"):
            with self.subTest(phrase=phrase):
                self.assertIn(" ".join(phrase.split()), flat)


class ResolvingThreadsIsScoped(unittest.TestCase):
    """The agent may close a loop it actually addressed — and nothing else."""

    def setUp(self) -> None:
        self.worktree = tempfile.mkdtemp()
        self.config = GuardConfig(worktree=self.worktree, remote="origin",
                                  head_branch="feature/worker-retry",
                                  local_branch="coder-ai/pr-1420")

    def gh(self, *args: str):
        return decide_gh(["gh", *args], {}, self.worktree, self.config)

    def test_reading_graphql_is_free(self) -> None:
        self.assertTrue(self.gh("api", "graphql", "-f",
                                "query=query{repository{pullRequest{id}}}"))

    def test_resolving_and_unresolving_a_thread_is_allowed(self) -> None:
        for mutation in ("resolveReviewThread", "unresolveReviewThread"):
            with self.subTest(mutation=mutation):
                self.assertTrue(self.gh(
                    "api", "graphql", "-f",
                    f'query=mutation{{{mutation}(input:{{threadId:"T_1"}}){{thread{{id}}}}}}'))

    def test_no_other_mutation_can_ride_along(self) -> None:
        attempts = [
            'query=mutation{resolveReviewThread(input:{threadId:"T"}){thread{id}} '
            'deleteRef(input:{refId:"R"}){clientMutationId}}',
            'query=mutation{mergePullRequest(input:{pullRequestId:"P"}){clientMutationId}}',
            'query=mutation{closePullRequest(input:{pullRequestId:"P"}){clientMutationId}}',
            'query=mutation{createRef(input:{name:"refs/heads/x"}){clientMutationId}}',
            'query=mutation{updateBranchProtectionRule(input:{}){clientMutationId}}',
            'query=mutation{addComment(input:{}){clientMutationId}}',
        ]
        for attempt in attempts:
            with self.subTest(attempt=attempt[:48]):
                decision = self.gh("api", "graphql", "-f", attempt)
                self.assertFalse(decision.allowed, f"BYPASS: {attempt}")

    def test_a_mutation_passed_through_other_flag_spellings_is_still_checked(self) -> None:
        for flag in ("--field", "--raw-field"):
            with self.subTest(flag=flag):
                self.assertFalse(self.gh(
                    "api", "graphql", flag,
                    'query=mutation{mergePullRequest(input:{}){clientMutationId}}').allowed)

    def test_merging_by_rest_is_still_refused(self) -> None:
        self.assertFalse(self.gh("api", "-X", "PUT",
                                 "repos/org/aiila/pulls/1420/merge").allowed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
