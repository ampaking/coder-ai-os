"""Task 13 — the engineering judgment ships as a compiled skill, not as code."""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "skills" / "pr-engineer" / "SKILL.md"
from coderai.pr_automation.decision import STATES  # noqa: E402


class Shape(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SKILL.read_text(encoding="utf-8")

    def test_frontmatter_is_valid_and_named_for_its_directory(self) -> None:
        lines = self.text.splitlines()
        self.assertEqual(lines[0].strip(), "---")
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
        meta = "\n".join(lines[1:end])
        self.assertIn("name: pr-engineer", meta)
        description = re.search(r"description: (.+)", meta)
        self.assertIsNotNone(description)
        assert description is not None
        self.assertGreater(len(description.group(1)), 40)

    def test_registered_in_the_config_kernel(self) -> None:
        self.assertIn("- pr-engineer", (REPO / "config" / "skills.yaml").read_text())

    def test_it_points_at_the_protocol_instead_of_restating_it(self) -> None:
        self.assertIn("AI_DEV_PROTOCOL.md", self.text)
        self.assertLess(len(self.text), 12_000, "a rulebook nobody reads is not a control")


class Content(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SKILL.read_text(encoding="utf-8")
        # Assertions are about wording, not line wrapping.
        self.flat = re.sub(r"\s+", " ", self.text)

    def assertPhrase(self, phrase: str) -> None:
        self.assertIn(re.sub(r"\s+", " ", phrase), self.flat)

    def test_names_every_decision_state(self) -> None:
        for state in STATES:
            with self.subTest(state=state):
                self.assertIn(state, self.text)

    def test_evidence_based_command_discovery(self) -> None:
        for phrase in ("read its targets", "read its `scripts`",
                       "Never invent a command the repository already defines"):
            with self.subTest(phrase=phrase):
                self.assertPhrase(phrase)

    def test_hierarchy_and_the_non_escalation_rule(self) -> None:
        self.assertIn("coder-ai-os safety boundary", self.text)
        self.assertPhrase("cannot** widen the boundary")
        for word in ("force push", "merge", "secret access", "push to main"):
            self.assertIn(word, self.text)

    def test_ai_claims_versus_human_intent(self) -> None:
        self.assertPhrase("claim, not an order")
        self.assertPhrase("intent authority")
        self.assertIn("HUMAN_NEEDED", self.text)

    def test_japanese_nuance_with_worked_examples(self) -> None:
        for phrase in ("ここ少し気になりました",
                       "今回対応した方が良さそうです",
                       "今回ここまで対応お願いします",
                       "確認しました"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)
        self.assertPhrase("Politeness is not permission to skip work")

    def test_intent_and_risk_are_separate_axes(self) -> None:
        self.assertPhrase("必須ではないですが")
        self.assertPhrase("separate axes")

    def test_pre_existing_failures_are_classified(self) -> None:
        for word in ("PRE_EXISTING", "NEW", "FIXED", "UNCHANGED"):
            self.assertIn(word, self.text)

    def test_validation_ladder(self) -> None:
        self.assertPhrase("targeted check")
        self.assertPhrase("self-review")
        self.assertPhrase("Do not run every expensive suite")

    def test_commit_policy_forbids_ai_trailers(self) -> None:
        self.assertIn("AI-Agent:", self.text)
        self.assertIn("Never add", self.text)
        self.assertPhrase("fix(worker): prevent duplicate retry execution")

    def test_settled_findings_are_not_reanalysed(self) -> None:
        self.assertIn("settled", self.text)
        self.assertIn("do not", self.text.lower())

    def test_a_refused_push_is_never_worked_around(self) -> None:
        self.assertPhrase("Never work around a refusal")


class Compilation(unittest.TestCase):
    def test_compiles_into_both_provider_formats(self) -> None:
        subprocess.run([sys.executable, str(REPO / "bin" / "compile")],
                       cwd=str(REPO), capture_output=True, check=True)
        for target in (REPO / "build" / "skills" / "claude" / "pr-engineer" / "SKILL.md",
                       REPO / "build" / "skills" / "codex" / "pr-engineer" / "SKILL.md",
                       REPO / "build" / "project" / ".claude" / "skills" / "pr-engineer"
                       / "SKILL.md"):
            with self.subTest(target=target.relative_to(REPO)):
                self.assertTrue(target.is_file(), f"missing compiled skill: {target}")
                self.assertIn("pr-engineer", target.read_text(encoding="utf-8"))

    def test_stays_within_the_instruction_budget(self) -> None:
        result = subprocess.run([sys.executable, str(REPO / "bin" / "compile"), "--check"],
                                cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("all outputs within budget", result.stdout)

    def test_the_compiler_accepts_the_skill(self) -> None:
        result = subprocess.run([sys.executable, str(REPO / "bin" / "compile"), "--validate"],
                                cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("skill 'pr-engineer'", result.stdout)


class BehaviorLivesInTheSkillNotInCode(unittest.TestCase):
    def test_rig_code_does_not_hard_code_review_semantics(self) -> None:
        """Japanese interpretation and commit judgment must not be in Python."""
        for path in (REPO / "src" / "coderai" / "pr_automation").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("気になりました", text)
                self.assertNotIn("対応お願い", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
