#!/usr/bin/env python3
"""Bounded multilingual project briefing tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "src" / "coderai" / "project_tasks" / "cli.py"


class TasksBriefingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name) / "project"
        (base / ".coder-ai").mkdir(parents=True)
        (base / ".ai").mkdir()
        self.project = base.resolve()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        digest = hashlib.sha256(str(self.project).encode()).hexdigest()
        (self.project / ".coder-ai" / "identity.json").write_text(json.dumps({
            "project_id": "注文-api", "git_root_hash": f"sha256:{digest}", "remote_hash": "sha256:test"
        }))
        (self.project / ".coder-ai" / "project.yaml").write_text(
            "project:\n  id: order-api\nuser:\n  languages: [Python, Japanese]\n"
        )
        (self.project / "README.md").write_text(
            "# 注文サービス\n\n注文を安全に処理するAPIです。技術用語 `OrderStatus` は変更しません。\n\n## 詳細\n"
        )
        (self.project / ".ai" / "PROJECT_SNAPSHOT.md").write_text(
            "## Top-level structure (depth 2)\n```\n.\napp\ntests\nREADME.md\n```\n"
        )
        (self.project / ".ai" / "PROJECT_NAVIGATOR.md").write_text(
            "## Current relevant flow\n```text\nrequest -> 注文API -> database -> response\n```\n"
        )
        (self.project / ".ai" / "standards.md").write_text(
            "## Declared tooling\n- test: pytest\n- lint: ruff check\n"
        )
        self.run_cli("enable")

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(CLI), "--project", str(self.project), *arguments],
            check=False, capture_output=True, text=True,
        )
        if result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

    def test_preserves_original_language_and_technical_identifier(self) -> None:
        briefing = json.loads(self.run_cli("brief", "--language", "English").stdout)
        self.assertEqual(briefing["project"]["title"], "注文サービス")
        self.assertIn("OrderStatus", briefing["project"]["purpose"])
        self.assertNotIn("`", briefing["project"]["purpose"])
        self.assertIn("注文API", briefing["flow"])
        self.assertEqual(briefing["requestedLanguage"], "English")
        self.assertTrue(briefing["translationRequired"])

    def test_briefing_is_bounded_and_evidence_linked(self) -> None:
        briefing = json.loads(self.run_cli("brief").stdout)
        self.assertLessEqual(len(briefing["entryPoints"]), 8)
        self.assertGreaterEqual(len(briefing["evidence"]), 4)
        self.assertEqual(briefing["validation"], ["test: pytest", "lint: ruff check"])

    def test_progressive_depth_keeps_quick_and_deep_views_small(self) -> None:
        quick = json.loads(self.run_cli("brief", "--depth", "quick").stdout)
        deep = json.loads(self.run_cli("brief", "--depth", "deep").stdout)
        self.assertEqual(quick["readingPath"][0]["time"], "30 seconds")
        self.assertIn("nextAction", quick["selected"])
        self.assertIn("evidence", deep["selected"])
        self.assertIn("OrderStatus", deep["technicalTerms"])


if __name__ == "__main__":
    unittest.main()
