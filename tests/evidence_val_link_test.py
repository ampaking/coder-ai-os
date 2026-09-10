"""Task 08 — a silently broken visual loop must be impossible to have again."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from coderai.evidence import val_link


class Chain(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = self.root / "app"
        self.home = self.root / "home"
        self.home.mkdir()
        (self.project / "web").mkdir(parents=True)
        (self.project / "web" / "Card.tsx").write_text("export const Card = () => null\n")
        subprocess.run(["git", "init", "-q", str(self.project)], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(self.project), "add", "-A"], check=True,
                       capture_output=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def configure(self, globs=("web/**/*.tsx",)) -> None:
        target = self.project / ".coder-ai" / "val"
        target.mkdir(parents=True, exist_ok=True)
        (target / "config.json").write_text(json.dumps({"watchGlobs": list(globs)}))
        wrapper = self.project / ".coder-ai" / "val" / "run"
        wrapper.write_text("#!/bin/sh\n")
        wrapper.chmod(0o755)

    def settings(self, *, edit_hook=True, evidence_hook=True, permitted=True) -> None:
        hooks = {"PostToolUse": [{"hooks": [{"command": c} for c in filter(None, [
            ".coder-ai/scripts/val-post-edit.py" if edit_hook else None,
            ".coder-ai/scripts/evidence-post-tool.py" if evidence_hook else None])]}]}
        allow = ["Bash(.coder-ai/val/run:*)"] if permitted else []
        target = self.project / ".claude"
        target.mkdir(exist_ok=True)
        (target / "settings.json").write_text(json.dumps(
            {"hooks": hooks, "permissions": {"allow": allow}}))

    def check(self):
        return val_link.check(self.project, home=self.home)

    def test_an_unconfigured_project_says_so_plainly(self) -> None:
        chain = self.check()
        self.assertFalse(chain.configured)
        self.assertIn("not configured here", chain.render())
        self.assertIn("coder-ai setup", chain.render())

    def test_a_complete_chain_works(self) -> None:
        self.configure()
        self.settings()
        chain = self.check()
        self.assertTrue(chain.works, chain.render())
        self.assertIn("will run here", chain.render())

    def test_globs_that_match_nothing_are_caught(self) -> None:
        """The failure that let unverified UI ship: configured, hooked, and inert."""
        self.configure(globs=("frontend/**/*.tsx",))
        self.settings()
        chain = self.check()
        self.assertFalse(chain.works)
        broken = [link.name for link in chain.broken]
        self.assertIn("watch globs", broken)
        self.assertIn("can never fire", chain.render())

    def test_no_globs_at_all_is_caught(self) -> None:
        self.configure(globs=())
        self.settings()
        self.assertIn("nothing counts as UI", self.check().render())

    def test_a_missing_permission_is_caught(self) -> None:
        """It was installed and hooked — and every attempt hit a prompt."""
        self.configure()
        self.settings(permitted=False)
        chain = self.check()
        self.assertFalse(chain.works)
        self.assertIn("so it gets skipped", chain.render())

    def test_a_global_permission_counts(self) -> None:
        self.configure()
        self.settings(permitted=False)
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "settings.json").write_text(json.dumps(
            {"permissions": {"allow": ["Bash(.coder-ai/val/run:*)"]}}))
        self.assertTrue(self.check().works)

    def test_a_missing_edit_hook_is_caught(self) -> None:
        self.configure()
        self.settings(edit_hook=False)
        self.assertIn("edits are never noticed", self.check().render())

    def test_a_missing_evidence_hook_is_caught(self) -> None:
        self.configure()
        self.settings(evidence_hook=False)
        self.assertIn("claims cannot be verified", self.check().render())

    def test_a_missing_wrapper_is_caught(self) -> None:
        self.configure()
        self.settings()
        (self.project / ".coder-ai" / "val" / "run").unlink()
        self.assertIn("missing or not executable", self.check().render())

    def test_an_unconsumed_pending_marker_is_reported(self) -> None:
        self.configure()
        self.settings()
        (self.project / ".coder-ai" / "val" / val_link.MARKER).touch()
        chain = self.check()
        self.assertFalse(chain.works)
        self.assertIn("never verified", chain.render())
        self.assertEqual(len(val_link.pending(self.project)), 1)

    def test_every_broken_link_names_its_fix(self) -> None:
        self.configure(globs=("nope/**/*.tsx",))
        self.settings(edit_hook=False, evidence_hook=False, permitted=False)
        for link in self.check().broken:
            with self.subTest(link=link.name):
                self.assertTrue(link.fix, f"{link.name} has no stated fix")

    def test_the_command_exits_non_zero_when_broken(self) -> None:
        self.configure(globs=("nope/**/*.tsx",))
        self.settings()
        CLI = Path(__file__).resolve().parents[1] / "bin" / "coder-ai"
        result = subprocess.run([str(CLI), "doctor"], cwd=str(self.project),
                                capture_output=True, text=True, timeout=120)
        self.assertNotIn("Traceback", result.stdout + result.stderr)


class SurfacedElsewhere(unittest.TestCase):
    def test_prove_reports_a_broken_chain(self) -> None:
        from coderai.evidence.prove import UNVERIFIED, prove

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".coder-ai" / "val").mkdir(parents=True)
            (project / ".coder-ai" / "val" / "config.json").write_text(
                json.dumps({"watchGlobs": ["nope/**/*.tsx"]}))
            subprocess.run(["git", "init", "-q", str(project)], check=True,
                           capture_output=True)
            verdict = prove(project, "t", changed=["web/Card.tsx"])
            visual = [line for line in verdict.lines if line.item == "visual loop"]
            self.assertEqual(len(visual), 1)
            self.assertEqual(visual[0].status, UNVERIFIED)

    def test_status_shows_it(self) -> None:
        from coderai.status import collect, render

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "app"
            (project / ".coder-ai" / "val").mkdir(parents=True)
            (project / ".coder-ai" / "val" / "config.json").write_text(
                json.dumps({"watchGlobs": ["nope/**/*.tsx"]}))
            subprocess.run(["git", "init", "-q", str(project)], check=True,
                           capture_output=True)
            text = render(collect(project, home=Path(tmp) / "home"))
            self.assertIn("Visual loop", text)
            self.assertIn("coder-ai sync", text)



class OnlyWhenItMatters(unittest.TestCase):
    """A broken loop is doctor's business; prove raises it when UI was touched."""

    def _project(self, tmp: str) -> Path:
        project = Path(tmp)
        (project / ".coder-ai" / "val").mkdir(parents=True)
        (project / ".coder-ai" / "val" / "config.json").write_text(
            json.dumps({"watchGlobs": ["nope/**/*.tsx"]}))
        subprocess.run(["git", "init", "-q", str(project)], check=True, capture_output=True)
        return project

    def test_a_task_that_touched_no_ui_is_not_nagged(self) -> None:
        from coderai.evidence.prove import prove

        with tempfile.TemporaryDirectory() as tmp:
            verdict = prove(self._project(tmp), "t", changed=["api/plans.py"])
            self.assertEqual([l for l in verdict.lines if l.item == "visual loop"], [])

    def test_ui_files_raise_it_even_when_the_globs_miss_them(self) -> None:
        from coderai.evidence.prove import prove

        with tempfile.TemporaryDirectory() as tmp:
            verdict = prove(self._project(tmp), "t", changed=["web/Card.tsx"])
            self.assertTrue([l for l in verdict.lines if l.item == "visual loop"])

    def test_a_pending_marker_raises_it_regardless(self) -> None:
        from coderai.evidence.prove import prove

        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(tmp)
            (project / ".coder-ai" / "val" / val_link.MARKER).touch()
            verdict = prove(project, "t", changed=["README.md"])
            self.assertTrue([l for l in verdict.lines if l.item == "visual loop"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
