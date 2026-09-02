"""Deterministic task lifecycle updates derived from structured events."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from coderai.project_tasks.storage import TASK_STATUSES, StorageError, new_id, now

EVENT_STATUS = {
    "task_started": "active",
    "task_continued": "active",
    "task_paused": "paused",
    "task_resumed": "active",
    "task_blocked": "blocked",
    "validation_started": "needs_validation",
    "validation_failed": "needs_validation",
    "completion_proposed": "needs_validation",
    "task_corrected": None,
    "decision_recorded": None,
    "session_started": None,
    "session_ended": None,
    "idea_proposed": None,
    "task_split": None,
    "validation_passed": None,
    "task_completed": None,
}

LINK_TYPES = {"depends_on", "blocked_by", "split_from", "related_to", "follow_up"}
CORRECTABLE_FIELDS = {"title", "summary", "theme", "status"}


def _text(value: Any, name: str, maximum: int, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise StorageError(f"{name} is required")
    if len(result) > maximum:
        raise StorageError(f"{name} exceeds {maximum} characters")
    return result


def _has_passing_validation(connection: sqlite3.Connection, task_id: str) -> bool:
    passing = connection.execute(
        "SELECT COALESCE(MAX(sequence),0) FROM task_events WHERE task_id=? AND event_type='validation_passed'",
        (task_id,),
    ).fetchone()[0]
    changed = connection.execute(
        "SELECT COALESCE(MAX(sequence),0) FROM task_events WHERE task_id=? AND event_type IN "
        "('task_started','task_continued','task_resumed','task_corrected','validation_failed')",
        (task_id,),
    ).fetchone()[0]
    return int(passing) > int(changed)


def apply_event(
    connection: sqlite3.Connection,
    project_hash: str,
    task_id: str,
    session_id: str,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    occurred_at: str,
    confidence: float,
) -> str:
    """Apply event side effects and return the resulting task status."""
    status = EVENT_STATUS[event_type]
    details = payload.get("details") or {}
    if not isinstance(details, dict):
        raise StorageError("details must be an object")

    if event_type == "validation_passed" or event_type == "validation_failed":
        validation = payload.get("validation") or {}
        if not isinstance(validation, dict):
            raise StorageError("validation must be an object")
        outcome = "passed" if event_type == "validation_passed" else "failed"
        validation_id = _text(validation.get("id"), "validation.id", 120) or new_id("validation")
        category = _text(validation.get("category") or "test", "validation.category", 80, True)
        duration_ms = validation.get("durationMs")
        connection.execute(
            "INSERT INTO validations(id,task_id,session_id,category,command_summary,outcome,duration_ms,occurred_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                validation_id,
                task_id,
                session_id,
                category,
                _text(validation.get("commandSummary"), "validation.commandSummary", 500),
                outcome,
                duration_ms,
                occurred_at,
            ),
        )
        connection.execute(
            "INSERT OR IGNORE INTO observations("
            "id,project_hash,task_id,event_id,source,kind,scope,outcome,summary,metric_value,metric_unit,"
            "confidence,occurred_at,collected_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"observation_{event_id}", project_hash, task_id, event_id, "repository-checks",
                category, "task", outcome, f"{category} validation {outcome}", duration_ms,
                "milliseconds" if duration_ms is not None else "", confidence, occurred_at, now(),
            ),
        )
        if event_type == "validation_passed":
            current = connection.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
            status = "completed" if current == "completed" else "active"

    if event_type == "task_completed":
        status = "completed" if _has_passing_validation(connection, task_id) else "needs_validation"

    if event_type == "decision_recorded":
        connection.execute(
            "INSERT INTO decisions(id,task_id,summary,reason,created_at) VALUES(?,?,?,?,?)",
            (
                _text(details.get("id"), "details.id", 120) or new_id("decision"),
                task_id,
                _text(details.get("summary") or payload.get("summary"), "details.summary", 2000, True),
                _text(details.get("reason"), "details.reason", 4000),
                occurred_at,
            ),
        )

    if event_type == "task_blocked":
        connection.execute(
            "INSERT INTO blockers(id,task_id,summary,status,created_at) VALUES(?,?,?,?,?)",
            (
                _text(details.get("id"), "details.id", 120) or new_id("blocker"),
                task_id,
                _text(details.get("summary") or payload.get("summary"), "details.summary", 2000, True),
                "open",
                occurred_at,
            ),
        )

    if event_type == "task_corrected":
        correction = payload.get("correction") or {}
        if not isinstance(correction, dict):
            raise StorageError("correction must be an object")
        field = _text(correction.get("field"), "correction.field", 80, True)
        if field not in CORRECTABLE_FIELDS:
            raise StorageError(f"field cannot be corrected: {field}")
        current = connection.execute(f"SELECT {field} FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
        corrected = _text(correction.get("value"), "correction.value", 2000, True)
        reason = _text(correction.get("reason"), "correction.reason", 2000)
        if field == "status" and corrected not in TASK_STATUSES:
            raise StorageError(f"unsupported task status: {corrected}")
        if field == "status" and not reason:
            raise StorageError("correction.reason is required for status overrides")
        if field != "status":
            connection.execute(f"UPDATE tasks SET {field}=?,updated_at=? WHERE id=?", (corrected, now(), task_id))
        connection.execute(
            "INSERT INTO corrections(id,task_id,field_name,previous_value,corrected_value,reason,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                new_id("correction"), task_id, field, str(current), corrected,
                reason, occurred_at,
            ),
        )
        status = corrected if field == "status" else None

    links = payload.get("links") or []
    if not isinstance(links, list):
        raise StorageError("links must be a list")
    for link in links:
        if not isinstance(link, dict):
            raise StorageError("each link must be an object")
        related_id = _text(link.get("taskId"), "links.taskId", 120, True)
        link_type = _text(link.get("type"), "links.type", 40, True)
        if link_type not in LINK_TYPES:
            raise StorageError(f"unsupported task link: {link_type}")
        if connection.execute("SELECT 1 FROM tasks WHERE id=?", (related_id,)).fetchone() is None:
            raise StorageError(f"related task does not exist: {related_id}")
        connection.execute(
            "INSERT OR IGNORE INTO task_links(task_id,related_task_id,link_type,created_at) VALUES(?,?,?,?)",
            (task_id, related_id, link_type, occurred_at),
        )

    if event_type == "session_ended":
        connection.execute("UPDATE sessions SET ended_at=? WHERE id=?", (occurred_at, session_id))

    if event_type == "task_resumed":
        connection.execute(
            "UPDATE blockers SET status='resolved',resolved_at=? WHERE task_id=? AND status='open'",
            (occurred_at, task_id),
        )

    if status is not None:
        completed_at = occurred_at if status == "completed" else None
        connection.execute(
            "UPDATE tasks SET status=?,updated_at=?,completed_at=? WHERE id=?",
            (status, now(), completed_at, task_id),
        )
    row = connection.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    return str(row[0])


def task_details(connection: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if task is None:
        return None
    result = dict(task)
    event_effort = connection.execute(
        "SELECT COUNT(*) AS eventCount,MIN(occurred_at) AS firstAt,MAX(occurred_at) AS lastAt,"
        "COALESCE((julianday(MAX(occurred_at))-julianday(MIN(occurred_at)))*1440,0) AS spanMinutes "
        "FROM task_events WHERE task_id=?", (task_id,),
    ).fetchone()
    session_effort = connection.execute(
        "SELECT COUNT(*) AS endedSessions,"
        "COALESCE(SUM((julianday(ended_at)-julianday(started_at))*1440),0) AS sessionMinutes "
        "FROM sessions WHERE task_id=? AND ended_at IS NOT NULL", (task_id,),
    ).fetchone()
    result["observedEffort"] = {
        "eventCount": int(event_effort["eventCount"]),
        "firstAt": event_effort["firstAt"],
        "lastAt": event_effort["lastAt"],
        "evidenceSpanMinutes": max(0, round(float(event_effort["spanMinutes"]), 1)),
        "endedSessions": int(session_effort["endedSessions"]),
        "endedSessionMinutes": max(0, round(float(session_effort["sessionMinutes"]), 1)),
        "caution": "Evidence span is elapsed time between recorded events, not continuous human work.",
    }
    result["evidenceBounds"] = {
        "validations": connection.execute("SELECT COUNT(*) FROM validations WHERE task_id=?", (task_id,)).fetchone()[0],
        "blockers": connection.execute("SELECT COUNT(*) FROM blockers WHERE task_id=?", (task_id,)).fetchone()[0],
        "commits": connection.execute("SELECT COUNT(*) FROM task_commits WHERE task_id=?", (task_id,)).fetchone()[0],
        "events": connection.execute("SELECT COUNT(*) FROM task_events WHERE task_id=?", (task_id,)).fetchone()[0],
        "links": connection.execute("SELECT COUNT(*) FROM task_links WHERE task_id=?", (task_id,)).fetchone()[0],
        "corrections": connection.execute("SELECT COUNT(*) FROM corrections WHERE task_id=?", (task_id,)).fetchone()[0],
        "limit": 100,
    }
    result["links"] = [dict(row) for row in connection.execute(
        "SELECT related_task_id AS taskId,link_type AS type FROM task_links WHERE task_id=? "
        "ORDER BY created_at DESC LIMIT 100", (task_id,)
    )]
    result["validations"] = [dict(row) for row in connection.execute(
        "SELECT category,command_summary,outcome,duration_ms,occurred_at FROM validations "
        "WHERE task_id=? ORDER BY occurred_at DESC LIMIT 100", (task_id,)
    )]
    result["blockers"] = [dict(row) for row in connection.execute(
        "SELECT summary,status,created_at,resolved_at FROM blockers WHERE task_id=? "
        "ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, created_at DESC LIMIT 100",
        (task_id,),
    )]
    result["corrections"] = [dict(row) for row in connection.execute(
        "SELECT field_name,previous_value,corrected_value,reason,created_at FROM corrections "
        "WHERE task_id=? ORDER BY created_at DESC LIMIT 100", (task_id,)
    )]
    result["commits"] = [dict(row) for row in connection.execute(
        "SELECT commits.hash,commits.committed_at,commits.additions,commits.deletions,commits.file_count,"
        "task_commits.confidence,task_commits.status,task_commits.evidence_json FROM task_commits "
        "JOIN commits ON commits.hash=task_commits.commit_hash WHERE task_commits.task_id=? "
        "ORDER BY commits.committed_at DESC LIMIT 100", (task_id,)
    )]
    for commit in result["commits"]:
        commit["evidence"] = json.loads(commit.pop("evidence_json"))
    result["events"] = [dict(row) for row in connection.execute(
        "SELECT id,event_type,summary,evidence_json,confidence,occurred_at FROM task_events "
        "WHERE task_id=? ORDER BY sequence DESC LIMIT 100", (task_id,)
    )]
    for event in result["events"]:
        event["evidence"] = json.loads(event.pop("evidence_json"))
    return result
