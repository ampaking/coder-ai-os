#!/usr/bin/env python3
"""Recovery, migration, concurrency, timestamp, and metadata-boundary tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from coderai.project_tasks import SCHEMA_VERSION
from coderai.project_tasks.git_metadata import parse_git_log
from coderai.project_tasks.project import state_dir
from coderai.project_tasks.storage import cleanup, StorageError, connect, initialize, record_collector_receipt, record_event

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "src" / "coderai" / "project_tasks" / "cli.py"


class ProjectTasksHardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = (Path(temporary.name) / "sample").resolve()
        (self.project / ".coder-ai").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        digest = hashlib.sha256(str(self.project).encode()).hexdigest()
        (self.project / ".coder-ai" / "identity.json").write_text(json.dumps({
            "project_id": "sample", "git_root_hash": f"sha256:{digest}", "remote_hash": "sha256:test",
        }), encoding="utf-8")
        self.run_cli("enable")

    def run_cli(self, *arguments: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(CLI), "--project", str(self.project), *arguments],
            capture_output=True, text=True, check=False,
        )
        if ok and result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

    @staticmethod
    def payload(index: int, occurred_at: str | None = None) -> dict[str, object]:
        value: dict[str, object] = {
            "eventId": f"event-{index}", "type": "task_started", "summary": f"Task {index}",
            "task": {"id": f"task-{index}", "title": f"Task {index}"},
            "session": {"id": f"session-{index}", "agentName": "test"},
        }
        if occurred_at is not None:
            value["occurredAt"] = occurred_at
        return value

    def test_corrupt_database_is_diagnosed_and_repaired_with_backup(self) -> None:
        database = state_dir(self.project) / "tasks.sqlite3"
        database.write_bytes(b"not a sqlite database")
        health = self.run_cli("doctor", ok=False)
        self.assertEqual(health.returncode, 1)
        self.assertFalse(json.loads(health.stdout)["healthy"])
        event = json.loads(self.run_cli(
            "event", "start", "--json", '{"title":"Safe retry","summary":"Do not crash"}',
        ).stdout)
        self.assertFalse(event["accepted"])
        self.assertIn("repair", event["questions"][0].lower())
        repaired = json.loads(self.run_cli("repair", "--yes").stdout)
        self.assertTrue(Path(repaired["backup"]).is_file())
        self.assertTrue(json.loads(self.run_cli("doctor").stdout)["healthy"])

    def test_schema_v2_migrates_git_link_status_without_data_loss(self) -> None:
        database = state_dir(self.project) / "tasks.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("ALTER TABLE task_commits DROP COLUMN status")
            connection.execute("PRAGMA user_version = 2")
        with connect(self.project) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(task_commits)")}
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        self.assertIn("status", columns)
        self.assertEqual(version, SCHEMA_VERSION)

    def test_schema_v3_adds_bounded_observation_contract(self) -> None:
        database = state_dir(self.project) / "tasks.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TABLE observations")
            connection.execute("PRAGMA user_version = 3")
        with connect(self.project) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(observations)")}
            indexes = {row[1] for row in connection.execute("PRAGMA index_list(observations)")}
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        self.assertEqual(columns, {
            "id", "project_hash", "task_id", "event_id", "source", "kind", "scope",
            "outcome", "summary", "metric_value", "metric_unit", "confidence", "occurred_at",
            "collected_at",
        })
        self.assertIn("observations_project_source_time", indexes)
        self.assertIn("observations_project_kind_outcome_time", indexes)
        self.assertIn("observations_project_time", indexes)
        self.assertEqual(version, SCHEMA_VERSION)

    def test_schema_v4_adds_collector_receipts_without_rewriting_evidence(self) -> None:
        self.run_cli("event", "start", "--json", '{"title":"Existing task","summary":"Keep me"}')
        database = state_dir(self.project) / "tasks.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TABLE collector_receipts")
            connection.execute("PRAGMA user_version = 4")
        with connect(self.project) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(collector_receipts)")}
            indexes = {row[1] for row in connection.execute("PRAGMA index_list(collector_receipts)")}
            task_count = int(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        self.assertIn("collector", columns)
        self.assertIn("observed_count", columns)
        self.assertIn("finished_at", columns)
        self.assertIn("collector_receipts_project_time", indexes)
        self.assertEqual(task_count, 1)
        self.assertEqual(version, SCHEMA_VERSION)

    def test_schema_v6_preserves_notification_state_and_expands_truthful_kind(self) -> None:
        database = state_dir(self.project) / "tasks.sqlite3"
        project_hash = hashlib.sha256(str(self.project).encode()).hexdigest()
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TABLE notifications")
            connection.execute(
                "CREATE TABLE notifications (id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,fingerprint TEXT NOT NULL,"
                "kind TEXT NOT NULL CHECK(kind IN ('weekly-progress','attention')),severity TEXT NOT NULL,title TEXT NOT NULL,"
                "body TEXT NOT NULL,evidence_json TEXT NOT NULL,state TEXT NOT NULL,period_start TEXT NOT NULL,period_end TEXT NOT NULL,"
                "created_at TEXT NOT NULL,updated_at TEXT NOT NULL,delivered_at TEXT,UNIQUE(project_hash,fingerprint))"
            )
            connection.execute(
                "INSERT INTO notifications VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("notice-1", f"sha256:{project_hash}", "old", "attention", "warning", "Keep", "Keep body", "[]",
                 "read", "2026-08-29T00:00:00+00:00", "2026-08-29T01:00:00+00:00",
                 "2026-08-29T01:00:00+00:00", "2026-08-29T01:00:00+00:00", None),
            )
            connection.execute("PRAGMA user_version = 6")
        with connect(self.project) as connection:
            row = connection.execute("SELECT title,state FROM notifications WHERE id='notice-1'").fetchone()
            connection.execute(
                "INSERT INTO notifications VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("notice-2", f"sha256:{project_hash}", "new", "daily-reflection", "information", "New", "Body", "[]",
                 "unread", "2026-08-29T00:00:00+00:00", "2026-08-29T01:00:00+00:00",
                 "2026-08-29T01:00:00+00:00", "2026-08-29T01:00:00+00:00", None),
            )
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        self.assertEqual(tuple(row), ("Keep", "read"))
        self.assertEqual(version, SCHEMA_VERSION)

    def test_schema_upgrade_creates_verified_private_backup(self) -> None:
        self.run_cli("event", "start", "--json", '{"title":"Preserve me","summary":"Existing data"}')
        database = state_dir(self.project) / "tasks.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TABLE collector_receipts")
            connection.execute("PRAGMA user_version = 4")
        with connect(self.project) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
        backups = list((state_dir(self.project) / "backups").glob("*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True) as backup:
            self.assertEqual(backup.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
        health = json.loads(self.run_cli("doctor").stdout)
        self.assertEqual(health["migrationBackups"], 1)
        self.assertEqual(health["latestMigrationBackup"], backups[0].name)

    def test_current_open_does_not_create_backup(self) -> None:
        with connect(self.project):
            pass
        self.assertFalse((state_dir(self.project) / "backups").exists())

    def test_migration_backups_are_bounded(self) -> None:
        database = state_dir(self.project) / "tasks.sqlite3"
        for _index in range(7):
            with sqlite3.connect(database) as connection:
                connection.execute("PRAGMA user_version = 4")
            with connect(self.project):
                pass
        backups = list((state_dir(self.project) / "backups").glob("*.sqlite3"))
        self.assertEqual(len(backups), 5)

    def test_newer_database_is_rejected_without_downgrade(self) -> None:
        database = state_dir(self.project) / "tasks.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        with self.assertRaisesRegex(StorageError, "newer coder-ai-os"):
            with connect(self.project):
                pass
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION + 1)

    def test_failed_migration_rolls_back_and_preserves_backup(self) -> None:
        self.run_cli("event", "start", "--json", '{"title":"Still here","summary":"Rollback proof"}')
        database = state_dir(self.project) / "tasks.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA user_version = 4")
        with mock.patch(
            "coderai.project_tasks.storage._execute_schema",
            side_effect=sqlite3.DatabaseError("forced migration failure"),
        ):
            with self.assertRaisesRegex(StorageError, "needs repair"):
                initialize(self.project)
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
        self.assertEqual(len(list((state_dir(self.project) / "backups").glob("*.sqlite3"))), 1)

    def test_database_symlink_is_rejected_without_touching_target(self) -> None:
        database = state_dir(self.project) / "tasks.sqlite3"
        preserved = state_dir(self.project) / "preserved.sqlite3"
        database.replace(preserved)
        database.symlink_to(preserved)
        with self.assertRaisesRegex(StorageError, "symlink database"):
            initialize(self.project)
        with sqlite3.connect(preserved) as connection:
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
        health = json.loads(self.run_cli("doctor", ok=False).stdout)
        self.assertFalse(health["healthy"])
        self.assertIn("symlink", health["detail"])

    def test_collector_receipts_respect_opt_out_and_are_idempotent_and_content_free(self) -> None:
        receipt = {
            "id": "receipt-agent-1", "collector": "agent-lifecycle", "trigger": "agent-hook",
            "status": "success", "observedCount": 2, "acceptedCount": 1, "duplicateCount": 1,
            "startedAt": "2026-08-29T01:00:00+00:00", "finishedAt": "2026-08-29T01:00:01+00:00",
        }
        self.run_cli("collect", "disable")
        with self.assertRaisesRegex(StorageError, "collection is off"):
            record_collector_receipt(self.project, receipt)
        self.run_cli("collect", "enable")
        first = record_collector_receipt(self.project, receipt)
        replay = record_collector_receipt(self.project, receipt)
        self.assertFalse(first["duplicate"])
        self.assertTrue(replay["duplicate"])
        with connect(self.project) as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM collector_receipts")]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("summary", rows[0])
        self.assertNotIn("content", rows[0])
        with self.assertRaisesRegex(StorageError, "unsupported field"):
            record_collector_receipt(self.project, {**receipt, "id": "receipt-agent-2", "details": "path"})
        with self.assertRaisesRegex(StorageError, "machine identifier"):
            record_collector_receipt(self.project, {**receipt, "id": "receipt-agent-3", "errorCode": "raw output"})
        record_collector_receipt(self.project, {
            **receipt, "id": "receipt-old", "startedAt": "2020-01-01T00:00:00+00:00",
            "finishedAt": "2020-01-01T00:00:01+00:00",
        })
        self.assertGreaterEqual(cleanup(self.project), 1)
        with connect(self.project) as connection:
            ids = {row[0] for row in connection.execute("SELECT id FROM collector_receipts")}
        self.assertEqual(ids, {"receipt-agent-1"})

    def test_parallel_writers_are_serialized_without_lost_events(self) -> None:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda index: record_event(self.project, self.payload(index)), range(24)))
        self.assertEqual(len({item["eventId"] for item in results}), 24)
        with connect(self.project) as connection:
            count = int(connection.execute("SELECT count(*) FROM task_events").fetchone()[0])
        self.assertEqual(count, 24)

    def test_timestamp_requires_timezone_and_agent_path_returns_guidance(self) -> None:
        with self.assertRaisesRegex(StorageError, "timezone"):
            record_event(self.project, self.payload(1, "2026-08-28T12:00:00"))
        result = json.loads(self.run_cli(
            "event", "start", "--json",
            '{"title":"Timestamp","summary":"Validate time","occurredAt":"not-a-time"}',
        ).stdout)
        self.assertFalse(result["accepted"])
        self.assertTrue(result["questions"])

    def test_git_parser_keeps_only_content_free_aggregates(self) -> None:
        digest = "a" * 40
        output = f"{digest}\t\t2026-08-28T10:00:00+00:00\n 3 files changed, 12 insertions(+), 4 deletions(-)\n"
        commits = parse_git_log(output)
        self.assertEqual(commits, [{
            "hash": digest, "parents": "", "at": "2026-08-28T10:00:00+00:00",
            "add": 12, "del": 4, "files": 3,
        }])
        self.assertNotIn("path", json.dumps(commits).lower())


if __name__ == "__main__":
    unittest.main()
