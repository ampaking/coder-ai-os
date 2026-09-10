"""Task 01 — `coder-ai pr` argument contract: pure, total, side-effect free."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from coderai.pr_automation.cli import (
    DEFAULT_WATCH_SECONDS,
    CliError,
    PrCommand,
    PrRun,
    parse_args,
    parse_watch,
)

REPO = Path(__file__).resolve().parents[1]


class ProviderArgvPassthrough(unittest.TestCase):
    def test_claude_model_flag_survives(self) -> None:
        run = parse_args(["1420", "--", "claude", "--model", "claude-fable-5"])
        assert isinstance(run, PrRun)
        self.assertEqual(run.target, "1420")
        self.assertEqual(run.provider, "claude")
        self.assertEqual(run.provider_argv, ("claude", "--model", "claude-fable-5"))

    def test_codex_model_flag_survives(self) -> None:
        run = parse_args(["feature/worker-retry", "--", "codex", "--model", "gpt-5.6-sol"])
        assert isinstance(run, PrRun)
        self.assertEqual(run.target, "feature/worker-retry")
        self.assertEqual(run.provider_argv, ("codex", "--model", "gpt-5.6-sol"))

    def test_rig_never_consumes_provider_options(self) -> None:
        # A provider flag that collides with a coder-ai-os flag must reach the provider intact.
        run = parse_args(["1420", "--watch", "2h", "--", "claude", "--watch", "--bg", "x"])
        assert isinstance(run, PrRun)
        self.assertEqual(run.watch.seconds, 7200)
        self.assertFalse(run.background)
        self.assertEqual(run.provider_argv, ("claude", "--watch", "--bg", "x"))

    def test_only_the_first_double_dash_splits(self) -> None:
        run = parse_args(["1420", "--", "claude", "--", "literal"])
        assert isinstance(run, PrRun)
        self.assertEqual(run.provider_argv, ("claude", "--", "literal"))

    def test_url_target_and_background(self) -> None:
        run = parse_args(
            ["https://github.com/org/repo/pull/1420", "--bg", "--watch", "4h", "--", "claude"]
        )
        assert isinstance(run, PrRun)
        self.assertEqual(run.target, "https://github.com/org/repo/pull/1420")
        self.assertTrue(run.background)
        self.assertEqual(run.watch.seconds, 4 * 3600)


class WatchDurations(unittest.TestCase):
    def test_default_is_one_hour(self) -> None:
        run = parse_args(["1420", "--", "claude"])
        assert isinstance(run, PrRun)
        self.assertEqual(run.watch.mode, "bounded")
        self.assertEqual(run.watch.seconds, DEFAULT_WATCH_SECONDS)
        self.assertEqual(DEFAULT_WATCH_SECONDS, 3600)

    def test_zero_is_a_single_pass(self) -> None:
        window = parse_watch("0")
        self.assertTrue(window.is_single_pass)
        self.assertFalse(window.is_unbounded)

    def test_units(self) -> None:
        self.assertEqual(parse_watch("90s").seconds, 90)
        self.assertEqual(parse_watch("30m").seconds, 1800)
        self.assertEqual(parse_watch("3h").seconds, 10800)
        self.assertEqual(parse_watch("8h").seconds, 28800)
        self.assertEqual(parse_watch("1d").seconds, 86400)

    def test_until_close(self) -> None:
        window = parse_watch("until-close")
        self.assertTrue(window.is_unbounded)
        self.assertFalse(window.is_single_pass)

    def test_equals_form(self) -> None:
        run = parse_args(["1420", "--watch=30m", "--", "claude"])
        assert isinstance(run, PrRun)
        self.assertEqual(run.watch.seconds, 1800)

    def test_rejects_garbage(self) -> None:
        for value in ("banana", "", "2 hours", "-5m", "3x", "h"):
            with self.subTest(value=value), self.assertRaises(CliError):
                parse_watch(value)

    def test_rejects_absurd_window(self) -> None:
        with self.assertRaises(CliError) as caught:
            parse_watch("30d")
        self.assertIn("until-close", str(caught.exception))

    def test_missing_duration_value(self) -> None:
        with self.assertRaises(CliError):
            parse_args(["1420", "--watch", "--", "claude"])


class Rejections(unittest.TestCase):
    def test_missing_double_dash_gives_a_fix_hint(self) -> None:
        with self.assertRaises(CliError) as caught:
            parse_args(["1420", "claude"])
        self.assertIn("--", str(caught.exception))

    def test_no_provider_after_double_dash(self) -> None:
        with self.assertRaises(CliError):
            parse_args(["1420", "--"])

    def test_option_instead_of_provider(self) -> None:
        with self.assertRaises(CliError):
            parse_args(["1420", "--", "--model", "x"])

    def test_unsupported_provider_names_the_supported_ones(self) -> None:
        with self.assertRaises(CliError) as caught:
            parse_args(["1420", "--", "aider"])
        message = str(caught.exception)
        self.assertIn("claude", message)
        self.assertIn("codex", message)

    def test_unknown_rig_option(self) -> None:
        with self.assertRaises(CliError) as caught:
            parse_args(["1420", "--turbo", "--", "claude"])
        self.assertIn("after '--'", str(caught.exception))

    def test_empty_argv(self) -> None:
        with self.assertRaises(CliError):
            parse_args([])

    def test_option_before_target(self) -> None:
        with self.assertRaises(CliError):
            parse_args(["--watch", "1h", "1420", "--", "claude"])


class Subcommands(unittest.TestCase):
    def test_status_without_target(self) -> None:
        command = parse_args(["status"])
        assert isinstance(command, PrCommand)
        self.assertEqual((command.name, command.target), ("status", None))

    def test_status_with_target(self) -> None:
        command = parse_args(["status", "1420"])
        assert isinstance(command, PrCommand)
        self.assertEqual(command.target, "1420")

    def test_attach_stop_log_require_a_pr(self) -> None:
        for name in ("attach", "stop", "log"):
            with self.subTest(name=name):
                command = parse_args([name, "1420"])
                assert isinstance(command, PrCommand)
                self.assertEqual((command.name, command.target), (name, "1420"))
                with self.assertRaises(CliError):
                    parse_args([name])

    def test_subcommand_rejects_provider_command(self) -> None:
        with self.assertRaises(CliError):
            parse_args(["stop", "1420", "--", "claude"])


class Purity(unittest.TestCase):
    def test_parsing_touches_nothing(self) -> None:
        """Parsing must not create files, run git/gh, or reach the network."""
        with tempfile.TemporaryDirectory() as tmp:
            before = os.getcwd()
            os.chdir(tmp)
            try:
                for argv in (
                    ["1420", "--", "claude"],
                    ["status"],
                    ["feature/x", "--watch", "0", "--bg", "--", "codex"],
                ):
                    parse_args(argv)
                self.assertEqual(list(Path(tmp).iterdir()), [])
            finally:
                os.chdir(before)


class Shim(unittest.TestCase):
    def _run(self, argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(argv, capture_output=True, text=True, cwd=cwd, timeout=60)

    def test_rig_shim_help(self) -> None:
        result = self._run([str(REPO / "bin" / "coder-ai"), "help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coder-ai pr", result.stdout)

    def test_rig_shim_resolves_through_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "coder-ai"
            link.symlink_to(REPO / "bin" / "coder-ai")
            result = self._run([str(link), "pr", "--help"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("coder-ai pr <pr|branch|url>", result.stdout)

    def test_bad_arguments_exit_two_without_side_effects(self) -> None:
        result = self._run([str(REPO / "bin" / "coder-ai"), "pr", "1420", "--watch", "banana",
                            "--", "claude"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("--watch", result.stderr)

    def test_coder_ai_os_pr_route_exists(self) -> None:
        result = self._run([str(REPO / "bin" / "coder-ai-os"), "pr", "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coder-ai pr", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
