"""Task 15 — CI passing costs nothing; CI failing wakes with evidence."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from pr_github import FakeGitHub  # noqa: E402

from coderai.pr_automation.ci import MAX_LOG, REDACTED, gather, redact  # noqa: E402
from coderai.pr_automation.delta import CI_FAILED, NEW_DISCUSSION, NEW_HEAD, compare  # noqa: E402
from coderai.pr_automation.github import Gh, GhResult  # noqa: E402
from coderai.pr_automation.reactions import (  # noqa: E402
    CI_RETRY_BUDGET, SLEEP_CI_BUDGET, SLEEP_CI_PASSED, SLEEP_CI_PENDING, SLEEP_NO_REASON,
    WakeRequest, react,
)
from coderai.pr_automation.snapshot import Check, collect  # noqa: E402
from coderai.pr_automation.state import Session  # noqa: E402


class LogHub(FakeGitHub):
    """A fake gh that also serves workflow logs."""

    def __init__(self, log: str = "FAILED test_retry\nAssertionError: 2 != 1\n") -> None:
        super().__init__()
        self.log = log
        self.log_calls = 0

    def __call__(self, argv, cwd, timeout):
        argv = list(argv)
        if argv[1:3] == ["run", "view"] and "run view" not in self.failures:
            self.log_calls += 1
            self.calls.append(tuple(argv))
            return GhResult(tuple(argv), 0, self.log, "", 5)
        return super().__call__(argv, cwd, timeout)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = LogHub()
        self.gh = Gh(Path("."), runner=self.hub)
        self.session = Session(host="github.com", owner="org", repo="aiila", number=1420,
                               head_branch="feature/worker-retry", base_branch="main",
                               remote="origin", provider="claude", provider_argv=["claude"])

    def snap(self):
        return collect(self.gh, "org", "aiila", 1420)

    def react(self, before, **kwargs):
        current = self.snap()
        return react(compare(before, current), current, self.session, gh=self.gh, **kwargs)


class Redaction(unittest.TestCase):
    def test_credential_shapes_are_removed(self) -> None:
        samples = [
            "token=ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "GITHUB_TOKEN: github_pat_11ABCDEFG0abcdefghij_ABCDEFGHIJKLMNOP",
            "aws key AKIAIOSFODNN7EXAMPLE here",
            "Authorization: Bearer abcdefghijklmnopqrstuvwx",
            "API_KEY = sk-abcdefghijklmnopqrstuvwxyz12",
            "MY_SECRET=hunter2hunter2",
            "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
        ]
        for sample in samples:
            with self.subTest(sample=sample[:20]):
                cleaned = redact(sample)
                self.assertIn(REDACTED, cleaned)
                for secret in ("ghp_abcdefghij", "github_pat_11ABCDEFG0", "AKIAIOSFODNN7",
                               "hunter2", "BEGIN RSA PRIVATE KEY"):
                    self.assertNotIn(secret, cleaned)

    def test_ordinary_log_text_survives(self) -> None:
        text = "FAILED tests/test_retry.py::test_duplicate - AssertionError: 2 != 1"
        self.assertEqual(redact(text), text)


class Evidence(Base):
    def test_failing_check_evidence_is_gathered_and_named(self) -> None:
        checks = [Check(name="worker-tests", status="COMPLETED", conclusion="FAILURE",
                        workflow="ci", required=True,
                        url="https://github.com/org/aiila/actions/runs/42")]
        report = gather(self.gh, checks)
        self.assertEqual(len(report.failed), 1)
        text = report.render()
        self.assertIn("worker-tests", text)
        self.assertIn("(required)", text)
        self.assertIn("AssertionError", text)

    def test_logs_are_redacted_and_capped(self) -> None:
        self.hub.log = "ghp_abcdefghijklmnopqrstuvwxyz0123456789\n" + ("x" * 50_000)
        checks = [Check(name="deploy", status="COMPLETED", conclusion="FAILURE",
                        url="https://github.com/org/aiila/actions/runs/42", required=True)]
        report = gather(self.gh, checks)
        log = report.failed[0].log
        self.assertNotIn("ghp_abcdefghij", log)
        self.assertLessEqual(len(log), MAX_LOG)

    def test_required_checks_come_first_and_the_rest_is_marked(self) -> None:
        checks = [Check(name=f"optional-{index}", status="COMPLETED", conclusion="FAILURE")
                  for index in range(8)]
        checks.append(Check(name="required-one", status="COMPLETED", conclusion="FAILURE",
                            required=True))
        report = gather(self.gh, checks, fetch_logs=False)
        self.assertEqual(report.failed[0].name, "required-one")
        self.assertTrue(report.truncated)
        self.assertIn("more failures exist", report.render())

    def test_a_missing_log_is_reported_not_fabricated(self) -> None:
        self.hub.failures["run view"] = (1, "log expired")
        checks = [Check(name="worker-tests", status="COMPLETED", conclusion="FAILURE",
                        url="https://github.com/org/aiila/actions/runs/42", required=True)]
        report = gather(self.gh, checks)
        self.assertIn("log unavailable", report.render())


class Routing(Base):
    def test_ci_passing_costs_no_model_call(self) -> None:
        self.hub.set_check("worker-tests", "IN_PROGRESS", required=True)
        before = self.snap()
        self.hub.set_check("worker-tests", "COMPLETED", "SUCCESS")
        self.assertEqual(self.react(before), SLEEP_CI_PASSED)
        self.assertEqual(self.hub.log_calls, 0)

    def test_ci_pending_stays_asleep(self) -> None:
        before = self.snap()
        self.hub.set_check("worker-tests", "IN_PROGRESS", required=True)
        self.assertEqual(self.react(before), SLEEP_CI_PENDING)

    def test_ci_failure_wakes_once_with_the_job_named(self) -> None:
        self.hub.set_check("worker-tests", "IN_PROGRESS", required=True)
        before = self.snap()
        self.hub.set_check("worker-tests", "COMPLETED", "FAILURE")
        request = self.react(before)
        self.assertIsInstance(request, WakeRequest)
        assert isinstance(request, WakeRequest)
        self.assertEqual(request.reason, CI_FAILED)
        self.assertEqual(request.checks, ["worker-tests"])
        self.assertIn("worker-tests", request.summary)
        self.assertIn("AssertionError", request.evidence)

    def test_an_optional_check_failure_does_not_force_a_wake(self) -> None:
        self.hub.set_check("flaky-e2e", "IN_PROGRESS", required=False)
        before = self.snap()
        self.hub.set_check("flaky-e2e", "COMPLETED", "FAILURE", required=False)
        self.assertEqual(self.react(before), SLEEP_NO_REASON)

    def test_repeated_identical_failure_stops_after_the_budget(self) -> None:
        self.hub.set_check("worker-tests", "IN_PROGRESS", required=True)
        before = self.snap()
        self.hub.set_check("worker-tests", "COMPLETED", "FAILURE")
        outcomes = [self.react(before) for _ in range(CI_RETRY_BUDGET + 2)]
        woke = [item for item in outcomes if isinstance(item, WakeRequest)]
        self.assertEqual(len(woke), CI_RETRY_BUDGET)
        self.assertEqual(outcomes[-1], SLEEP_CI_BUDGET)
        self.assertIn(f"of {CI_RETRY_BUDGET}", woke[-1].summary)

    def test_a_new_head_resets_the_budget(self) -> None:
        self.hub.set_check("worker-tests", "IN_PROGRESS", required=True)
        before = self.snap()
        self.hub.set_check("worker-tests", "COMPLETED", "FAILURE")
        for _ in range(CI_RETRY_BUDGET):
            self.react(before)
        self.assertEqual(self.react(before), SLEEP_CI_BUDGET)
        self.hub.push_commit("bbb2220000000000000000000000000000000000")
        self.hub.set_check("worker-tests", "COMPLETED", "FAILURE")
        self.assertIsInstance(self.react(before), WakeRequest)

    def test_reviewer_feedback_during_a_ci_run_does_not_wait_for_ci(self) -> None:
        """§64: 10:00 push, 10:01 CI starts, 10:03 reviewer comments."""
        self.hub.push_commit("ccc3330000000000000000000000000000000000")
        self.hub.set_check("worker-tests", "IN_PROGRESS", required=True)
        before = self.snap()
        self.hub.add_comment("reviewer", "One more thing about the retry path.")
        request = self.react(before)
        self.assertIsInstance(request, WakeRequest)
        assert isinstance(request, WakeRequest)
        self.assertEqual(request.reason, NEW_DISCUSSION)
        self.assertEqual(self.hub.log_calls, 0, "no CI logs needed for a review wake")

    def test_a_new_head_wakes_without_ci_evidence(self) -> None:
        before = self.snap()
        self.hub.push_commit("ddd4440000000000000000000000000000000000")
        request = self.react(before)
        assert isinstance(request, WakeRequest)
        self.assertEqual(request.reason, NEW_HEAD)
        self.assertEqual(request.evidence, "")

    def test_every_outcome_is_named(self) -> None:
        before = self.snap()
        outcome = self.react(before)
        self.assertTrue(isinstance(outcome, WakeRequest) or isinstance(outcome, str))
        if isinstance(outcome, str):
            self.assertTrue(outcome)


if __name__ == "__main__":
    unittest.main(verbosity=2)
