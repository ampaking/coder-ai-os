"""One CLI, one style — enforced, so it cannot drift again.

Every command must behave the same way for a human and for an agent: the same
help shape, the same flags for the same kinds of work, the same message prefix,
and one name for the product everywhere.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "bin" / "coder-ai"

# Commands that report something an agent may want to parse.
REPORTING = ("status", "prove")
# Commands that change something, so a rehearsal must be possible.
ACTING = ("ship",)


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(CLI), *args], cwd=str(REPO), capture_output=True,
                          text=True, timeout=120)


class OneName(unittest.TestCase):
    def test_no_abandoned_name_survives_anywhere_user_facing(self) -> None:
        pattern = re.compile(r"\b(rig|caos|CAOS)\b")
        offenders = []
        for path in [REPO / "README.md", REPO / "install.sh", REPO / "CHANGELOG.md",
                     *(REPO / "docs").glob("*.md"),
                     *(REPO / "src").rglob("*.py"),
                     *(REPO / "bin").iterdir(),
                     *(REPO / "skills").rglob("*.md"),
                     *(REPO / "config").glob("*.yaml")]:
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            # `retire_cli` must name the old binary in order to delete it; that is
            # cleanup, not advertising, so it is the one place the name may appear.
            text = "\n".join(line for line in text.splitlines()
                             if "retire_cli" not in line)
            if pattern.search(text):
                offenders.append(path.relative_to(REPO))
        self.assertEqual(offenders, [], f"abandoned names still present: {offenders}")

    def test_one_command_on_path(self) -> None:
        text = (REPO / "install.sh").read_text()
        links = re.findall(r'link_cli "\$REPO_DIR/([^"]+)"', text)
        self.assertEqual(sorted(links), ["bin/coder-ai", "bin/coder-ai-os"],
                         "a second binary crept back onto PATH")

    def test_the_project_folder_and_command_share_a_name(self) -> None:
        self.assertTrue((REPO / ".coder-ai").is_dir())
        self.assertTrue(CLI.is_file())

    def test_the_machine_root_matches_too(self) -> None:
        from coderai.pr_automation.state import DEFAULT_HOME

        self.assertEqual(DEFAULT_HOME, "~/.coder-ai")

    def test_environment_variables_share_one_prefix(self) -> None:
        found = set()
        for path in (REPO / "src").rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            found |= set(re.findall(r"\b([A-Z][A-Z0-9]+)_[A-Z_]+\b(?=[\"'])",
                                    path.read_text(encoding="utf-8")))
        stray = {name for name in found if name in {"RIG", "CAOS"}}
        self.assertEqual(stray, set(), f"stray env prefixes: {stray}")


class OneStyle(unittest.TestCase):
    def test_every_advertised_command_runs(self) -> None:
        listed = set(re.findall(r"^    coder-ai ([a-z-]+)", run("help").stdout, re.M))
        self.assertGreaterEqual(len(listed), 10)
        for command in sorted(listed):
            with self.subTest(command=command):
                result = run(command, "--help")
                self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_help_is_available_the_same_way_everywhere(self) -> None:
        for command in ("status", "prove", "ship", "pr"):
            with self.subTest(command=command):
                result = run(command, "--help")
                self.assertEqual(result.returncode, 0)
                self.assertIn("usage:", result.stdout.lower())
                self.assertIn(f"coder-ai {command}", result.stdout)

    def test_reporting_commands_answer_in_json(self) -> None:
        """An agent should never have to parse a human-facing table."""
        for command in REPORTING:
            with self.subTest(command=command):
                self.assertIn("--json", run(command, "--help").stdout)
                result = run(command, "--json")
                self.assertNotIn("Traceback", result.stdout + result.stderr)
                json.loads(result.stdout)

    def test_pr_status_answers_in_json_too(self) -> None:
        result = run("pr", "status", "--json")
        self.assertEqual(result.returncode, 0)
        self.assertIsInstance(json.loads(result.stdout), list)

    def test_acting_commands_can_rehearse(self) -> None:
        for command in ACTING:
            with self.subTest(command=command):
                self.assertIn("--dry-run", run(command, "--help").stdout)
        self.assertIn("--dry-run", run("pr", "--help").stdout)

    def test_messages_are_prefixed_with_the_command(self) -> None:
        pattern = re.compile(r'"(coder-ai[a-z ]*): ')
        prefixes = set()
        for path in (REPO / "src").rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            prefixes |= set(pattern.findall(path.read_text(encoding="utf-8")))
        self.assertTrue(prefixes)
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                self.assertTrue(prefix.startswith("coder-ai"),
                                f"{prefix!r} does not name the command")

    def test_no_command_teaches_a_name_that_is_not_on_path(self) -> None:
        """`val` is reached through `coder-ai val`; its help must not say otherwise."""
        result = subprocess.run([str(REPO / "val" / "val")], capture_output=True, text=True)
        text = result.stdout + result.stderr
        self.assertIn("coder-ai val", text)
        self.assertNotIn("usage: val ", text)

    def test_the_legacy_name_announces_itself_as_legacy(self) -> None:
        result = subprocess.run([str(REPO / "bin" / "coder-ai-os"), "help"],
                                capture_output=True, text=True)
        self.assertIn("DEPRECATED", result.stdout)
        self.assertIn("coder-ai", result.stdout)

    def test_a_bad_flag_explains_itself_rather_than_crashing(self) -> None:
        for args in (("prove", "--nope"), ("ship", "--nope"), ("pr", "status", "--nope")):
            with self.subTest(args=args):
                result = run(*args)
                self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_the_readme_and_help_agree(self) -> None:
        readme = (REPO / "README.md").read_text()
        listed = set(re.findall(r"^    coder-ai ([a-z-]+)", run("help").stdout, re.M))
        table = set(re.findall(r"`coder-ai ([a-z-]+)", readme))
        missing = {c for c in listed if c not in table} - {"init", "install", "help", "run"}
        self.assertEqual(missing, set(), f"commands absent from the README: {missing}")



class WhatGetsCommitted(unittest.TestCase):
    """Local state stays local; exactly one shared file is the exception."""

    def test_setup_keeps_the_runtime_folder_clone_local(self) -> None:
        text = (REPO / "install.sh").read_text()
        self.assertIn("/.coder-ai/*", text)
        self.assertIn("!/.coder-ai/delivery.yaml", text)
        self.assertNotIn("\n/.coder-ai/\n", text,
                         "excluding the directory makes the contract unshareable")

    def test_this_repo_keeps_its_own_workspace_local(self) -> None:
        ignore = (REPO / ".gitignore").read_text()
        self.assertIn("\n.ai/\n", ignore, "the .ai workspace must stay local")
        import subprocess
        tracked = subprocess.run(["git", "ls-files", ".ai/", ".coder-ai/"], cwd=str(REPO),
                                 capture_output=True, text=True).stdout.strip()
        self.assertEqual(tracked, "", f"local data is tracked: {tracked[:200]}")

    def test_the_readme_states_what_is_committed(self) -> None:
        readme = (REPO / "README.md").read_text()
        self.assertIn("kept clone-local", readme)

if __name__ == "__main__":
    unittest.main(verbosity=2)
