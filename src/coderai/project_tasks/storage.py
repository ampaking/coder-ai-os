"""SQLite storage and structured event ingestion."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from coderai.project_tasks import SCHEMA_VERSION, SETTINGS_VERSION
from coderai.project_tasks.project import ProjectError, load_settings, root_hash, state_dir

TASK_STATUSES = {
    "proposed", "active", "paused", "blocked", "needs_validation", "completed", "cancelled"
}
EVENT_TYPES = {
    "task_started", "task_continued", "task_split", "task_paused", "task_resumed",
    "task_blocked", "decision_recorded", "validation_started", "validation_passed",
    "validation_failed", "completion_proposed", "task_completed", "task_corrected",
    "session_started", "session_ended", "idea_proposed",
}
COLLECTOR_TRIGGERS = {"agent-hook", "validation-hook", "git-hook", "manual", "service"}
COLLECTOR_STATUSES = {"success", "no_change", "partial", "failed"}
COLLECTOR_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}")
FORBIDDEN_KEYS = {
    "rawprompt", "prompt", "sourcecode", "content", "password", "passwd", "secret", "token",
    "credential", "credentials", "authorization", "cookie", "privatekey", "apikey", "api_key",
}
MIGRATION_BACKUP_LIMIT = 5


class StorageError(RuntimeError):
    """Raised for invalid or unsafe task data."""


def now() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4()}"


def _reject_forbidden(value: Any, location: str = "event") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "").replace("_", "")
            if normalized in {item.replace("_", "") for item in FORBIDDEN_KEYS}:
                raise StorageError(f"{location} contains forbidden field: {key}")
            _reject_forbidden(child, f"{location}.{key}")
    elif isinstance(value, list):
        if len(value) > 100:
            raise StorageError(f"{location} contains too many items")
        for index, child in enumerate(value):
            _reject_forbidden(child, f"{location}[{index}]")
    elif isinstance(value, str) and len(value) > 12_000:
        raise StorageError(f"{location} contains an oversized string")


def database_path(project: Path) -> Path:
    return state_dir(project) / "tasks.sqlite3"


def _migration_backup_dir(project: Path) -> Path:
    return state_dir(project) / "backups"


def _create_migration_backup(
    project: Path, target: Path, from_version: int, to_version: int,
) -> Path:
    directory = _migration_backup_dir(project)
    if directory.is_symlink():
        raise StorageError("refusing unsafe migration backup path")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = directory / f"tasks.v{from_version}.before-v{to_version}.{stamp}.sqlite3"
    if backup.exists() or backup.is_symlink():
        raise StorageError("refusing unsafe migration backup path")
    source = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=5)
    destination = sqlite3.connect(backup, timeout=5)
    try:
        source.backup(destination)
        destination.commit()
    except BaseException:
        destination.close()
        source.close()
        backup.unlink(missing_ok=True)
        raise
    destination.close()
    source.close()
    os.chmod(backup, 0o600)
    try:
        verifier = sqlite3.connect(f"file:{backup}?mode=ro", uri=True, timeout=5)
        try:
            check = str(verifier.execute("PRAGMA quick_check").fetchone()[0])
            version = int(verifier.execute("PRAGMA user_version").fetchone()[0])
        finally:
            verifier.close()
    except sqlite3.DatabaseError as exc:
        backup.unlink(missing_ok=True)
        raise StorageError("could not verify the pre-migration backup") from exc
    if check != "ok" or version != from_version:
        backup.unlink(missing_ok=True)
        raise StorageError("could not verify the pre-migration backup")
    return backup


def _prune_migration_backups(project: Path) -> None:
    directory = _migration_backup_dir(project)
    if not directory.exists() or directory.is_symlink():
        return
    backups = sorted(
        directory.glob("tasks.v*.before-v*.sqlite3"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )
    for backup in backups[MIGRATION_BACKUP_LIMIT:]:
        if backup.is_file() and not backup.is_symlink():
            backup.unlink()


def _execute_schema(connection: sqlite3.Connection, schema: str) -> None:
    statement = ""
    for line in schema.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            if statement.strip():
                connection.execute(statement)
            statement = ""
    if statement.strip():
        raise StorageError("Project Tasks schema contains an incomplete statement")


def database_health(project: Path) -> dict[str, Any]:
    target = database_path(project)
    if target.is_symlink():
        return {"healthy": False, "detail": "refusing symlink database path", "migrationBackups": 0}
    if not target.exists():
        return {"healthy": True, "detail": "database not created yet", "migrationBackups": 0}
    try:
        connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=2)
        try:
            result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        return {"healthy": False, "detail": "SQLite database is unreadable"}
    backup_directory = _migration_backup_dir(project)
    backups = [] if not backup_directory.is_dir() or backup_directory.is_symlink() else sorted(
        item.name for item in backup_directory.glob("tasks.v*.before-v*.sqlite3")
        if item.is_file() and not item.is_symlink()
    )
    return {
        "healthy": result == "ok",
        "detail": result,
        "migrationBackups": len(backups),
        "latestMigrationBackup": backups[-1] if backups else None,
    }


def repair_database(project: Path) -> dict[str, Any]:
    require_enabled(project)
    target = database_path(project)
    health = database_health(project)
    if health["healthy"]:
        return {**health, "repaired": False, "backup": None}
    backup = target.with_name(f"tasks.corrupt.{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.sqlite3")
    if backup.exists() or target.is_symlink() or backup.is_symlink():
        raise StorageError("refusing unsafe database repair path")
    target.replace(backup)
    initialize(project)
    return {"healthy": True, "detail": "created a clean database; corrupt file preserved",
            "repaired": True, "backup": str(backup)}


def require_enabled(project: Path) -> dict[str, Any]:
    settings = load_settings(project)
    if not settings or not settings.get("enabled"):
        raise StorageError("Project Tasks is disabled; run: coder-ai-os tasks enable")
    if int(settings.get("settingsVersion", 1)) > SETTINGS_VERSION:
        raise StorageError("task settings were created by a newer coder-ai-os version")
    return settings


def initialize(project: Path) -> Path:
    require_enabled(project)
    directory = state_dir(project)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    target = database_path(project)
    if target.is_symlink():
        raise StorageError("refusing symlink database path")
    existed = target.exists() and target.stat().st_size > 0
    connection = sqlite3.connect(target, timeout=5)
    backup: Path | None = None
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
        connection.execute("BEGIN IMMEDIATE")
        existing_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if existing_version > SCHEMA_VERSION:
            raise StorageError(
                "task database was created by a newer coder-ai-os version; update or restart before opening it"
            )
        if existed and existing_version < SCHEMA_VERSION:
            backup = _create_migration_backup(
                project, target, existing_version, SCHEMA_VERSION,
            )
        _execute_schema(connection, schema)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
        if existing_version < 2 and "estimated_minutes" not in columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN estimated_minutes INTEGER CHECK(estimated_minutes IS NULL OR estimated_minutes >= 0)")
        if existing_version < 2 and "actual_minutes" not in columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN actual_minutes INTEGER CHECK(actual_minutes IS NULL OR actual_minutes >= 0)")
        link_columns = {row[1] for row in connection.execute("PRAGMA table_info(task_commits)")}
        if existing_version < 3 and "status" not in link_columns:
            connection.execute("ALTER TABLE task_commits ADD COLUMN status TEXT NOT NULL DEFAULT 'candidate'")
        if existing_version == 6:
            connection.execute(
                "CREATE TABLE notifications_v7 (id TEXT PRIMARY KEY,project_hash TEXT NOT NULL REFERENCES projects(project_hash) ON DELETE CASCADE,"
                "fingerprint TEXT NOT NULL CHECK(length(fingerprint) BETWEEN 1 AND 160),"
                "kind TEXT NOT NULL CHECK(kind IN ('weekly-progress','daily-reflection','attention')),"
                "severity TEXT NOT NULL CHECK(severity IN ('information','warning')),"
                "title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 160),body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 500),"
                "evidence_json TEXT NOT NULL DEFAULT '[]' CHECK(length(evidence_json) <= 4000),"
                "state TEXT NOT NULL DEFAULT 'unread' CHECK(state IN ('unread','read','dismissed')),"
                "period_start TEXT NOT NULL,period_end TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,delivered_at TEXT,"
                "UNIQUE(project_hash,fingerprint))"
            )
            connection.execute(
                "INSERT INTO notifications_v7(id,project_hash,fingerprint,kind,severity,title,body,evidence_json,state,period_start,period_end,created_at,updated_at,delivered_at) "
                "SELECT id,project_hash,fingerprint,kind,severity,title,body,evidence_json,state,period_start,period_end,created_at,updated_at,delivered_at FROM notifications"
            )
            connection.execute("DROP TABLE notifications")
            connection.execute("ALTER TABLE notifications_v7 RENAME TO notifications")
            connection.execute(
                "CREATE INDEX notifications_project_state_time ON notifications(project_hash,state,updated_at DESC)"
            )
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        timestamp = now()
        connection.execute(
            "INSERT INTO projects(project_hash,name,created_at,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(project_hash) DO UPDATE SET name=excluded.name,updated_at=excluded.updated_at",
            (root_hash(project), project.name, timestamp, timestamp),
        )
        connection.commit()
    except StorageError:
        connection.rollback()
        raise
    except (OSError, sqlite3.DatabaseError) as exc:
        connection.rollback()
        raise StorageError("Project Tasks storage needs repair before recording") from exc
    finally:
        connection.close()
    os.chmod(target, 0o600)
    if backup is not None:
        _prune_migration_backups(project)
    return target


@contextmanager
def connect(project: Path) -> Iterator[sqlite3.Connection]:
    require_enabled(project)
    target = database_path(project)
    if target.is_symlink():
        raise StorageError("refusing symlink database path")
    if not target.exists() or target.stat().st_size == 0:
        target = initialize(project)
    try:
        connection = sqlite3.connect(target, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if int(connection.execute("PRAGMA user_version").fetchone()[0]) != SCHEMA_VERSION:
            connection.close()
            target = initialize(project)
            connection = sqlite3.connect(target, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
    except sqlite3.DatabaseError as exc:
        if "connection" in locals():
            connection.close()
        raise StorageError("Project Tasks storage needs repair before recording") from exc
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _bounded_text(value: Any, name: str, maximum: int, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise StorageError(f"{name} is required")
    if len(text) > maximum:
        raise StorageError(f"{name} exceeds {maximum} characters")
    return text


def _optional_minutes(value: Any, name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise StorageError(f"{name} must be a non-negative integer")
    result = int(value)
    if result < 0 or result > 1_000_000:
        raise StorageError(f"{name} must be between 0 and 1000000")
    return result


def _timestamp(value: Any) -> str:
    text = _bounded_text(value, "occurredAt", 80, required=True)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StorageError("occurredAt must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise StorageError("occurredAt must include a timezone")
    return parsed.astimezone(UTC).isoformat()


def record_event(project: Path, payload: dict[str, Any]) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise StorageError("event must be a JSON object")
    _reject_forbidden(payload)
    if int(payload.get("eventVersion", 1)) != 1:
        raise StorageError("unsupported eventVersion")
    event_type = _bounded_text(payload.get("type"), "type", 80, required=True)
    if event_type not in EVENT_TYPES:
        raise StorageError(f"unsupported event type: {event_type}")
    summary = _bounded_text(payload.get("summary"), "summary", 2000, required=True)
    confidence = float(payload.get("confidence", 1.0))
    if not 0 <= confidence <= 1:
        raise StorageError("confidence must be between 0 and 1")
    task_data = payload.get("task") or {}
    session_data = payload.get("session") or {}
    if not isinstance(task_data, dict) or not isinstance(session_data, dict):
        raise StorageError("task and session must be objects")
    timestamp = _timestamp(payload.get("occurredAt") or now())
    task_id = _bounded_text(task_data.get("id"), "task.id", 120) or new_id("task")
    session_id = _bounded_text(session_data.get("id"), "session.id", 120) or new_id("session")
    event_id = _bounded_text(payload.get("eventId"), "eventId", 120) or new_id("event")
    title = _bounded_text(task_data.get("title") or summary, "task.title", 240, required=True)
    task_summary = _bounded_text(task_data.get("summary"), "task.summary", 2000)
    theme = _bounded_text(task_data.get("theme"), "task.theme", 120)
    status = _bounded_text(task_data.get("status") or "active", "task.status", 40)
    if status not in TASK_STATUSES:
        raise StorageError(f"unsupported task status: {status}")
    estimated_minutes = _optional_minutes(task_data.get("estimatedMinutes"), "task.estimatedMinutes")
    actual_minutes = _optional_minutes(task_data.get("actualMinutes"), "task.actualMinutes")
    agent_name = _bounded_text(session_data.get("agentName") or "unknown", "session.agentName", 80, True)
    agent_version = _bounded_text(session_data.get("agentVersion"), "session.agentVersion", 80)
    model_name = _bounded_text(session_data.get("modelName"), "session.modelName", 120)
    native_id = _bounded_text(session_data.get("nativeSessionId"), "session.nativeSessionId", 240) or None
    resume_supported = 1 if session_data.get("resumeSupported") else 0
    evidence = payload.get("evidence") or []
    if not isinstance(evidence, list):
        raise StorageError("evidence must be a list")
    evidence_json = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    if len(evidence_json) > 12_000:
        raise StorageError("evidence exceeds storage limit")
    created = now()
    project_key = root_hash(project)
    with connect(project) as connection:
        connection.execute(
            "INSERT INTO tasks(id,project_hash,title,summary,theme,status,confidence,source,estimated_minutes,actual_minutes,created_at,updated_at,started_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "title=excluded.title,summary=CASE WHEN excluded.summary='' THEN tasks.summary ELSE excluded.summary END,"
            "theme=CASE WHEN excluded.theme='' THEN tasks.theme ELSE excluded.theme END,"
            "estimated_minutes=COALESCE(excluded.estimated_minutes,tasks.estimated_minutes),"
            "actual_minutes=COALESCE(excluded.actual_minutes,tasks.actual_minutes),"
            "confidence=excluded.confidence,updated_at=excluded.updated_at",
            (task_id, project_key, title, task_summary, theme, status, confidence,
             _bounded_text(task_data.get("source") or "agent", "task.source", 40), estimated_minutes,
             actual_minutes, created, created, timestamp),
        )
        connection.execute(
            "INSERT INTO sessions(id,project_hash,task_id,agent_name,agent_version,model_name,native_session_id,resume_supported,started_at) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id,"
            "agent_version=excluded.agent_version,model_name=excluded.model_name,"
            "native_session_id=COALESCE(excluded.native_session_id,sessions.native_session_id),"
            "resume_supported=excluded.resume_supported",
            (session_id, project_key, task_id, agent_name, agent_version, model_name, native_id,
             resume_supported, timestamp),
        )
        event_cursor = connection.execute(
            "INSERT OR IGNORE INTO task_events(id,project_hash,task_id,session_id,event_type,summary,evidence_json,confidence,occurred_at,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (event_id, project_key, task_id, session_id, event_type, summary, evidence_json,
             confidence, timestamp, created),
        )
        if event_cursor.rowcount:
            from coderai.project_tasks.lifecycle import apply_event
            resulting_status = apply_event(
                connection, project_key, task_id, session_id, event_id, event_type, payload,
                timestamp, confidence,
            )
        else:
            resulting_status = str(connection.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()[0])
    return {"eventId": event_id, "sessionId": session_id, "taskId": task_id, "status": resulting_status}


def record_collector_receipt(project: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Record content-free collector health while project collection is enabled."""
    settings = require_enabled(project)
    if not settings.get("automaticCollection"):
        raise StorageError("automatic collection is off; run: coder-ai-os tasks collect enable")
    if not isinstance(payload, dict):
        raise StorageError("collector receipt must be an object")
    _reject_forbidden(payload)
    allowed = {"id", "collector", "trigger", "status", "observedCount", "acceptedCount",
               "duplicateCount", "rejectedCount", "errorCode", "startedAt", "finishedAt",
               "contractVersion"}
    unexpected = sorted(str(key) for key in payload if key not in allowed)
    if unexpected:
        raise StorageError(f"collector receipt contains unsupported field: {unexpected[0]}")
    collector = _bounded_text(payload.get("collector"), "collector", 80, True)
    error_code = _bounded_text(payload.get("errorCode"), "errorCode", 80)
    if not COLLECTOR_IDENTIFIER.fullmatch(collector):
        raise StorageError("collector must be a lowercase machine identifier")
    if error_code and not COLLECTOR_IDENTIFIER.fullmatch(error_code):
        raise StorageError("errorCode must be a lowercase machine identifier")
    trigger = _bounded_text(payload.get("trigger"), "trigger", 40, True)
    status = _bounded_text(payload.get("status"), "status", 40, True)
    if trigger not in COLLECTOR_TRIGGERS:
        raise StorageError("unsupported collector trigger")
    if status not in COLLECTOR_STATUSES:
        raise StorageError("unsupported collector status")
    contract_version = int(payload.get("contractVersion", 1))
    if contract_version != 1:
        raise StorageError("unsupported collector contractVersion")
    counts = []
    for key in ("observedCount", "acceptedCount", "duplicateCount", "rejectedCount"):
        value = int(payload.get(key, 0))
        if value < 0 or value > 1_000_000:
            raise StorageError(f"{key} must be between 0 and 1000000")
        counts.append(value)
    observed, accepted, duplicate, rejected = counts
    if accepted + duplicate + rejected > observed:
        raise StorageError("collector result counts exceed observedCount")
    started_at = _timestamp(payload.get("startedAt") or now())
    finished_at = _timestamp(payload.get("finishedAt") or now())
    if datetime.fromisoformat(finished_at) < datetime.fromisoformat(started_at):
        raise StorageError("finishedAt must not be before startedAt")
    receipt_id = _bounded_text(payload.get("id"), "id", 120) or new_id("receipt")
    with connect(project) as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO collector_receipts("
            "id,project_hash,collector,trigger_kind,status,observed_count,accepted_count,duplicate_count,"
            "rejected_count,error_code,contract_version,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (receipt_id, root_hash(project), collector, trigger, status, observed, accepted, duplicate,
             rejected, error_code, contract_version, started_at, finished_at),
        )
    return {"receiptId": receipt_id, "duplicate": not bool(cursor.rowcount), "status": status}


