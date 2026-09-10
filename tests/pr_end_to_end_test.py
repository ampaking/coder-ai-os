"""Task 20 — the §77 production flow, end to end, against fixtures only.

Japanese review feedback -> wake -> fix -> validate -> commit -> push -> CI fails
-> wake -> repair -> CI passes -> READY -> merged -> terminal.

No network, no real provider, no real GitHub — but a real git, a real remote, a
real worktree, and the real guard.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import pr_repo  # noqa: E402
from pr_github import FakeGitHub  # noqa: E402

from coderai.pr_automation import audit, states, ui  # noqa: E402
from coderai.pr_automation.cli import PrRun, WatchWindow, parse_args  # noqa: E402
from coderai.pr_automation.findings import load as load_ledger  # noqa: E402
from coderai.pr_automation.github import Gh  # noqa: E402
from coderai.pr_automation.state import (  # noqa: E402
    Session, ensure_dir, list_sessions, load_session, save_session, session_dir,
)
from coderai.pr_automation.supervisor import run_session, status_rows  # noqa: E402
from coderai.pr_automation.wake import CommandResult  # noqa: E402

HEAD_BRANCH = "feature/worker-retry"

JAPANESE_REQUEST = (
    "なるほどです。\nただproductionでも起こり得るので、\n今回対応した方が良さそうです。"
)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.value = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class FakeAgent:
    """A provider stand-in that actually engineers — through the guarded environment."""

    def __init__(self, hub: FakeGitHub, script: list[dict]) -> None:
        self.hub = hub
        self.script = list(script)
        self.prompts: list[str] = []
        self.calls = 0

    def __call__(self, argv, cwd, env, timeout, stdin) -> CommandResult:
        self.calls += 1
        self.prompts.append(stdin)
        step = self.script.pop(0) if self.script else {"state": "NO_ACTION",
                                                       "summary": "nothing to do"}
        decision = dict(step)
        edit = decision.pop("_edit", None)
        message = decision.pop("_commit", "")
        if edit:
            target = Path(cwd) / edit["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(edit["body"], encoding="utf-8")
            self._git(["git", "add", "-A"], cwd, env)
            self._git(["git", "commit", "-m", message], cwd, env)
            self._git(["git", "push"], cwd, env)
            sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(cwd),
                                 capture_output=True, text=True).stdout.strip()
            decision["pushed_sha"] = sha
            decision.setdefault("commits", []).append(message)
            self.hub.push_commit(sha, message)     # GitHub notices the push
        body = json.dumps(decision)
        if "--output-last-message" in argv:
            Path(argv[argv.index("--output-last-message") + 1]).write_text(body)
            body = ""
        return CommandResult(tuple(argv), 0, body, "", 10)

    def _git(self, args, cwd, env) -> None:
        result = subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(f"agent git failed: {args}: {result.stderr}")


class EndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.origin, self.clone = pr_repo.build(self.root)
        self.hub = FakeGitHub()
        self.gh = Gh(self.clone, runner=self.hub)
        self.clock = FakeClock()
        self.state_root = self.root / "sessions"
        self.worktrees = self.root / "worktrees"
        self.lines: list[str] = []
        # The PR head the fake GitHub reports must be the real remote head.
        self.hub.pr["headRefOid"] = pr_repo.git(self.clone, "rev-parse",
                                                f"origin/{HEAD_BRANCH}")
        self.hub.pr["commits"][0]["oid"] = self.hub.pr["headRefOid"]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def out(self, text) -> None:
        self.lines.append(str(text))

    def text(self) -> str:
        return "\n".join(self.lines)

    def run_rig(self, argv: list[str], agent: FakeAgent | None = None):
        run = parse_args(argv)
        assert isinstance(run, PrRun)
        return run_session(run, self.clone, gh=self.gh, state_root=self.state_root,
                           worktree_root=self.worktrees, clock=self.clock, out=self.out,
                           runner=agent)

    def directory(self, number: int = 1420) -> Path:
        return session_dir("org", "aiila", number, root=self.state_root)

    def test_the_full_production_flow(self) -> None:
        thread = self.hub.add_thread("reviewer", "retryが二重実行される可能性があります。",
                                     "worker/retry.py", 2)
        self.hub.reply_in_thread(thread, "reviewer", JAPANESE_REQUEST)
        self.hub.set_check("worker-tests", "COMPLETED", "SUCCESS")

        agent = FakeAgent(self.hub, [
            {"state": "FIX_NEEDED",
             "summary": "Reviewer asks for retry handling to be fixed in this PR. "
                        "Verified against worker/retry.py and its callers.",
             "changed_files": ["worker/retry.py"],
             "validation": ["worker tests: passed", "lint: passed"],
             "findings": [],
             "_edit": {"path": "worker/retry.py",
                       "body": "def retry():\n    # idempotent\n    return 2\n"},
             "_commit": "fix(worker): prevent duplicate retry execution"},
            {"state": "FIX_NEEDED", "summary": "CI failure is mine; fixing the timeout path.",
             "changed_files": ["tests/test_retry.py"],
             "validation": ["worker tests: passed"],
             "_edit": {"path": "tests/test_retry.py", "body": "def test_retry():\n    pass\n"},
             "_commit": "test(worker): cover retry timeout path"},
        ])

        polls = {"count": 0}
        original_collect = self.gh

        def scripted_collect():
            from coderai.pr_automation.snapshot import collect
            polls["count"] += 1
            if polls["count"] == 3:
                self.hub.set_check("worker-tests", "COMPLETED", "FAILURE")
            if polls["count"] == 6:
                self.hub.set_check("worker-tests", "COMPLETED", "SUCCESS")
                self.hub.add_review("reviewer", "APPROVED", "確認しました。ありがとうございます。")
            if polls["count"] >= 8:
                self.hub.merge()
            return collect(original_collect, "org", "aiila", 1420)

        import coderai.pr_automation.supervisor as supervisor_module
        original = supervisor_module.collect
        supervisor_module.collect = lambda client, owner, repo, number: scripted_collect()
        try:
            report = self.run_rig(["1420", "--watch", "2h", "--", "codex"], agent)
        finally:
            supervisor_module.collect = original

        # --- terminal state ------------------------------------------------
        self.assertEqual(report.reason, states.MERGED)
        self.assertEqual(report.session.state, states.MERGED)

        # --- the AI actually engineered, and only on the PR branch ---------
        # triage + CI failure + the reviewer's approval. Crucially NOT our own two
        # pushes: coder-ai-os must never wake itself with the HEAD it just published.
        self.assertEqual(agent.calls, 3, f"unexpected wake count; prompts: {len(agent.prompts)}")
        wakes = [item["reason"] for item in audit.read(self.directory())
                 if item["event"] == "ai_wake"]
        self.assertEqual(wakes, ["FIRST_SNAPSHOT", "CI_FAILED", "NEW_DISCUSSION"])
        self.assertIn("今回対応した方が良さそうです", agent.prompts[0])
        self.assertIn("pr-engineer", agent.prompts[0])
        head = pr_repo.git(self.origin, "rev-parse", HEAD_BRANCH)
        self.assertEqual(head, report.session.last_push_sha)
        self.assertEqual(pr_repo.git(self.origin, "log", "--format=%s", "-1", HEAD_BRANCH),
                         "test(worker): cover retry timeout path")

        # --- nothing else on the remote moved ------------------------------
        main_before = pr_repo.git(self.origin, "rev-parse", "main")
        self.assertEqual(pr_repo.git(self.origin, "rev-parse", "main"), main_before)
        refs = pr_repo.git(self.origin, "for-each-ref", "--format=%(refname)")
        self.assertNotIn("refs/tags/", refs)
        self.assertNotIn("coder-ai/pr-1420", refs)

        # --- every push is a verified fast-forward -------------------------
        pushes = [item for item in audit.read(self.directory())
                  if item["event"] == "push_complete"]
        self.assertEqual(len(pushes), 2)
        failures = [item for item in audit.read(self.directory())
                    if item["event"] == "push_verification_failed"]
        self.assertEqual(failures, [])

        # --- the engineer's checkout is untouched --------------------------
        self.assertEqual(pr_repo.git(self.clone, "status", "--porcelain"), "")
        self.assertEqual(pr_repo.git(self.clone, "rev-parse", "--abbrev-ref", "HEAD"), "main")

        # --- commit messages carry no AI metadata (§31) --------------------
        log = pr_repo.git(self.origin, "log", "--format=%B", "-3", HEAD_BRANCH)
        for trailer in ("AI-Agent", "AI-Model", "Co-Authored-By"):
            self.assertNotIn(trailer, log)

        # --- elevation is gone ---------------------------------------------
        self.assertFalse((self.directory() / "guard").exists())
        self.assertFalse((self.directory() / "guard.json").exists())
        self.assertFalse((self.directory() / "claude-settings.json").exists())
        worktree = self.worktrees / "org" / "aiila" / "pr-1420"
        self.assertFalse(worktree.exists(), "the worktree is released on a terminal state")

        # --- the session is fully reconstructable ---------------------------
        events = [item["event"] for item in audit.read(self.directory())]
        for expected in ("session_started", "ai_wake", "ai_started", "ai_finished",
                         "push_complete", "watch_finished", "session_finished"):
            self.assertIn(expected, events)
        saved = load_session("org", "aiila", 1420, root=self.state_root)
        assert saved is not None
        self.assertEqual(saved.state, states.MERGED)
        self.assertEqual(saved.wake_count, 3)

    def test_an_idle_pull_request_wakes_nobody(self) -> None:
        agent = FakeAgent(self.hub, [{"state": "NO_ACTION", "summary": "nothing to do"}])
        report = self.run_rig(["1420", "--watch", "1h", "--", "claude"], agent)
        self.assertEqual(report.reason, states.WATCH_TIMEOUT)
        self.assertEqual(agent.calls, 1, "only the first-snapshot triage")
        self.assertIn("Watch window ended.", self.text())
        self.assertIn("coder-ai pr 1420 --watch 2h", self.text())

    def test_the_permission_notice_is_shown_once(self) -> None:
        agent = FakeAgent(self.hub, [{"state": "NO_ACTION", "summary": "ok"}])
        self.run_rig(["1420", "--watch", "0", "--", "claude"], agent)
        self.assertEqual(self.text().count("coder-ai · PR Automation"), 1)
        self.assertIn("✓ non-force push feature/worker-retry", self.text())
        self.assertIn("✗ force push", self.text())

    def test_a_session_resumes_instead_of_restarting(self) -> None:
        agent = FakeAgent(self.hub, [{"state": "NO_ACTION", "summary": "ok"}])
        self.run_rig(["1420", "--watch", "0", "--", "claude"], agent)
        first = load_session("org", "aiila", 1420, root=self.state_root)
        assert first is not None

        self.hub.add_comment("reviewer", "one more thing")
        agent2 = FakeAgent(self.hub, [{"state": "NO_ACTION", "summary": "ok"}])
        self.run_rig(["1420", "--watch", "0", "--", "claude"], agent2)
        second = load_session("org", "aiila", 1420, root=self.state_root)
        assert second is not None
        self.assertEqual(second.created_at, first.created_at, "the session was resumed")
        self.assertEqual(second.wake_count, first.wake_count + 1)

    def test_a_closed_pull_request_is_refused_before_any_work(self) -> None:
        from coderai.pr_automation.supervisor import SupervisorError
        self.hub.close()
        with self.assertRaises(SupervisorError) as caught:
            self.run_rig(["1420", "--watch", "0", "--", "claude"], None)
        self.assertIn("PR_ALREADY_CLOSED", str(caught.exception))
        self.assertFalse((self.worktrees / "org").exists())


class MultiPr(unittest.TestCase):
    """§74–75: several PRs, no resident agent per PR."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def seed(self, number: int, state: str) -> None:
        session = Session(host="github.com", owner="org", repo="aiila", number=number,
                          head_branch=f"feature/{number}", base_branch="main",
                          remote="origin", provider="claude", provider_argv=["claude"])
        session.state = state
        ensure_dir(session_dir("org", "aiila", number, root=self.root), self.root)
        save_session(session, root=self.root)

    def test_the_supervisor_view_lists_every_pr(self) -> None:
        for number, state in ((1420, "WAITING_FOR_REVIEW"), (1421, "WAITING_FOR_CI"),
                              (1430, "READY"), (1435, "HUMAN_NEEDED")):
            self.seed(number, state)
        rows = status_rows(self.root)
        self.assertEqual([item.number for item in rows], [1420, 1421, 1430, 1435])
        table = ui.status_table(rows)
        for state in ("WAITING_FOR_REVIEW", "WAITING_FOR_CI", "READY", "HUMAN_NEEDED"):
            self.assertIn(state, table)

    def test_sessions_are_isolated_from_each_other(self) -> None:
        self.seed(1420, "READY")
        self.seed(1421, "OPEN")
        first = load_session("org", "aiila", 1420, root=self.root)
        second = load_session("org", "aiila", 1421, root=self.root)
        assert first is not None and second is not None
        self.assertEqual(first.state, "READY")
        self.assertEqual(second.state, "OPEN")
        self.assertNotEqual(first.head_branch, second.head_branch)

    def test_a_corrupt_session_does_not_break_the_view(self) -> None:
        self.seed(1420, "READY")
        broken = ensure_dir(session_dir("org", "aiila", 1499, root=self.root), self.root)
        (broken / "session.json").write_text("{ not json")
        self.assertEqual([item.number for item in status_rows(self.root)], [1420])

    def test_no_agent_process_is_resident_for_an_idle_pr(self) -> None:
        self.seed(1420, "READY")
        from coderai.pr_automation import daemon
        directory = session_dir("org", "aiila", 1420, root=self.root)
        self.assertIsNone(daemon.read_pid(directory))


if __name__ == "__main__":
    unittest.main(verbosity=2)
