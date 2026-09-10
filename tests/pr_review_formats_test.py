"""Do real-world AI review formats survive the pipeline intact?

Two shapes seen in production:
  1. `chatgpt-codex-connector[bot]` — inline review threads, one P1 finding each,
     sometimes citing a component AGENTS.md.
  2. `github-actions[bot]` — ONE long conversation comment carrying several
     findings at once (CRITICAL / WARNING / SUGGESTION) plus a checklist.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from pr_github import FakeGitHub  # noqa: E402

from coderai.pr_automation.context import build_bundle, discover_guidance  # noqa: E402
from coderai.pr_automation.delta import NEW_DISCUSSION, compare  # noqa: E402
from coderai.pr_automation.findings import (  # noqa: E402
    FIXED, KIND_AI_REVIEW, Ledger, apply_decision, apply_snapshot, finding_id,
)
from coderai.pr_automation.github import Gh  # noqa: E402
from coderai.pr_automation.snapshot import collect  # noqa: E402
from coderai.pr_automation.state import Session  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reviews"
CODEX_MIGRATION = (FIXTURES / "codex_p1_migration.md").read_text(encoding="utf-8")
CODEX_RENEWAL = (FIXTURES / "codex_p1_renewal.md").read_text(encoding="utf-8")
AI_REVIEW = (FIXTURES / "github_actions_ai_review.md").read_text(encoding="utf-8")

CHANGED = [
    "packages/function/src/services/subscription_renewal_service.py",
    "packages/admin-api/app/infrastructure/repository/async_rdb/subscription_change_repository.py",
    "libs/domain-model/alembic/versions/c8b26618de7f_add_billing_management_history.py",
    "packages/admin-api/app/application/use_cases/admin/billing_discounts.py",
]


def a_session() -> Session:
    return Session(host="github.com", owner="org", repo="billing", number=2210,
                   head_branch="feature/billing-management", base_branch="main",
                   remote="origin", provider="claude", provider_argv=["claude"],
                   title="Billing management history")


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = FakeGitHub(number=2210, owner="org", repo="billing",
                              head_branch="feature/billing-management")
        self.hub.pr["files"] = [{"path": name} for name in CHANGED] + [
            {"path": f"packages/admin-web/app/page-{index}.tsx"} for index in range(278)]
        self.gh = Gh(Path("."), runner=self.hub)
        self.session = a_session()
        self._tmp = tempfile.TemporaryDirectory()
        self.worktree = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def snap(self):
        return collect(self.gh, "org", "billing", 2210)

    def seed_codex(self) -> tuple[str, str]:
        renewal = self.hub.add_thread(
            "chatgpt-codex-connector[bot]", CODEX_RENEWAL,
            "packages/function/src/services/subscription_renewal_service.py", 214)
        migration = self.hub.add_thread(
            "chatgpt-codex-connector[bot]", CODEX_MIGRATION,
            "libs/domain-model/alembic/versions/c8b26618de7f_add_billing_management_history.py",
            52)
        return renewal, migration

    def bundle(self, ledger: Ledger | None = None) -> str:
        snapshot = self.snap()
        delta = compare(None, snapshot)
        return build_bundle(self.session, snapshot, delta, ledger or Ledger(), self.worktree)


class CodexInlineReview(Base):
    def test_each_p1_thread_becomes_one_ai_review_finding(self) -> None:
        self.seed_codex()
        ledger = apply_snapshot(Ledger(), self.snap())
        self.assertEqual(len(ledger), 2)
        for finding in ledger.findings.values():
            self.assertEqual(finding.kind, KIND_AI_REVIEW)
            self.assertTrue(finding.is_bot, "the codex connector must be recognised as a bot")
            self.assertEqual(finding.state, "OPEN")

    def test_the_finding_keeps_its_file_and_line_anchor(self) -> None:
        self.seed_codex()
        ledger = apply_snapshot(Ledger(), self.snap())
        anchors = {(item.path, item.line) for item in ledger.findings.values()}
        self.assertIn(
            ("libs/domain-model/alembic/versions/c8b26618de7f_add_billing_management_history.py",
             52), anchors)

    def test_the_p1_body_reaches_the_ai_intact(self) -> None:
        self.seed_codex()
        text = self.bundle()
        self.assertIn("Replace handwritten SQL in the new migrations", text)
        self.assertIn("prohibiting handwritten raw SQL", text)
        self.assertIn("AGENTS.md reference: libs/domain-model/AGENTS.md:L15-L17", text)

    def test_the_cited_component_instructions_are_discovered(self) -> None:
        """The finding cites libs/domain-model/AGENTS.md — coder-ai-os must surface it."""
        target = self.worktree / "libs" / "domain-model"
        target.mkdir(parents=True)
        (target / "AGENTS.md").write_text("no handwritten SQL in migrations\n")
        (self.worktree / "AGENTS.md").write_text("root rules\n")
        found = discover_guidance(self.worktree, CHANGED)
        self.assertIn("libs/domain-model/AGENTS.md", found)
        self.assertIn("AGENTS.md", found)

    def test_component_instructions_survive_a_large_changed_file_list(self) -> None:
        """282 changed files must not push component guidance out of the bundle."""
        deep = self.worktree / "packages" / "admin-api"
        deep.mkdir(parents=True)
        (deep / "AGENTS.md").write_text("admin-api rules\n")
        changed = [f"packages/admin-web/app/page-{index}.tsx" for index in range(278)]
        changed.append("packages/admin-api/app/application/use_cases/admin/billing_discounts.py")
        found = discover_guidance(self.worktree, changed)
        self.assertIn("packages/admin-api/AGENTS.md", found)

    def test_resolution_by_a_human_settles_the_finding(self) -> None:
        renewal, _ = self.seed_codex()
        ledger = apply_snapshot(Ledger(), self.snap())
        identifier = ledger.by_thread(renewal).id
        self.hub.resolve_thread(renewal)
        apply_snapshot(ledger, self.snap())
        self.assertEqual(ledger.get(identifier).state, "RESOLVED")
        self.assertEqual(len(ledger), 2, "resolution must not create a new finding")

    def test_all_three_bots_wake_triage_exactly_once(self) -> None:
        before = self.snap()
        self.seed_codex()
        self.hub.add_comment("github-actions[bot]", AI_REVIEW, bot=True)
        delta = compare(before, self.snap())
        self.assertEqual(delta.wake_reason, NEW_DISCUSSION)
        self.assertEqual(len(delta.new_review_comments), 2)
        self.assertEqual(len(delta.new_comments), 1)


class StructuredBotComment(Base):
    def test_the_whole_review_reaches_the_ai_uncut(self) -> None:
        self.hub.add_comment("github-actions[bot]", AI_REVIEW, bot=True)
        text = self.bundle()
        for marker in ("🚨 CRITICAL", "⚠️ WARNING", "💡 SUGGESTION", "Checklist"):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)
        self.assertIn("Missing is_service_provider authorization check", text)
        self.assertIn("Remove the orphaned function.", text)
        self.assertIn("AI PR review powered by Claude", text)

    def test_it_is_marked_as_a_bot_claim_not_a_human_request(self) -> None:
        self.hub.add_comment("github-actions[bot]", AI_REVIEW, bot=True)
        self.assertIn("(bot)", self.bundle())

    def test_rig_does_not_invent_findings_from_it(self) -> None:
        """A prose comment carries several findings; only the AI may split them."""
        self.hub.add_comment("github-actions[bot]", AI_REVIEW, bot=True)
        ledger = apply_snapshot(Ledger(), self.snap())
        self.assertEqual(len(ledger), 0)

    def test_the_ai_gets_a_stable_id_to_promote_findings_against(self) -> None:
        identifier = self.hub.add_comment("github-actions[bot]", AI_REVIEW, bot=True)
        text = self.bundle()
        candidate = finding_id(identifier)
        self.assertIn(candidate, text,
                      "the bundle must offer a deterministic id for this comment")

    def test_promoted_findings_stay_stable_across_wakes(self) -> None:
        identifier = self.hub.add_comment("github-actions[bot]", AI_REVIEW, bot=True)
        candidate = finding_id(identifier)
        ledger = Ledger()
        apply_snapshot(ledger, self.snap())
        # Wake 1: the AI splits the comment into three findings using the offered id.
        apply_decision(ledger, [
            {"id": f"{candidate}#1", "state": "VERIFIED", "note": "authz gap is real"},
            {"id": f"{candidate}#2", "state": "REJECTED", "note": "review-evidence warning"},
            {"id": f"{candidate}#3", "state": "OPEN", "note": "dead code _missing_periods"},
        ], by="claude")
        self.assertEqual(len(ledger), 3)
        # Wake 2: the same comment is still there; the ids must not churn.
        apply_snapshot(ledger, self.snap())
        text = self.bundle(ledger)
        self.assertIn(f"{candidate}#1", text)
        self.assertEqual(len(ledger), 3, "re-ingestion must not duplicate the findings")
        apply_decision(ledger, [{"id": f"{candidate}#1", "state": FIXED,
                                 "note": "added is_service_provider check"}], by="claude")
        self.assertEqual(ledger.get(f"{candidate}#1").state, FIXED)
        self.assertEqual(len(ledger), 3)

    def test_a_long_review_is_clipped_visibly_not_silently(self) -> None:
        self.hub.add_comment("github-actions[bot]", "x" * 60_000, bot=True)
        text = self.bundle()
        self.assertIn("clipped", text.lower())

    def test_a_large_changed_file_list_is_marked_when_truncated(self) -> None:
        text = self.bundle()
        self.assertIn("more changed file", text)


class SkillCoversTheseFormats(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (Path(__file__).resolve().parents[1] / "skills" / "pr-engineer"
                     / "SKILL.md").read_text(encoding="utf-8")

    def test_it_tells_the_ai_which_id_to_use(self) -> None:
        self.assertIn("candidate finding id", self.text)

    def test_it_covers_several_findings_in_one_comment(self) -> None:
        flat = " ".join(self.text.split())
        self.assertIn("#1", flat)



class BotRecognition(unittest.TestCase):
    """Both connectors must be seen as bots, however GitHub reports them."""

    def test_login_suffix_and_typename_are_both_honoured(self) -> None:
        from coderai.pr_automation.snapshot import _is_bot

        self.assertTrue(_is_bot({"login": "chatgpt-codex-connector[bot]"}))
        self.assertTrue(_is_bot({"login": "github-actions[bot]"}))
        self.assertTrue(_is_bot({"login": "github-actions"}))
        self.assertTrue(_is_bot({"login": "some-app", "__typename": "Bot"}))
        self.assertTrue(_is_bot({"login": "some-app", "is_bot": True}))
        self.assertFalse(_is_bot({"login": "ampaking", "__typename": "User"}))

    def test_the_graphql_query_asks_for_the_author_type(self) -> None:
        from coderai.pr_automation.snapshot import THREADS_QUERY

        self.assertIn("author { login __typename }", THREADS_QUERY)
if __name__ == "__main__":
    unittest.main(verbosity=2)
