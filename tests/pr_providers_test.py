"""Task 16 — a provider failure costs a retry, not the session."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coderai.pr_automation.providers import (
    AUTH_FAILED, AVAILABLE, BACKOFF, FAILED, FIX, HealthStore, MAX_BACKOFF, NOT_INSTALLED,
    REVIEW, TEMPORARILY_LIMITED, TRIAGE, VERIFY, detect, load, record, save, select,
)
from coderai.pr_automation.state import Session


def a_session(provider: str = "codex", argv: list[str] | None = None) -> Session:
    return Session(host="github.com", owner="org", repo="aiila", number=1420,
                   head_branch="feature/worker-retry", base_branch="main", remote="origin",
                   provider=provider, provider_argv=argv or [provider])


def both_installed(name: str) -> bool:
    return name in {"claude", "codex"}


class Health(unittest.TestCase):
    def test_detect_marks_missing_providers(self) -> None:
        store = HealthStore()
        with mock.patch("coderai.pr_automation.providers.installed",
                        side_effect=lambda name: name == "claude"):
            detect(store, now=100.0)
        self.assertEqual(store.get("claude").state, AVAILABLE)
        self.assertEqual(store.get("codex").state, NOT_INSTALLED)

    def test_rate_limit_backs_off_then_recovers(self) -> None:
        store = HealthStore()
        health = record(store, "codex", TEMPORARILY_LIMITED, now=1000.0)
        self.assertEqual(health.state, TEMPORARILY_LIMITED)
        self.assertEqual(health.retry_after, 1000.0 + BACKOFF[TEMPORARILY_LIMITED])
        self.assertFalse(health.usable(1000.0))
        self.assertTrue(health.usable(1000.0 + BACKOFF[TEMPORARILY_LIMITED]))

    def test_auth_failure_is_not_retried_within_the_window(self) -> None:
        store = HealthStore()
        health = record(store, "claude", AUTH_FAILED, now=1000.0, note="gh auth login")
        self.assertFalse(health.usable(1000.0 + 60))
        self.assertTrue(health.usable(1000.0 + BACKOFF[AUTH_FAILED]))

    def test_repeated_failures_back_off_exponentially_but_bounded(self) -> None:
        store = HealthStore()
        delays = []
        for index in range(8):
            health = record(store, "codex", FAILED, now=0.0)
            delays.append(health.retry_after)
        self.assertLess(delays[0], delays[1])
        self.assertLessEqual(max(delays), MAX_BACKOFF)

    def test_success_clears_the_history(self) -> None:
        store = HealthStore()
        record(store, "codex", FAILED, now=0.0)
        record(store, "codex", FAILED, now=0.0)
        health = record(store, "codex", AVAILABLE, now=100.0)
        self.assertEqual(health.state, AVAILABLE)
        self.assertEqual(health.failures, 0)
        self.assertTrue(health.usable(100.0))

    def test_persistence_round_trip(self) -> None:
        store = HealthStore()
        record(store, "codex", TEMPORARILY_LIMITED, now=1000.0, note="rate limited")
        with tempfile.TemporaryDirectory() as tmp:
            save(Path(tmp), store)
            restored = load(Path(tmp))
        self.assertEqual(restored.get("codex").state, TEMPORARILY_LIMITED)
        self.assertEqual(restored.get("codex").note, "rate limited")

    def test_missing_file_is_an_empty_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load(Path(tmp)).providers, {})


class Selection(unittest.TestCase):
    def setUp(self) -> None:
        self.patch = mock.patch("coderai.pr_automation.providers.installed",
                                side_effect=both_installed)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.store = HealthStore()

    def test_the_parent_owns_triage(self) -> None:
        session = a_session("codex", ["codex", "--model", "gpt-5.6-sol"])
        assignment = select(TRIAGE, session, self.store)
        assert assignment is not None
        self.assertEqual(assignment.provider, "codex")
        self.assertEqual(assignment.argv, ["codex", "--model", "gpt-5.6-sol"])
        self.assertFalse(assignment.fallback)

    def test_the_parent_model_does_not_propagate_to_worker_roles(self) -> None:
        session = a_session("claude", ["claude", "--model", "claude-fable-5"])
        for role in (VERIFY, FIX):
            with self.subTest(role=role):
                assignment = select(role, session, self.store)
                assert assignment is not None
                self.assertEqual(assignment.provider, "claude")
                self.assertNotIn("--model", assignment.argv)

    def test_review_prefers_the_opposite_provider(self) -> None:
        session = a_session("codex")
        assignment = select(REVIEW, session, self.store)
        assert assignment is not None
        self.assertEqual(assignment.provider, "claude")
        self.assertEqual(assignment.reason, "cross-provider review")

    def test_review_falls_back_to_the_parent_when_alone(self) -> None:
        session = a_session("codex")
        record(self.store, "claude", NOT_INSTALLED)
        self.store.get("claude").state = NOT_INSTALLED
        assignment = select(REVIEW, session, self.store)
        assert assignment is not None
        self.assertEqual(assignment.provider, "codex")

    def test_a_rate_limited_parent_falls_back_with_a_reason(self) -> None:
        session = a_session("codex")
        record(self.store, "codex", TEMPORARILY_LIMITED, now=1000.0)
        assignment = select(FIX, session, self.store, now=1000.0)
        assert assignment is not None
        self.assertEqual(assignment.provider, "claude")
        self.assertTrue(assignment.fallback)
        self.assertIn("temporarily_limited", assignment.reason)

    def test_the_parent_returns_once_its_window_expires(self) -> None:
        session = a_session("codex")
        record(self.store, "codex", TEMPORARILY_LIMITED, now=1000.0)
        later = 1000.0 + BACKOFF[TEMPORARILY_LIMITED] + 1
        assignment = select(FIX, session, self.store, now=later)
        assert assignment is not None
        self.assertEqual(assignment.provider, "codex")

    def test_a_provider_that_is_not_installed_is_never_selected(self) -> None:
        session = a_session("codex")
        with mock.patch("coderai.pr_automation.providers.installed",
                        side_effect=lambda name: name == "claude"):
            detect(self.store)
            assignment = select(FIX, session, self.store)
        assert assignment is not None
        self.assertEqual(assignment.provider, "claude")

    def test_no_usable_provider_returns_none(self) -> None:
        session = a_session("codex")
        record(self.store, "codex", AUTH_FAILED, now=1000.0)
        record(self.store, "claude", AUTH_FAILED, now=1000.0)
        self.assertIsNone(select(FIX, session, self.store, now=1000.0))

    def test_context_survives_a_provider_swap(self) -> None:
        """The session state — not the provider — holds the PR understanding."""
        session = a_session("codex")
        with tempfile.TemporaryDirectory() as tmp:
            from coderai.pr_automation.findings import Finding, Ledger, save as save_ledger
            ledger = Ledger()
            ledger.upsert(Finding(id="F-1", state="VERIFIED", kind="ai_review",
                                  excerpt="retry can process the job twice"))
            save_ledger(Path(tmp), ledger)
            record(self.store, "codex", TEMPORARILY_LIMITED, now=1000.0)
            save(Path(tmp), self.store)

            store = load(Path(tmp))
            assignment = select(FIX, session, store, now=1000.0)
            assert assignment is not None
            self.assertEqual(assignment.provider, "claude")
            from coderai.pr_automation.findings import load as load_ledger
            self.assertEqual(load_ledger(Path(tmp)).get("F-1").state, "VERIFIED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
