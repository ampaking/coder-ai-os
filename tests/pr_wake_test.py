"""Task 12 — one wake, one validated decision, no resident model process."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import pr_repo  # noqa: E402
from pr_github import FakeGitHub  # noqa: E402

from coderai.pr_automation import audit  # noqa: E402
from coderai.pr_automation.context import build_bundle, discover_guidance  # noqa: E402
from coderai.pr_automation.decision import (  # noqa: E402
    DONE, FIX_NEEDED, HUMAN_NEEDED, NO_ACTION, REPLY_NEEDED, STATES, VERIFY_NEEDED,
    Decision, DecisionError, parse_provider_output, validate,
)
from coderai.pr_automation.delta import compare  # noqa: E402
from coderai.pr_automation.findings import FIXED, Ledger, apply_snapshot  # noqa: E402
from coderai.pr_automation.github import Gh  # noqa: E402
from coderai.pr_automation.guard import install as guard_install  # noqa: E402
from coderai.pr_automation.snapshot import collect  # noqa: E402
from coderai.pr_automation.state import Session, ensure_dir, session_dir  # noqa: E402
from coderai.pr_automation.wake import (  # noqa: E402
    STALE_RUN, CommandResult, decision_argv, wake,
)


class DecisionContract(unittest.TestCase):
    def test_every_state_round_trips(self) -> None:
        for state in STATES:
            with self.subTest(state=state):
                decision = validate({"state": state, "summary": "ok"})
                self.assertEqual(decision.state, state)

    def test_missing_or_unknown_state_is_an_error(self) -> None:
        for value in ({"summary": "x"}, {"state": "MAYBE", "summary": "x"},
                      {"state": "", "summary": "x"}, {"state": None, "summary": "x"}):
            with self.subTest(value=value), self.assertRaises(DecisionError):
                validate(value)

    def test_missing_summary_is_an_error(self) -> None:
        with self.assertRaises(DecisionError):
            validate({"state": NO_ACTION})
        with self.assertRaises(DecisionError):
            validate({"state": NO_ACTION, "summary": "   "})

    def test_malformed_output_is_never_silently_no_action(self) -> None:
        for raw in ("", "   ", "not json at all", "[]", "null", '{"foo":1}'):
            with self.subTest(raw=raw), self.assertRaises(DecisionError):
                parse_provider_output("codex", raw)

    def test_claude_envelope_is_unwrapped(self) -> None:
        payload = {"state": FIX_NEEDED, "summary": "fixed the retry path"}
        envelope = json.dumps({"type": "result", "structured_output": payload})
        self.assertEqual(parse_provider_output("claude", envelope).state, FIX_NEEDED)

    def test_claude_string_result_is_unwrapped(self) -> None:
        payload = json.dumps({"state": DONE, "summary": "done"})
        envelope = json.dumps({"result": payload})
        self.assertEqual(parse_provider_output("claude", envelope).state, DONE)

    def test_a_decision_wrapped_in_prose_is_recovered(self) -> None:
        raw = ('Here is my decision.\n\n'
               '{"state": "REPLY_NEEDED", "summary": "answered the Redis question"}\n'
               'Returning control to coder-ai-os.')
        self.assertEqual(parse_provider_output("codex", raw).state, REPLY_NEEDED)

    def test_fields_are_bounded(self) -> None:
        decision = validate({"state": DONE, "summary": "x" * 99_999,
                             "changed_files": ["f" * 5000] * 500,
                             "findings": [{"id": "F-1", "state": "FIXED"}] * 500})
        self.assertLessEqual(len(decision.summary), 4000)
        self.assertLessEqual(len(decision.changed_files), 100)
        self.assertLessEqual(len(decision.findings), 100)

    def test_derived_properties(self) -> None:
        self.assertTrue(validate({"state": HUMAN_NEEDED, "summary": "x"}).needs_human)
        self.assertTrue(validate({"state": NO_ACTION, "summary": "x"}).sleeps)
        self.assertTrue(validate({"state": DONE, "summary": "x",
                                  "commits": ["abc"]}).mutated)
        self.assertFalse(validate({"state": VERIFY_NEEDED, "summary": "x"}).mutated)

    def test_invalid_finding_rows_are_dropped_not_fatal(self) -> None:
        decision = validate({"state": DONE, "summary": "x",
                             "findings": [{"id": "F-1", "state": "FIXED"},
                                          {"id": "", "state": "FIXED"},
                                          {"state": "FIXED"}, "junk"]})
        self.assertEqual(decision.findings, [{"id": "F-1", "state": "FIXED", "note": ""}])


class ContextBundle(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = FakeGitHub()
        self.gh = Gh(Path("."), runner=self.hub)
        self._tmp = tempfile.TemporaryDirectory()
        self.worktree = Path(self._tmp.name)
        self.session = Session(host="github.com", owner="org", repo="aiila", number=1420,
                               head_branch="feature/worker-retry", base_branch="main",
                               remote="origin", provider="claude", provider_argv=["claude"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def bundle(self, ledger: Ledger | None = None) -> str:
        snapshot = collect(self.gh, "org", "aiila", 1420)
        delta = compare(None, snapshot)
        return build_bundle(self.session, snapshot, delta, ledger or Ledger(),
                            self.worktree)

    def test_carries_raw_conversation_not_conclusions(self) -> None:
        self.hub.add_comment("reviewer", "なるほどです。ただproductionでも起こり得るので、"
                                          "今回対応した方が良さそうです。")
        text = self.bundle()
        self.assertIn("今回対応した方が良さそうです", text)
        self.assertIn("reviewer", text)

    def test_marks_bots(self) -> None:
        self.hub.add_thread("codex", "P1: retry can process the job twice.", bot=True)
        self.assertIn("(bot)", self.bundle())

    def test_thread_status_and_anchor(self) -> None:
        thread = self.hub.add_thread("reviewer", "fix this", "worker/retry.py", 42)
        self.hub.resolve_thread(thread, by="reviewer")
        text = self.bundle()
        self.assertIn("[RESOLVED by reviewer]", text)
        self.assertIn("worker/retry.py:42", text)

    def test_settled_findings_are_shown_so_they_are_not_reanalysed(self) -> None:
        self.hub.add_thread("reviewer", "fix retry")
        snapshot = collect(self.gh, "org", "aiila", 1420)
        ledger = apply_snapshot(Ledger(), snapshot)
        identifier = next(iter(ledger.findings))
        ledger.transition(identifier, FIXED, by="ai", note="fixed in 91ad773")
        text = self.bundle(ledger)
        self.assertIn(identifier, text)
        self.assertIn(FIXED, text)
        self.assertIn("do not re-analyse settled", text.lower())

    def test_guidance_discovery_lists_without_reading(self) -> None:
        (self.worktree / "AGENTS.md").write_text("rules")
        (self.worktree / "Makefile").write_text("test:\n\tpytest\n")
        (self.worktree / "worker").mkdir()
        (self.worktree / "worker" / "AGENTS.md").write_text("component rules")
        found = discover_guidance(self.worktree, ["worker/retry.py"])
        self.assertIn("AGENTS.md", found)
        self.assertIn("Makefile", found)
        self.assertIn("worker/AGENTS.md", found)

    def test_ci_failures_are_named(self) -> None:
        self.hub.set_check("worker-tests", "COMPLETED", "FAILURE")
        text = self.bundle()
        self.assertIn("worker-tests", text)
        self.assertIn("[FAIL]", text)

    def test_truncation_is_marked(self) -> None:
        for index in range(260):
            self.hub.add_comment("reviewer", f"comment {index}")
        text = self.bundle()
        self.assertIn("Truncation", text)
        self.assertIn("older comments omitted", text)

    def test_bundle_stays_within_the_cap(self) -> None:
        for index in range(300):
            self.hub.add_comment("reviewer", "x" * 3000)
        self.assertLessEqual(len(self.bundle()), 61_000)

    def test_partial_collection_is_flagged_as_unknown(self) -> None:
        self.hub.failures["api graphql"] = (1, "server error")
        self.assertIn("Treat missing sections as unknown", self.bundle())


class WakeRun(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.origin, self.clone = pr_repo.build(self.root)
        self.hub = FakeGitHub()
        self.gh = Gh(Path("."), runner=self.hub)
        self.session = Session(host="github.com", owner="org", repo="aiila", number=1420,
                               head_branch="feature/worker-retry", base_branch="main",
                               remote="origin", provider="codex", provider_argv=["codex"])
        from coderai.pr_automation.worktree import ensure_worktree
        self.tree = ensure_worktree(self.session, self.clone, root=self.root / "wt")
        self.directory = ensure_dir(session_dir("org", "aiila", 1420,
                                                root=self.root / "sessions"),
                                    self.root / "sessions")
        self.guard = guard_install.install(self.directory, self.tree.path, remote="origin",
                                           head_branch="feature/worker-retry",
                                           local_branch=self.tree.branch)
        self.ledger = Ledger()
        self.calls: list[list[str]] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def runner_for(self, payload, returncode: int = 0):
        def runner(argv, cwd, env, timeout, stdin):
            self.calls.append(list(argv))
            self.last_env = env
            self.last_cwd = cwd
            self.last_prompt = stdin
            if isinstance(payload, str):
                body = payload
            else:
                body = json.dumps(payload)
            if "--output-last-message" in argv:
                Path(argv[argv.index("--output-last-message") + 1]).write_text(body)
                body = ""
            return CommandResult(tuple(argv), returncode, body, "", 42)
        return runner

    def do_wake(self, payload, **kwargs):
        snapshot = collect(self.gh, "org", "aiila", 1420)
        delta = compare(None, snapshot)
        return wake(self.session, snapshot, delta, guard=self.guard,
                    worktree=self.tree.path, directory=self.directory,
                    ledger=self.ledger, runner=self.runner_for(payload), **kwargs)

    def test_a_valid_decision_is_recorded(self) -> None:
        outcome = self.do_wake({"state": FIX_NEEDED, "summary": "fixing retry",
                                "changed_files": ["worker/retry.py"],
                                "commits": ["91ad773"], "pushed_sha": "91ad773"})
        self.assertTrue(outcome.ok)
        assert outcome.decision is not None
        self.assertEqual(outcome.decision.state, FIX_NEEDED)
        self.assertEqual(self.session.last_push_sha, "91ad773")
        self.assertTrue((outcome.run_dir / "decision.json").is_file())
        self.assertTrue((outcome.run_dir / "prompt.txt").is_file())

    def test_audit_records_an_started_finished_pair(self) -> None:
        self.do_wake({"state": NO_ACTION, "summary": "nothing to do"})
        events = [item["event"] for item in audit.read(self.directory)]
        self.assertIn("ai_started", events)
        self.assertIn("ai_finished", events)

    def test_the_provider_runs_inside_the_worktree_with_the_guard(self) -> None:
        self.do_wake({"state": NO_ACTION, "summary": "ok"})
        self.assertEqual(self.last_cwd, self.tree.path)
        self.assertEqual(self.last_env["PATH"].split(":")[0], str(self.guard.bin_dir))
        self.assertEqual(self.last_env["CODER_AI_PR_MODE"], "1")

    def test_the_users_provider_arguments_survive(self) -> None:
        self.session.provider_argv = ["codex", "--model", "gpt-5.6-sol"]
        self.do_wake({"state": NO_ACTION, "summary": "ok"})
        self.assertIn("--model", self.calls[0])
        self.assertIn("gpt-5.6-sol", self.calls[0])
        self.assertIn("--output-schema", self.calls[0])

    def test_decision_findings_update_the_ledger(self) -> None:
        self.hub.add_thread("codex", "P1: retry twice", bot=True)
        snapshot = collect(self.gh, "org", "aiila", 1420)
        apply_snapshot(self.ledger, snapshot)
        identifier = next(iter(self.ledger.findings))
        self.do_wake({"state": DONE, "summary": "verified and fixed",
                      "findings": [{"id": identifier, "state": "FIXED",
                                    "note": "guarded by the queue lock"}]})
        self.assertEqual(self.ledger.get(identifier).state, FIXED)

    def test_malformed_output_fails_typed_and_recovers_the_ledger(self) -> None:
        self.hub.add_thread("reviewer", "fix retry")
        snapshot = collect(self.gh, "org", "aiila", 1420)
        apply_snapshot(self.ledger, snapshot)
        identifier = next(iter(self.ledger.findings))
        self.ledger.transition(identifier, "FIXING", by="ai")
        outcome = self.do_wake("total garbage, not json")
        self.assertFalse(outcome.ok)
        self.assertIsNone(outcome.decision)
        self.assertIn("not JSON", outcome.error)
        self.assertEqual(self.ledger.get(identifier).state, "OPEN")
        events = [item["event"] for item in audit.read(self.directory)]
        self.assertIn("ai_failed", events)

    def test_a_human_push_mid_run_invalidates_the_run(self) -> None:
        def refresh():
            self.hub.push_commit("xyz9990000000000000000000000000000000000")
            return collect(self.gh, "org", "aiila", 1420)

        outcome = self.do_wake({"state": DONE, "summary": "pushed"}, refresh=refresh)
        self.assertTrue(outcome.stale)
        self.assertIsNone(outcome.decision)
        self.assertEqual(outcome.error, STALE_RUN)
        events = [item["event"] for item in audit.read(self.directory)]
        self.assertIn("ai_stale", events)

    def test_an_unchanged_head_is_not_stale(self) -> None:
        outcome = self.do_wake({"state": DONE, "summary": "ok"},
                               refresh=lambda: collect(self.gh, "org", "aiila", 1420))
        self.assertFalse(outcome.stale)

    def test_no_elevated_settings_survive_the_run(self) -> None:
        self.session.provider = "claude"
        self.session.provider_argv = ["claude"]
        self.do_wake({"state": NO_ACTION, "summary": "ok"})
        leftovers = list(self.directory.glob("claude-settings.json"))
        self.assertEqual(leftovers, [])

    def test_nothing_stays_in_flight_after_a_decision(self) -> None:
        self.hub.add_thread("reviewer", "fix retry")
        snapshot = collect(self.gh, "org", "aiila", 1420)
        apply_snapshot(self.ledger, snapshot)
        identifier = next(iter(self.ledger.findings))
        self.do_wake({"state": DONE, "summary": "done",
                      "findings": [{"id": identifier, "state": "VERIFYING"}]})
        self.assertEqual(self.ledger.get(identifier).state, "OPEN")
        self.assertEqual(self.ledger.in_flight(), [])


class ProviderArgv(unittest.TestCase):
    def test_codex_gets_a_schema_file_and_result_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            argv, result = decision_argv("codex", ["codex", "--model", "x"], Path(tmp))
            self.assertEqual(argv[:4], ["codex", "exec", "--color", "never"])
            self.assertIn("--output-schema", argv)
            self.assertEqual(argv[-1], "-")
            assert result is not None
            self.assertTrue((Path(tmp) / "schema.json").is_file())

    def test_claude_gets_print_and_json_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            argv, result = decision_argv("claude", ["claude"], Path(tmp))
            self.assertIn("--print", argv)
            self.assertIn("--json-schema", argv)
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
