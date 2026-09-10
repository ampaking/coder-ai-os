"""Task 19 — one config kernel, validated by the compiler, installed by install.sh."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from coderai.pr_automation.config import (
    ConfigError, DEFAULT_POLL, PrConfig, load, parse_duration, validate,
)

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config" / "pr_automation.yaml"


class Durations(unittest.TestCase):
    def test_units(self) -> None:
        self.assertEqual(parse_duration("45s", default=0), 45)
        self.assertEqual(parse_duration("30m", default=0), 1800)
        self.assertEqual(parse_duration("1h", default=0), 3600)
        self.assertEqual(parse_duration("7d", default=0), 604800)
        self.assertEqual(parse_duration("900", default=0), 900)

    def test_default_and_rejection(self) -> None:
        self.assertEqual(parse_duration(None, default=42), 42)
        for value in ("soon", "", "1 hour", "-5m"):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                parse_duration(value, default=0)


class ShippedConfig(unittest.TestCase):
    def test_loads_with_the_documented_defaults(self) -> None:
        config = load()
        self.assertEqual(config.watch_default, 3600)
        self.assertEqual(config.poll, DEFAULT_POLL)
        self.assertFalse(config.commit_trailers)
        self.assertEqual(config.commit_identity, "repository")
        self.assertEqual(config.cross_provider_review, "prefer-opposite")

    def test_declares_both_execution_profiles(self) -> None:
        text = CONFIG.read_text()
        self.assertIn("git_write: false", text)
        self.assertIn("git_write: scoped", text)

    def test_a_missing_file_falls_back_to_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load(Path(tmp) / "absent.yaml")
        self.assertEqual(config, PrConfig())


class ConfigCannotWidenTheBoundary(unittest.TestCase):
    def test_normal_profile_may_not_be_given_git_write(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            validate({"execution_profiles": {"normal": {"git_write": True}}})
        self.assertIn("may never elevate ordinary sessions", str(caught.exception))

    def test_unscoped_git_write_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            validate({"execution_profiles": {"pr_automation": {"git_write": True}}})
        with self.assertRaises(ConfigError):
            validate({"execution_profiles": {"pr_automation": {"git_write": "all"}}})

    def test_deploy_and_secrets_stay_off_in_both_profiles(self) -> None:
        for profile in ("normal", "pr_automation"):
            for key in ("deploy", "secrets"):
                with self.subTest(profile=profile, key=key), self.assertRaises(ConfigError):
                    validate({"execution_profiles": {profile: {key: True}}})

    def test_ai_commit_trailers_cannot_be_enabled(self) -> None:
        with self.assertRaises(ConfigError):
            validate({"commit": {"trailers": True}})

    def test_the_allow_deny_matrix_is_code_not_configuration(self) -> None:
        """Nothing in the config file can name a git operation to permit."""
        text = CONFIG.read_text()
        for forbidden in ("--force", "force_push", "allow:", "deny:", "merge:"):
            self.assertNotIn(forbidden, text)


class CompilerIntegration(unittest.TestCase):
    def compile(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(REPO / "bin" / "compile"), *args],
                              cwd=str(REPO), capture_output=True, text=True)

    def test_validate_accepts_the_shipped_config(self) -> None:
        result = self.compile("--validate")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("FAIL", result.stdout)

    def test_check_stays_within_budget(self) -> None:
        result = self.compile("--check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("all outputs within budget", result.stdout)

    def test_the_compiler_refuses_an_elevated_normal_profile(self) -> None:
        import importlib.machinery
        import importlib.util

        loader = importlib.machinery.SourceFileLoader("compilemod",
                                                      str(REPO / "bin" / "compile"))
        spec = importlib.util.spec_from_loader("compilemod", loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        problems = module.pr_automation_problems({
            "pr_automation": {"execution_profiles": {"normal": {"git_write": True}}}})
        self.assertTrue(problems)
        self.assertIn("normal.git_write", problems[0])

    def test_a_machine_overlay_cannot_override_pr_automation(self) -> None:
        text = (REPO / "bin" / "compile").read_text()
        self.assertIn('over_all.pop("pr_automation", None)', text)
        self.assertIn('over_all.pop("safety", None)', text)


class InstallerIntegration(unittest.TestCase):
    def test_install_links_the_cli(self) -> None:
        text = (REPO / "install.sh").read_text()
        self.assertIn('link_cli "$REPO_DIR/bin/coder-ai"', text)
        self.assertTrue((REPO / "bin" / "coder-ai").is_file())
        self.assertTrue((REPO / "bin" / "coder-ai").stat().st_mode & 0o111)

    def test_verify_lists_the_pr_artifacts(self) -> None:
        result = subprocess.run([str(REPO / "bin" / "coder-ai-os"), "verify"],
                                cwd=str(REPO), capture_output=True, text=True)
        self.assertIn("pr-engineer/SKILL.md", result.stdout)

    def test_the_pr_route_is_documented_in_help(self) -> None:
        result = subprocess.run([str(REPO / "bin" / "coder-ai-os"), "help"],
                                cwd=str(REPO), capture_output=True, text=True)
        self.assertIn("coder-ai pr", result.stdout)

    def test_installer_syntax_is_valid(self) -> None:
        for script in (REPO / "install.sh", REPO / "bin" / "coder-ai",
                       REPO / "bin" / "coder-ai-os"):
            with self.subTest(script=script.name):
                result = subprocess.run(["bash", "-n", str(script)], capture_output=True,
                                        text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


class Documentation(unittest.TestCase):
    """The README is the index; docs/pr-automation.md carries the detail."""

    def setUp(self) -> None:
        self.readme = (REPO / "README.md").read_text()
        self.guide = (REPO / "docs" / "pr-automation.md").read_text()
        self.flat = re.sub(r"\s+", " ", self.readme + "\n" + self.guide)

    def test_the_readme_introduces_pr_automation_and_links_the_guide(self) -> None:
        self.assertIn("PR automation", self.readme)
        self.assertIn("docs/pr-automation.md", self.readme)
        self.assertIn("coder-ai pr 1420 -- claude", re.sub(r"\s+", " ", self.readme))

    def test_the_readme_stays_scannable(self) -> None:
        """A wall of text is a README nobody reads."""
        # Line count scales with the number of features, so it is a loose bound.
        # Paragraph length is the real signal for "this became a manual".
        lines = self.readme.splitlines()
        self.assertLess(len(lines), 300, "README has grown back into a manual")
        # Tables and code blocks are scannable by construction; prose is what
        # turns a README into a manual, so only prose is measured.
        prose = [item for item in re.split(r"\n\s*\n", self.readme)
                 if not item.lstrip().startswith(("|", "```", "```sh"))
                 and "```" not in item and "|---" not in item]
        longest = max(len(re.sub(r"\s+", " ", item)) for item in prose)
        self.assertLess(longest, 500, "a paragraph has grown past skimmable length")

    def test_both_modes_are_documented(self) -> None:
        self.assertIn("no `git commit`, no `git push`", self.flat)
        self.assertIn("coder-ai pr", self.flat)

    def test_the_cli_contract_and_watch_values_are_documented(self) -> None:
        self.assertIn("Everything after `--` is the provider's own command", self.flat)
        self.assertIn("until-close", self.flat)
        self.assertIn("coder-ai pr status", self.flat)

    def test_the_enforced_boundary_and_its_end_are_documented(self) -> None:
        lowered = self.flat.lower()
        self.assertIn("refused by the environment", lowered)
        self.assertIn("inherits nothing", lowered)

    def test_resolution_handling_is_documented(self) -> None:
        self.assertIn("PR author", self.guide)
        self.assertIn("re-opened", self.guide)

    def test_every_feature_has_a_way_to_run_it(self) -> None:
        for command in ("coder-ai setup", "coder-ai sync", "coder-ai status", "coder-ai verify",
                        "coder-ai find", "coder-ai pr", "coder-ai val", "coder-ai tasks",
                        "coder-ai prove", "coder-ai ship", "update-ai-context.sh"):
            with self.subTest(command=command):
                self.assertIn(command, self.readme)

    def test_the_readme_uses_one_cli_name(self) -> None:
        """Mixed entry points are the usability bug this replaced."""
        import re

        stale = re.findall(r"coder-ai-os (?:install|setup|sync|init|verify|doctor|"
                           r"tasks|val|run|compile|diff|profile)\b", self.readme)
        self.assertEqual(stale, [], f"README still invokes the old CLI name: {stale}")
        self.assertIn("`coder-ai-os` still works", self.readme)

    def test_internal_links_resolve(self) -> None:
        for path in [REPO / "README.md", *(REPO / "docs").glob("*.md")]:
            for text, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)",
                                           path.read_text()):
                if target.startswith(("http", "#")):
                    continue
                with self.subTest(source=path.name, link=target):
                    self.assertTrue((path.parent / target).exists(),
                                    f"{path.name}: {text} -> {target}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
