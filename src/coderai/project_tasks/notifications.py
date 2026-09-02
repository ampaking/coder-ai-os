"""Deterministic, privacy-bounded local notification analysis."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from coderai.project_tasks.project import load_settings, root_hash
from coderai.project_tasks.storage import connect


def _notification_id(project_hash: str, fingerprint: str) -> str:
    digest = hashlib.sha256(f"{project_hash}:{fingerprint}".encode()).hexdigest()[:24]
    return f"notification_{digest}"


def analyze_notifications(project: Path, observed_at: datetime | None = None) -> list[dict[str, Any]]:
    """Create or refresh deduplicated evidence summaries; never infer human productivity."""
    current = (observed_at or datetime.now(UTC)).astimezone(UTC)
    settings = load_settings(project) or {}
    if not settings.get("notificationsEnabled"):
        return []
    week_start = (current - timedelta(days=current.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    project_hash = root_hash(project)
    with connect(project) as connection:
        event_rows = connection.execute(
            "SELECT event_type,COUNT(*) AS count FROM task_events WHERE project_hash=? AND occurred_at>=? "
            "GROUP BY event_type", (project_hash, week_start.isoformat()),
        ).fetchall()
        event_counts = {row["event_type"]: int(row["count"]) for row in event_rows}
        observation_rows = connection.execute(
            "SELECT outcome,COUNT(*) AS count FROM observations WHERE project_hash=? AND occurred_at>=? "
            "GROUP BY outcome", (project_hash, week_start.isoformat()),
        ).fetchall()
        observation_counts = {row["outcome"]: int(row["count"]) for row in observation_rows}
        unfinished = int(connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE project_hash=? AND status IN "
            "('active','blocked','needs_validation')", (project_hash,),
        ).fetchone()[0])
        outcomes = event_counts.get("task_completed", 0) + event_counts.get("validation_passed", 0) + observation_counts.get("passed", 0)
        attention = event_counts.get("task_blocked", 0) + event_counts.get("validation_failed", 0) + observation_counts.get("failed", 0)
        evidence_total = sum(event_counts.values()) + sum(observation_counts.values())
        candidates: list[dict[str, Any]] = []
        if evidence_total and current.weekday() == 4:
            candidates.append({
                "fingerprint": f"weekly:{week_start.date().isoformat()}", "kind": "weekly-progress",
                "severity": "warning" if attention else "information", "title": "Weekly project evidence is ready",
                "body": f"{outcomes} outcome/proof signal(s), {attention} attention signal(s), and {unfinished} unfinished task(s).",
                "evidence": [{"type": "count", "name": "outcomes", "value": outcomes},
                             {"type": "count", "name": "attention", "value": attention},
                             {"type": "count", "name": "unfinished", "value": unfinished}],
                "periodStart": week_start.isoformat(), "periodEnd": current.isoformat(),
            })
        recent_attention = int(connection.execute(
            "SELECT COUNT(*) FROM task_events WHERE project_hash=? AND occurred_at>=? AND "
            "event_type IN ('task_blocked','validation_failed')", (project_hash, day_start.isoformat()),
        ).fetchone()[0]) + int(connection.execute(
            "SELECT COUNT(*) FROM observations WHERE project_hash=? AND occurred_at>=? AND outcome='failed'",
            (project_hash, day_start.isoformat()),
        ).fetchone()[0])
        today_outcomes = int(connection.execute(
            "SELECT COUNT(*) FROM task_events WHERE project_hash=? AND occurred_at>=? AND "
            "event_type IN ('task_completed','validation_passed')", (project_hash, day_start.isoformat()),
        ).fetchone()[0]) + int(connection.execute(
            "SELECT COUNT(*) FROM observations WHERE project_hash=? AND occurred_at>=? AND outcome='passed'",
            (project_hash, day_start.isoformat()),
        ).fetchone()[0])
        today_evidence = int(connection.execute(
            "SELECT COUNT(*) FROM task_events WHERE project_hash=? AND occurred_at>=?",
            (project_hash, day_start.isoformat()),
        ).fetchone()[0]) + int(connection.execute(
            "SELECT COUNT(*) FROM observations WHERE project_hash=? AND occurred_at>=?",
            (project_hash, day_start.isoformat()),
        ).fetchone()[0])
        focus = connection.execute(
            "SELECT title FROM tasks WHERE project_hash=? AND status IN ('active','blocked','needs_validation') "
            "ORDER BY updated_at DESC LIMIT 1", (project_hash,),
        ).fetchone()
        if today_evidence:
            focus_text = f" on {str(focus['title'])[:120]}" if focus else " on this project"
            primary = "Start tomorrow by inspecting the oldest failed proof and choose one small correction." if recent_attention else "Tomorrow, choose one small outcome and attach proof when it is ready."
            reflection = f"You kept moving{focus_text} today: {today_outcomes} verified outcome(s) and {recent_attention} attention signal(s). Take it easy—{primary} Or pause and write down what is already stable."
            candidates.append({
                "fingerprint": f"reflection:{day_start.date().isoformat()}", "kind": "daily-reflection",
                "severity": "warning" if recent_attention else "information", "title": "Your project reflection is ready",
                "body": reflection[:500],
                "evidence": [{"type": "count", "name": "outcomes", "value": today_outcomes},
                             {"type": "count", "name": "attention", "value": recent_attention}],
                "periodStart": day_start.isoformat(), "periodEnd": current.isoformat(),
            })
        for item in candidates:
            identifier = _notification_id(project_hash, item["fingerprint"])
            connection.execute(
                "INSERT INTO notifications(id,project_hash,fingerprint,kind,severity,title,body,evidence_json,state,"
                "period_start,period_end,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'unread',?,?,?,?) "
                "ON CONFLICT(project_hash,fingerprint) DO UPDATE SET severity=excluded.severity,title=excluded.title,"
                "body=excluded.body,evidence_json=excluded.evidence_json,period_end=excluded.period_end,"
                "updated_at=excluded.updated_at WHERE notifications.state!='dismissed'",
                (identifier, project_hash, item["fingerprint"], item["kind"], item["severity"], item["title"],
                 item["body"], json.dumps(item["evidence"], separators=(",", ":")),
                 item["periodStart"], item["periodEnd"], current.isoformat(), current.isoformat()),
            )
    return list_notifications(project)


def list_notifications(project: Path, limit: int = 50) -> list[dict[str, Any]]:
    if not 1 <= limit <= 200:
        raise ValueError("notification limit must be between 1 and 200")
    with connect(project) as connection:
        rows = connection.execute(
            "SELECT id,kind,severity,title,body,evidence_json,state,period_start AS periodStart,"
            "period_end AS periodEnd,created_at AS createdAt,updated_at AS updatedAt,"
            "delivered_at AS deliveredAt FROM notifications WHERE project_hash=? "
            "ORDER BY updated_at DESC LIMIT ?", (root_hash(project), limit),
        ).fetchall()
    result = [dict(row) for row in rows]
    for item in result:
        item["evidence"] = json.loads(item.pop("evidence_json"))
    return result


def notification_counts(project: Path) -> dict[str, int]:
    """Return exact inbox state totals independently of the bounded detail list."""
    counts = {"all": 0, "unread": 0, "read": 0, "dismissed": 0}
    with connect(project) as connection:
        rows = connection.execute(
            "SELECT state,COUNT(*) AS count FROM notifications WHERE project_hash=? GROUP BY state",
            (root_hash(project),),
        ).fetchall()
    for row in rows:
        counts[str(row["state"])] = int(row["count"])
        counts["all"] += int(row["count"])
    return counts


def set_notification_state(project: Path, notification_id: str, state: str) -> dict[str, Any]:
    """Persist an explicit inbox state transition for one project notification."""
    if state not in {"unread", "read", "dismissed"}:
        raise ValueError("notification state must be unread, read, or dismissed")
    timestamp = datetime.now(UTC).isoformat()
    with connect(project) as connection:
        cursor = connection.execute(
            "UPDATE notifications SET state=?,updated_at=? WHERE id=? AND project_hash=?",
            (state, timestamp, notification_id, root_hash(project)),
        )
        if not cursor.rowcount:
            raise ValueError("notification not found")
        row = connection.execute(
            "SELECT id,kind,severity,title,body,evidence_json,state,period_start AS periodStart,"
            "period_end AS periodEnd,created_at AS createdAt,updated_at AS updatedAt,delivered_at AS deliveredAt "
            "FROM notifications WHERE id=? AND project_hash=?",
            (notification_id, root_hash(project)),
        ).fetchone()
    result = dict(row)
    result["evidence"] = json.loads(result.pop("evidence_json"))
    return result


def deliver_notifications(project: Path) -> int:
    """Deliver unread notifications through the native OS API without a shell."""
    settings = load_settings(project) or {}
    if not settings.get("notificationsEnabled") or platform.system() != "Darwin":
        return 0
    pending = [item for item in list_notifications(project) if item["deliveredAt"] is None and item["state"] == "unread"]
    delivered = 0
    script = "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run"
    for item in pending[:3]:
        result = subprocess.run(
            ["osascript", "-e", script, item["title"], item["body"]],
            capture_output=True, text=True, check=False, timeout=10,
        )
        if result.returncode:
            continue
        timestamp = datetime.now(UTC).isoformat()
        with connect(project) as connection:
            connection.execute(
                "UPDATE notifications SET delivered_at=?,updated_at=? WHERE id=? AND project_hash=?",
                (timestamp, timestamp, item["id"], root_hash(project)),
            )
        delivered += 1
    return delivered
