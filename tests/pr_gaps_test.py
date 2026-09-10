"""Regressions for the gaps found reviewing the seams between modules.

Each of these was reachable in production and invisible to the per-module tests,
because each lives in the wiring rather than in a single module.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import pr_repo  # noqa: E402
from pr_github import FakeGitHub  # noqa: E402

from coderai.pr_automation import audit, providers  # noqa: E402
from coderai.pr_automation.cli import PrRun, parse_args  # noqa: E402
from coderai.pr_automation.decision import DONE, REVIEW_NEEDED  # noqa: E402
from coderai.pr_automation.github import Gh, GhResult  # noqa: E402
from coderai.pr_automation.state import load_session, session_dir  # noqa: E402
from coderai.pr_automation.supervisor import run_session  # noqa: E402
from coderai.pr_automation.wake import CommandResult  # noqa: E402

HEAD_BRANCH = "feature/worker-retry"


class Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class IdentityHub(FakeGitHub):
    """Adds `gh api user`, so CAOS can tell its own posts from real feedback."""

    def __init__(self, login: str = "caos-bot", **kwargs) -> None:
        super().__init__(**kwargs)
        self.login = login

    def __call__(self, argv, cwd, timeout):
        argv = list(argv)
        if argv[1:3] == ["api", "user"]:
            self.calls.append(tuple(argv))
            return GhResult(tuple(argv), 0, json.dumps(self.login), "", 3)
        return super().__call__(argv, cwd, timeout)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.origin, self.clone = pr_repo.build(self.root)
        self.hub = IdentityHub()
        self.hub.pr["headRefOid"] = pr_repo.git(self.clone, "rev-parse",
                                                f"origin/{HEAD_BRANCH}")
        self.gh = Gh(self.clone, runner=self.hub)
        self.clock = Clock()
        self.state = self.root / "sessions"
        self.trees = self.root / "worktrees"
        self.lines: list[str] = []
        self.prompts: list[str] = []
        self.roles: list[str] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def out(self, text) -> None:
        self.lines.append(str(text))

    def agent(self, script: list[dict]):
        remaining = list(script)

        def runner(argv, cwd, env, timeout, stdin):
            self.prompts.append(stdin)
            step = remaining.pop(0) if remaining else {"state": "NO_ACTION",
                                                       "summary": "nothing to do"}
            action = step.pop("_do", None)
            if action:
                action()
            body = json.dumps(step)
            if "--output-last-message" in argv:
                Path(argv[argv.index("--output-last-message") + 1]).write_text(body)
                body = ""
            return CommandResult(tuple(argv), step.pop("_rc", 0), body,
                                 step.pop("_stderr", ""), 5)

        return runner

    def supervise(self, argv: list[str], runner):
        run = parse_args(argv)
        assert isinstance(run, PrRun)
        return run_session(run, self.clone, gh=self.gh, state_root=self.state,
                           worktree_root=self.trees, clock=self.clock, out=self.out,
                           runner=runner)

    def directory(self) -> Path:
        return session_dir("org", "aiila", 1420, root=self.state)

    def events(self) -> list[str]:
        return [item["event"] for item in audit.read(self.directory())]


class OwnRepliesDoNotWakeUs(Base):
    def test_a_reply_the_agent_posted_is_not_read_as_new_feedback(self) -> None:
        """The agent answering a reviewer must not re-trigger itself."""
        self.hub.add_comment("reviewer", "Why is Redis used here?")

        def reply():
            self.hub.add_comment(self.hub.login, "Redis is the queue backend; see docs/queue.md")

        report = self.supervise(["1420", "--watch", "30m", "--", "codex"],
                                self.agent([{"state": "REPLY_NEEDED",
                                             "summary": "answered the Redis question",
                                             "replies": ["explained the queue backend"],
                                             "_do": reply}]))
        self.assertEqual(report.result.wakes, 1,
                         "the agent's own reply woke it again")

    def test_someone_elses_comment_during_our_run_still_wakes_us(self) -> None:
        def reply_and_new_feedback():
            self.hub.add_comment(self.hub.login, "fixed")
            self.hub.add_comment("reviewer", "one more thing about retries")

        report = self.supervise(["1420", "--watch", "30m", "--", "codex"],
                                self.agent([{"state": "DONE", "summary": "done",
                                             "_do": reply_and_new_feedback}]))
        self.assertEqual(report.result.wakes, 2)

    def test_the_identity_is_fetched_once_per_session(self) -> None:
        self.supervise(["1420", "--watch", "0", "--", "codex"],
                       self.agent([{"state": "NO_ACTION", "summary": "ok"}]))
        lookups = [call for call in self.hub.calls if call[1:3] == ("api", "user")]
        self.assertEqual(len(lookups), 1)


class PartialSnapshotsAreNotBaselines(Base):
    def test_a_partial_collection_never_becomes_the_baseline(self) -> None:
        """Otherwise the next complete poll reads restored threads as new activity."""
        self.hub.add_thread("codex", "P1: retry can run twice", bot=True)
        polls = {"count": 0}

        def collect_with_a_blip():
            # poll 1 = first snapshot, poll 2 = the wake's staleness refresh,
            # poll 3 = the first real watch poll — that is where the blip belongs.
            polls["count"] += 1
            if polls["count"] == 3:
                self.hub.failures = {"api graphql": (1, "server error")}
            else:
                self.hub.failures = {}
            from coderai.pr_automation.snapshot import collect
            return collect(self.gh, "org", "aiila", 1420)

        import coderai.pr_automation.supervisor as supervisor_module
        saved = supervisor_module.collect
        supervisor_module.collect = lambda *a, **k: collect_with_a_blip()
        try:
            report = self.supervise(["1420", "--watch", "20m", "--", "codex"],
                                    self.agent([{"state": "NO_ACTION", "summary": "ok"}]))
        finally:
            supervisor_module.collect = saved

        self.assertEqual(report.result.wakes, 1,
                         "a transient gh failure caused a spurious wake")
        self.assertIn("snapshot_partial", self.events())
        stored = json.loads((self.directory() / "snapshot.json").read_text())
        self.assertFalse(stored["partial"], "a partial snapshot was persisted")
        self.assertEqual(len(stored["review_comments"]), 1)


class ProviderTroubleIsClassified(Base):
    def test_a_rate_limited_provider_is_not_recorded_as_a_hard_failure(self) -> None:
        runner = self.agent([{"state": "NO_ACTION", "summary": "x", "_rc": 1,
                              "_stderr": "Error: 429 Too Many Requests — rate limit"}])

        def broken(argv, cwd, env, timeout, stdin):
            self.prompts.append(stdin)
            return CommandResult(tuple(argv), 1, "",
                                 "Error: 429 Too Many Requests — rate limit reached", 5)

        self.supervise(["1420", "--watch", "0", "--", "codex"], broken)
        health = providers.load(self.directory())
        self.assertEqual(health.get("codex").state, providers.TEMPORARILY_LIMITED)
        self.assertGreater(health.get("codex").retry_after, 0)
        self.assertIn("provider_unhealthy", self.events())

    def test_an_auth_failure_is_named_as_such(self) -> None:
        def logged_out(argv, cwd, env, timeout, stdin):
            return CommandResult(tuple(argv), 1, "", "Invalid API key provided", 5)

        self.supervise(["1420", "--watch", "0", "--", "codex"], logged_out)
        self.assertEqual(providers.load(self.directory()).get("codex").state,
                         providers.AUTH_FAILED)

    def test_a_successful_run_keeps_the_provider_healthy(self) -> None:
        self.supervise(["1420", "--watch", "0", "--", "codex"],
                       self.agent([{"state": "NO_ACTION", "summary": "ok"}]))
        health = providers.load(self.directory())
        self.assertEqual(health.get("codex").state, providers.AVAILABLE)
        self.assertEqual(health.get("codex").failures, 0)


class ReviewNeededActuallyReviews(Base):
    def test_it_triggers_a_read_only_second_opinion(self) -> None:
        def fake_installed(name: str) -> bool:
            return name in {"claude", "codex"}

        from unittest import mock
        with mock.patch("coderai.pr_automation.providers.installed",
                        side_effect=fake_installed):
            self.supervise(["1420", "--watch", "0", "--", "codex"],
                           self.agent([
                               {"state": REVIEW_NEEDED,
                                "summary": "concurrency change deserves a second look"},
                               {"state": "NO_ACTION",
                                "summary": "reviewed; nothing actionable"},
                           ]))
        self.assertIn("review_started", self.events())
        started = [item for item in audit.read(self.directory())
                   if item["event"] == "review_started"]
        self.assertEqual(started[0]["provider"], "claude", "should prefer the opposite provider")
        self.assertTrue(started[0]["opposite"])
        self.assertEqual(len(self.prompts), 2)
        self.assertIn("read-only second opinion", self.prompts[1])
        self.assertIn("Do NOT edit, commit, or push", self.prompts[1])

    def test_a_normal_decision_does_not_trigger_a_review(self) -> None:
        self.supervise(["1420", "--watch", "0", "--", "codex"],
                       self.agent([{"state": DONE, "summary": "done"}]))
        self.assertNotIn("review_started", self.events())

    def test_with_only_one_provider_installed_it_still_reviews(self) -> None:
        """No opposite provider is a reason to review with the same one, not to skip."""
        from unittest import mock
        with mock.patch("coderai.pr_automation.providers.installed",
                        side_effect=lambda name: name == "codex"):
            self.supervise(["1420", "--watch", "0", "--", "codex"],
                           self.agent([
                               {"state": REVIEW_NEEDED, "summary": "look again"},
                               {"state": "NO_ACTION", "summary": "nothing actionable"},
                           ]))
        started = [item for item in audit.read(self.directory())
                   if item["event"] == "review_started"]
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["provider"], "codex")
        self.assertFalse(started[0]["opposite"])

    def test_the_reviewer_never_recurses_into_another_review(self) -> None:
        from unittest import mock
        with mock.patch("coderai.pr_automation.providers.installed",
                        side_effect=lambda name: name in {"claude", "codex"}):
            self.supervise(["1420", "--watch", "0", "--", "codex"],
                           self.agent([
                               {"state": REVIEW_NEEDED, "summary": "look again"},
                               {"state": REVIEW_NEEDED, "summary": "and again"},
                           ]))
        started = [item for item in audit.read(self.directory())
                   if item["event"] == "review_started"]
        self.assertEqual(len(started), 1, "a review must not trigger another review")


class BackgroundLaunch(unittest.TestCase):
    """`--bg` had no coverage at all: it is the path a user hits first."""

    def test_it_resolves_before_detaching_so_errors_are_visible(self) -> None:
        from unittest import mock

        from coderai.pr_automation import runner as runner_module
        from coderai.pr_automation.resolve import ResolveError

        invocation = parse_args(["1420", "--bg", "--watch", "4h", "--", "claude"])
        assert isinstance(invocation, PrRun)
        with mock.patch.object(runner_module, "daemon") as daemon, \
             mock.patch("coderai.pr_automation.resolve.resolve",
                        side_effect=ResolveError("PR_NOT_FOUND", "no such pull request")):
            code = runner_module.run(invocation, cwd=Path("."))
        self.assertEqual(code, 2)
        daemon.spawn.assert_not_called()

    def test_the_detached_command_preserves_watch_and_provider_arguments(self) -> None:
        from unittest import mock

        from coderai.pr_automation import runner as runner_module

        invocation = parse_args(["feature/x", "--bg", "--watch", "4h",
                                 "--", "claude", "--model", "claude-fable-5"])
        assert isinstance(invocation, PrRun)

        class FakePR:
            owner, repo, number = "org", "aiila", 1420

        with mock.patch.object(runner_module, "daemon") as daemon, \
             mock.patch("coderai.pr_automation.resolve.resolve", return_value=FakePR()), \
             mock.patch.object(runner_module, "ensure_dir", return_value=Path(".")):
            daemon.spawn.return_value = 4242
            code = runner_module.run(invocation, cwd=Path("."))
        self.assertEqual(code, 0)
        argv = daemon.spawn.call_args[0][0]
        self.assertIn("1420", argv)
        self.assertIn("--watch", argv)
        self.assertIn("14400s", argv)
        self.assertEqual(argv[-3:], ["claude", "--model", "claude-fable-5"])
        self.assertIn("--", argv)

    def test_until_close_and_single_pass_survive_detaching(self) -> None:
        from coderai.pr_automation.runner import _watch_argument

        self.assertEqual(_watch_argument(parse_args(["1", "--watch", "until-close",
                                                     "--", "claude"])), "until-close")
        self.assertEqual(_watch_argument(parse_args(["1", "--watch", "0",
                                                     "--", "claude"])), "0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
