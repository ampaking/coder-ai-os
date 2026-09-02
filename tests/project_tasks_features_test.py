#!/usr/bin/env python3
"""Portable capture, review, Git opt-in, and package-isolation tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from coderai.project_tasks.analytics import safe_export
from coderai.project_tasks.project import load_settings, write_settings
from coderai.project_tasks.storage import record_collector_receipt

from coderai.project_tasks.project import root_hash
from coderai.project_tasks.storage import connect

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "src" / "coderai" / "project_tasks" / "cli.py"


class ProjectTasksFeaturesTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = (Path(temporary.name) / "sample").resolve()
        (self.project / ".coder-ai").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        digest = hashlib.sha256(str(self.project).encode()).hexdigest()
        (self.project / ".coder-ai" / "identity.json").write_text(json.dumps({
            "project_id": "sample", "git_root_hash": f"sha256:{digest}", "remote_hash": "sha256:test"
        }))
        self.run_cli("enable")

    def run_cli(self, *arguments: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(CLI), "--project", str(self.project), *arguments],
            capture_output=True, text=True, check=False,
        )
        if ok and result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

    def event(self, action: str, **extra: object) -> dict[str, str]:
        data = {"title": "Add project task capture", "summary": f"Lifecycle {action}",
                "agent": "codex", "nativeSessionId": "thread-local", "resumeSupported": True, **extra}
        return json.loads(self.run_cli("event", action, "--json", json.dumps(data)).stdout)

    def test_lifecycle_adapter_reuses_stable_ids_and_requires_proof(self) -> None:
        started = self.event("start")
        completed = self.event("complete", taskId=started["taskId"], sessionId=started["sessionId"])
        self.assertEqual(completed["status"], "needs_validation")
        self.event("validate", taskId=started["taskId"], sessionId=started["sessionId"], proof="unit tests")
        completed = self.event("complete", taskId=started["taskId"], sessionId=started["sessionId"])
        self.assertEqual(completed["status"], "completed")
        ended = self.event("end", taskId=started["taskId"], sessionId=started["sessionId"])
        self.assertTrue(ended["accepted"])
        review = json.loads(self.run_cli("review").stdout)
        self.assertIn("observedSessionMinutes", review["effort"])
        self.assertIn("reportedActualMinutes", review["effort"])

    def test_review_ideas_and_export_are_evidence_linked_and_safe(self) -> None:
        started = self.event("start")
        self.event(
            "validate", taskId=started["taskId"], sessionId=started["sessionId"],
            proof="private repository command",
        )
        review = json.loads(self.run_cli("review").stdout)
        ideas = json.loads(self.run_cli("ideas").stdout)
        exported = json.loads(self.run_cli("export").stdout)
        self.assertEqual(review["unfinished"], 1)
        self.assertTrue(ideas[0]["evidence"])
        self.assertIn("native agent session IDs", exported["excluded"])
        self.assertIn("validations", exported)
        self.assertIn("observations", exported)
        self.assertEqual(len(exported["observations"]), 1)
        self.assertNotIn("command", json.dumps(exported["observations"]).lower())
        self.assertIn("raw repository command output", exported["excluded"])
        self.assertIn("ideas", exported)
        self.assertIn("reviews", exported)
        self.assertIn("gitLinks", exported)
        self.assertNotIn("sessions", exported)
        second_review = json.loads(self.run_cli("review").stdout)
        self.assertEqual(second_review["id"], review["id"])
        updated = json.loads(self.run_cli("idea", ideas[0]["id"], "--status", "accepted").stdout)
        self.assertEqual(updated["status"], "accepted")
        refreshed = json.loads(self.run_cli("ideas").stdout)
        self.assertEqual(next(item for item in refreshed if item["id"] == updated["id"])["status"], "accepted")

    def test_automatic_collection_defaults_on_and_supports_explicit_opt_out(self) -> None:
        settings = load_settings(self.project) or {}
        settings["schemaVersion"] = 4
        write_settings(self.project, settings)
        initial = json.loads(self.run_cli("collect", "status").stdout)
        self.assertTrue(initial["enabled"])
        self.assertEqual(initial["mode"], "event-hooks")
        disabled = json.loads(self.run_cli("collect", "disable").stdout)
        self.assertFalse(disabled["enabled"])
        self.assertEqual(disabled["mode"], "off")
        enabled = json.loads(self.run_cli("collect", "enable").stdout)
        self.assertTrue(enabled["enabled"])
        self.assertEqual(enabled["mode"], "event-hooks")
        self.assertIn("prompts", enabled["policy"]["excluded"])
        self.assertFalse(enabled["gitMetadata"])
        self.assertEqual((load_settings(self.project) or {})["schemaVersion"], 4)
        self.assertTrue(json.loads(self.run_cli("collect", "status").stdout)["enabled"])
        disabled_again = json.loads(self.run_cli("collect", "disable").stdout)
        self.assertFalse(disabled_again["enabled"])
        self.assertIsInstance(json.loads(self.run_cli("list", "--json").stdout), list)

    def test_settings_only_config_does_not_advance_database_marker(self) -> None:
        settings = load_settings(self.project) or {}
        settings["schemaVersion"] = 4
        write_settings(self.project, settings)
        self.run_cli("config", "--retention-days", "120")
        self.assertEqual((load_settings(self.project) or {})["schemaVersion"], 4)
        settings = load_settings(self.project) or {}
        settings["schemaVersion"] = 999
        write_settings(self.project, settings)
        self.assertIsInstance(json.loads(self.run_cli("list", "--json").stdout), list)

    def test_safe_export_exposes_collector_health_without_payloads(self) -> None:
        self.run_cli("collect", "enable")
        record_collector_receipt(self.project, {
            "id": "receipt-export", "collector": "agent-lifecycle", "trigger": "agent-hook",
            "status": "partial", "observedCount": 3, "acceptedCount": 1,
            "duplicateCount": 1, "rejectedCount": 1, "errorCode": "invalid-event",
            "startedAt": "2026-08-29T01:00:00+00:00", "finishedAt": "2026-08-29T01:00:02+00:00",
        })
        exported = safe_export(self.project)
        self.assertIn("notifications", exported)
        self.assertEqual(exported["collectorReceipts"], [{
            "id": "receipt-export", "collector": "agent-lifecycle", "trigger": "agent-hook",
            "status": "partial", "observedCount": 3, "acceptedCount": 1,
            "duplicateCount": 1, "rejectedCount": 1, "errorCode": "invalid-event",
            "contractVersion": 1, "startedAt": "2026-08-29T01:00:00+00:00",
            "finishedAt": "2026-08-29T01:00:02+00:00",
        }])
        self.assertNotIn("payload", json.dumps(exported["collectorReceipts"]).lower())

    def test_git_collection_is_off_by_default_and_content_free(self) -> None:
        denied = self.run_cli("git", ok=False)
        self.assertIn("Git metadata is off", denied.stderr)
        self.run_cli("config", "--git-metadata", "on")
        result = json.loads(self.run_cli("git").stdout)
        self.assertEqual(result, {"examined": 0, "inserted": 0, "candidateLinks": 0})

    def test_git_candidate_can_be_confirmed_and_rejected(self) -> None:
        started = self.event("start")
        digest = "a" * 40
        with connect(self.project) as connection:
            connection.execute(
                "INSERT INTO commits(hash,project_hash,parent_hashes,committed_at,additions,deletions,file_count) "
                "VALUES(?,?,?,?,?,?,?)",
                (digest, root_hash(self.project), "", "2026-08-28T00:00:00+00:00", 4, 1, 2),
            )
        confirmed = json.loads(self.run_cli(
            "git-link", "--task-id", started["taskId"], "--commit-hash", digest, "--status", "confirmed"
        ).stdout)
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(confirmed["confidence"], 1.0)
        rejected = json.loads(self.run_cli(
            "git-link", "--task-id", started["taskId"], "--commit-hash", digest, "--status", "rejected"
        ).stdout)
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["confidence"], 0.0)

    def test_runtime_namespace_is_not_generic_tasks(self) -> None:
        self.assertTrue((ROOT / "src" / "coderai" / "project_tasks" / "cli.py").is_file())
        self.assertFalse((ROOT / "coder_ai_os").exists())
        self.assertFalse((ROOT / "tasks" / "cli.py").exists())

    def test_partial_agent_input_returns_guidance_without_cli_error(self) -> None:
        result = self.run_cli("event", "start", "--json", '{"unexpected":"value"}')
        payload = json.loads(result.stdout)
        self.assertFalse(payload["accepted"])
        self.assertTrue(payload["questions"])
        self.assertEqual(payload["ignoredFields"], ["unexpected"])

    def test_agent_input_redacts_secrets_and_ignores_raw_prompt_fields(self) -> None:
        result = self.run_cli("event", "start", "--json", json.dumps({
            "title": "Configure API", "summary": "authorization=Bearer abcdefghijklmnop",
            "rawPrompt": "do not store this transcript", "agent": "codex",
        }))
        payload = json.loads(result.stdout)
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["ignoredFields"], ["rawPrompt"])
        self.assertTrue(payload["redactions"])
        details = json.loads(self.run_cli("show", payload["taskId"]).stdout)
        self.assertNotIn("abcdefghijklmnop", json.dumps(details))

    def test_native_session_matches_continuation_without_repeating_title(self) -> None:
        started = self.event("start")
        data = {"title": "Continue implementation", "summary": "Work on the next isolated part",
                "agent": "codex", "nativeSessionId": "thread-local", "resumeSupported": True}
        continued = json.loads(self.run_cli("event", "continue", "--json", json.dumps(data)).stdout)
        self.assertTrue(continued["accepted"])
        self.assertEqual(continued["taskId"], started["taskId"])
        self.assertEqual(continued["matchConfidence"], 0.98)

    def test_new_start_does_not_reuse_task_from_native_session(self) -> None:
        first = self.event("start")
        second = json.loads(self.run_cli("event", "start", "--json", json.dumps({
            "title": "Improve human-readable graph",
            "summary": "Create a distinct project graph task",
            "agent": "codex",
            "nativeSessionId": "thread-local",
            "resumeSupported": True,
        })).stdout)
        self.assertNotEqual(second["taskId"], first["taskId"])
        self.assertEqual(second["matchConfidence"], 1.0)

    def test_unmatched_continuation_returns_question_instead_of_creating_task(self) -> None:
        payload = json.loads(self.run_cli(
            "event", "continue", "--json", '{"title":"Unknown work","summary":"No matching task"}'
        ).stdout)
        self.assertFalse(payload["accepted"])
        self.assertTrue(payload["questions"])


if __name__ == "__main__":
    unittest.main()
