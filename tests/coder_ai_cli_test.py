"""One CLI, one name: `coder-ai` covers the whole harness and says where you are."""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from coderai.status import Status, collect, render

REPO = Path(__file__).resolve().parents[1]
RIG = REPO / "bin" / "coder-ai"


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([str(RIG), *args], cwd=str(cwd or REPO), capture_output=True,
                          text=True, timeout=120)


class OneEntryPoint(unittest.TestCase):
    def test_help_groups_every_feature(self) -> None:
        result = run("help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("coder-ai install", "coder-ai setup", "coder-ai sync", "coder-ai init",
                        "coder-ai status", "coder-ai find", "coder-ai pr", "coder-ai val",
                        "coder-ai tasks", "coder-ai run", "coder-ai verify", "coder-ai doctor"):
            with self.subTest(command=command):
                self.assertIn(command, result.stdout)

    def test_help_is_not_polluted_by_the_script_body(self) -> None:
        result = run("help")
        self.assertNotIn("set -euo pipefail", result.stdout)
        self.assertNotIn("BASH_SOURCE", result.stdout)

    def test_bare_invocation_shows_help_not_an_error(self) -> None:
        result = run()
        self.assertEqual(result.returncode, 0)
        self.assertIn("one CLI", result.stdout)

    def test_everything_else_forwards_to_the_harness(self) -> None:
        result = run("verify")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("verify", result.stdout)

    def test_topic_help(self) -> None:
        result = run("help", "pr")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coder-ai pr <pr|branch|url>", result.stdout)

    def test_config_topic_points_at_the_settings(self) -> None:
        result = run("help", "config")
        self.assertIn("config/README.md", result.stdout)

    def test_the_legacy_name_still_works(self) -> None:
        result = subprocess.run([str(REPO / "bin" / "coder-ai-os"), "verify"],
                                cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_both_commands_are_linked_by_the_installer(self) -> None:
        text = (REPO / "install.sh").read_text()
        self.assertIn('link_cli "$REPO_DIR/bin/coder-ai"', text)
        self.assertIn('link_cli "$REPO_DIR/bin/coder-ai-os"', text)


class TheRealEntryPoint(unittest.TestCase):
    """Every command a user can type, run the way they actually type it.

    The unit tests imported `parse_args` directly and passed. Running the CLI for
    real took a different path — `python -m …cli` executes the module a second time
    as `__main__`, so the parsed object's class was not the class `dispatch`
    compared against, and every `coder-ai pr <n> -- <provider>` crashed with a
    traceback. No test had ever invoked the binary the way a person does.
    """

    def assert_clean(self, result: subprocess.CompletedProcess, *args: str) -> None:
        combined = result.stdout + result.stderr
        self.assertNotIn("Traceback", combined,
                         f"`coder-ai {' '.join(args)}` crashed:\n{combined}")
        self.assertNotIn("AttributeError", combined)

    def test_supervising_a_pr_reaches_the_supervisor_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for args in (("pr", "1420", "--", "claude"),
                         ("pr", "1420", "--watch", "0", "--", "codex"),
                         ("pr", "feature/x", "--", "claude"),
                         ("pr", "1420", "--dry-run", "--", "claude")):
                with self.subTest(args=args):
                    result = run(*args, cwd=Path(tmp))
                    self.assert_clean(result, *args)
                    self.assertEqual(result.returncode, 2,
                                     "a directory with no repository should fail cleanly")

    def test_session_commands_reach_their_handlers(self) -> None:
        for args in (("pr", "status"), ("pr", "status", "1420"),
                     ("pr", "log", "1420"), ("pr", "stop", "1420"),
                     ("pr", "attach", "1420")):
            with self.subTest(args=args):
                result = run(*args)
                self.assert_clean(result, *args)
                self.assertIn(result.returncode, (0, 1))

    def test_bad_arguments_still_explain_themselves(self) -> None:
        for args in (("pr", "1420", "claude"), ("pr", "1420", "--watch", "banana",
                                                "--", "claude"),
                     ("pr", "1420", "--", "aider")):
            with self.subTest(args=args):
                result = run(*args)
                self.assert_clean(result, *args)
                self.assertEqual(result.returncode, 2)

    def test_the_entry_point_imports_the_cli_exactly_once(self) -> None:
        """A second copy of the module is what broke dispatch."""
        entry = REPO / "src" / "coderai" / "pr_automation" / "__main__.py"
        self.assertTrue(entry.is_file(), "the package needs a single entry module")
        for script in (REPO / "bin" / "coder-ai", REPO / "bin" / "coder-ai-os"):
            with self.subTest(script=script.name):
                self.assertNotIn("coderai.pr_automation.cli", script.read_text(),
                                 "running the cli module directly duplicates its classes")

    def test_dispatch_does_not_depend_on_class_identity(self) -> None:
        from coderai.pr_automation import runner

        source = (REPO / "src" / "coderai" / "pr_automation" / "runner.py").read_text()
        self.assertNotIn("isinstance(invocation", source)

        class LooksLikeARun:
            kind = "run"
            target, background, dry_run = "1", False, False
            provider, provider_argv = "claude", ("claude",)

            class watch:
                mode, seconds = "single", 0

        from unittest import mock
        with mock.patch.object(runner, "run", return_value=0) as ran:
            runner.dispatch(LooksLikeARun())
        ran.assert_called_once()


class Find(unittest.TestCase):
    @contextlib.contextmanager
    def atlas(self, *symbols: str):
        """A project carrying a code atlas. This repo's own `.ai/` is never committed,
        so searching the checkout only worked on a machine that had run `coder-ai sync`."""
        with tempfile.TemporaryDirectory() as tmp:
            maps = Path(tmp) / ".ai" / "symbols"
            maps.mkdir(parents=True)
            (maps / "coderai.md").write_text(
                "# coderai\n\n| symbol | file | line |\n|---|---|---|\n"
                + "".join(f"| {name} | src/coderai/pr_automation/wake.py | 1 |\n"
                          for name in symbols))
            yield Path(tmp)

    def test_finds_a_symbol_in_the_atlas(self) -> None:
        with self.atlas("_provider_command") as project:
            result = run("find", "_provider_command", cwd=project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("_provider_command", result.stdout)

    def test_a_miss_explains_itself(self) -> None:
        with self.atlas("_provider_command") as project:
            result = run("find", "definitelyNotASymbolAnywhere", cwd=project)
        self.assertEqual(result.returncode, 1)
        self.assertIn("coder-ai sync", result.stdout + result.stderr)

    def test_needs_an_argument(self) -> None:
        result = run("find")
        self.assertEqual(result.returncode, 1)
        self.assertIn("needs a symbol", result.stderr)

    def test_a_project_without_an_atlas_is_told_what_to_do(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run("find", "anything", cwd=Path(tmp))
            self.assertEqual(result.returncode, 1)
            self.assertIn("coder-ai sync", result.stderr)


class StatusOverview(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = self.root / "my-app"
        (self.project / ".git").mkdir(parents=True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.state = self.root / "sessions"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def status(self) -> Status:
        return collect(self.project, state_root=self.state, home=self.home)

    def test_a_fresh_project_is_told_to_set_up(self) -> None:
        status = self.status()
        self.assertFalse(status.set_up)
        text = render(status)
        self.assertIn("not set up — run: coder-ai setup", text)
        self.assertIn("coder-ai setup", "\n".join(status.next_steps()))

    def test_a_set_up_project_reports_its_atlas_and_task(self) -> None:
        (self.project / ".coder-ai").mkdir()
        symbols = self.project / ".ai" / "symbols"
        symbols.mkdir(parents=True)
        (symbols / "INDEX.md").write_text("# index\n")
        (symbols / "core.md").write_text("# core\n")
        memory = self.project / ".ai" / "memory"
        memory.mkdir(parents=True)
        memory.joinpath("CURRENT.md").write_text(
            "# Current task checkpoint\n\n# Retry handling repair — 2026-09-10\n\nnotes\n")
        status = self.status()
        self.assertTrue(status.set_up)
        self.assertEqual(status.atlas_maps, 2)
        self.assertEqual(status.task, "Retry handling repair — 2026-09-10")
        text = render(status)
        self.assertIn("2 maps", text)
        self.assertIn("Retry handling repair", text)

    def test_agents_are_detected_from_their_managed_block(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        (claude / "CLAUDE.md").write_text("<!-- coder-ai-os:managed -->\nrules\n")
        installed = dict(self.status().agents)
        self.assertTrue(installed["Claude"])
        self.assertFalse(installed["Codex"])

    def test_an_unmanaged_file_does_not_count_as_installed(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        (claude / "CLAUDE.md").write_text("my own notes\n")
        self.assertFalse(dict(self.status().agents)["Claude"])

    def test_enabled_extras_are_listed(self) -> None:
        (self.project / ".coder-ai" / "val").mkdir(parents=True)
        (self.project / ".coder-ai" / "val" / "run").write_text("#!/bin/sh\n")
        (self.project / ".coder-ai" / "tasks").mkdir(parents=True)
        (self.project / ".coder-ai" / "tasks" / "settings.json").write_text("{}")
        text = render(self.status())
        self.assertIn("visual loop ready", text)
        self.assertIn("project tasks on", text)

    def test_pr_sessions_for_this_project_are_shown(self) -> None:
        from coderai.pr_automation.state import Session, ensure_dir, save_session, session_dir

        subprocess.run(["git", "init", "-q", str(self.project)], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(self.project), "remote", "add", "origin",
                        "git@github.com:org/my-app.git"], check=True, capture_output=True)
        session = Session(host="github.com", owner="org", repo="my-app", number=1420,
                          head_branch="feature/x", base_branch="main", remote="origin",
                          provider="claude", provider_argv=["claude"])
        session.state = "WAITING_FOR_REVIEW"
        ensure_dir(session_dir("org", "my-app", 1420, root=self.state), self.state)
        save_session(session, root=self.state)
        text = render(self.status())
        self.assertIn("#1420 WAITING_FOR_REVIEW (claude)", text)

    def test_other_projects_sessions_are_only_counted(self) -> None:
        from coderai.pr_automation.state import Session, ensure_dir, save_session, session_dir

        session = Session(host="github.com", owner="other", repo="thing", number=99,
                          head_branch="f", base_branch="main", remote="origin",
                          provider="codex", provider_argv=["codex"])
        session.state = "READY"
        ensure_dir(session_dir("other", "thing", 99, root=self.state), self.state)
        save_session(session, root=self.state)
        text = render(self.status())
        self.assertIn("1 in other projects", text)

    def test_terminal_sessions_are_not_reported_as_running(self) -> None:
        from coderai.pr_automation.state import Session, ensure_dir, save_session, session_dir

        session = Session(host="github.com", owner="other", repo="thing", number=99,
                          head_branch="f", base_branch="main", remote="origin",
                          provider="codex", provider_argv=["codex"])
        session.state = "MERGED"
        ensure_dir(session_dir("other", "thing", 99, root=self.state), self.state)
        save_session(session, root=self.state)
        self.assertIn("PR sessions none", render(self.status()))

    def test_rendering_never_raises_on_a_bare_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            render(collect(Path(tmp), state_root=self.state, home=self.home))

    def test_the_command_runs_and_offers_json(self) -> None:
        result = run("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coder-ai ·", result.stdout)
        result = run("status", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        import json
        self.assertIn("agents", json.loads(result.stdout))



class FeatureTableIsRunnable(unittest.TestCase):
    """Every 'Try it' cell must be something a user can actually type."""

    def setUp(self) -> None:
        import re

        readme = (REPO / "README.md").read_text()
        # "Everything it does" is several three-column tables (icon | feature | try it),
        # one per group, with an empty header row. Check every row in all of them.
        tables = re.findall(r"^\|(?: *\|){3}\n\|---\|---\|---\|\n((?:\|.*\n)+)",
                            readme, re.MULTILINE)
        self.assertTrue(tables, "the feature table is missing")
        self.rows = [line for table in tables
                     for line in table.splitlines() if line.strip()]
        self.help = run("help").stdout

    def test_the_table_covers_every_feature(self) -> None:
        self.assertGreaterEqual(len(self.rows), 8)

    def test_every_cell_is_a_real_command(self) -> None:
        commands = [row.rsplit("|", 2)[1].strip() for row in self.rows]
        for cell in commands:
            with self.subTest(cell=cell):
                self.assertTrue(cell.startswith("`"), f"{cell!r} is prose, not a command")

    def test_rig_commands_in_the_table_exist(self) -> None:
        for row in self.rows:
            cell = row.rsplit("|", 2)[1].strip().strip("`")
            if not cell.startswith("coder-ai "):
                continue
            verb = cell.split()[1]
            with self.subTest(command=cell):
                self.assertIn(f"coder-ai {verb}", self.help,
                              f"`{cell}` is advertised but not in `coder-ai help`")

    def test_slash_commands_in_the_table_are_installed(self) -> None:
        available = {path.stem for path in (REPO / "build" / "commands" / "claude").glob("*.md")}
        self.assertTrue(available, "no compiled commands to check against")
        for row in self.rows:
            cell = row.rsplit("|", 2)[1].strip()
            for token in cell.split():
                token = token.strip("`")
                if token.startswith("/"):
                    with self.subTest(command=token):
                        self.assertIn(token.lstrip("/"), available,
                                      f"{token} is advertised but not compiled")

    def test_the_command_reference_matches_the_cli(self) -> None:
        import re

        readme = (REPO / "README.md").read_text()
        table = re.search(r"## Commands\n+\|(?: *\|){2}\n\|---\|---\|\n((?:\|.*\n)+)",
                          readme)
        self.assertIsNotNone(table, "the command reference is missing")
        assert table is not None
        for line in table.group(1).splitlines():
            cell = line.split("|")[1].strip()
            verb = cell.strip("`").replace("coder-ai ", "").split()[0].strip("`")
            if verb in {"compile", "help"}:
                continue
            with self.subTest(command=cell):
                self.assertIn(verb, self.help + "compile diff profile",
                              f"{cell} is documented but not offered by the CLI")
if __name__ == "__main__":
    unittest.main(verbosity=2)