def list_tasks(project: Path, status: str | None = None) -> list[dict[str, Any]]:
    with connect(project) as connection:
        if status:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE project_hash=? AND status=? ORDER BY updated_at DESC",
                (root_hash(project), status),
            )
        else:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE project_hash=? ORDER BY updated_at DESC", (root_hash(project),)
            )
        return [dict(row) for row in rows]


def get_task(project: Path, task_id: str) -> dict[str, Any] | None:
    from coderai.project_tasks.lifecycle import task_details
    with connect(project) as connection:
        return task_details(connection, task_id)


def cleanup(project: Path) -> int:
    settings = require_enabled(project)
    retention = int(settings.get("retentionDays", 90))
    if retention <= 0:
        return 0
    cutoff = (datetime.now(UTC) - timedelta(days=retention)).isoformat()
    with connect(project) as connection:
        before = connection.total_changes
        connection.execute("DELETE FROM task_events WHERE occurred_at < ?", (cutoff,))
        connection.execute(
            "DELETE FROM tasks WHERE project_hash=? AND status IN ('completed','cancelled') AND updated_at < ?",
            (root_hash(project), cutoff),
        )
        connection.execute(
            "DELETE FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ? "
            "AND id NOT IN (SELECT session_id FROM task_events WHERE session_id IS NOT NULL)",
            (cutoff,),
        )
        connection.execute(
            "DELETE FROM collector_receipts WHERE project_hash=? AND finished_at < ?",
            (root_hash(project), cutoff),
        )
        connection.execute(
            "DELETE FROM observations WHERE project_hash=? AND occurred_at < ?",
            (root_hash(project), cutoff),
        )
        return int(connection.total_changes - before)
