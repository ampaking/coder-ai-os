"""Temporary, privacy-safe demonstration data for the on-demand dashboard."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from coderai.project_tasks import SCHEMA_VERSION
from coderai.project_tasks.project import root_hash, write_settings
from coderai.project_tasks.storage import connect, record_event


def _event(project: Path, task_id: str, title: str, theme: str, kind: str,
           at: datetime, agent: str, summary: str) -> None:
    payload: dict[str, object] = {
        "eventId": f"{task_id}-{kind}-{int(at.timestamp())}",
        "type": kind,
        "summary": summary,
        "occurredAt": at.isoformat(),
        "task": {"id": task_id, "title": title, "theme": theme,
                 "estimatedMinutes": 120 if kind == "task_started" else None},
        "session": {"id": f"session-{agent}-{task_id}", "agentName": agent,
                    "modelName": "demo", "resumeSupported": True},
    }
    if kind.startswith("validation_"):
        payload["validation"] = {
            "id": f"proof-{task_id}-{int(at.timestamp())}",
            "category": "test",
            "commandSummary": "focused checks",
        }
    if kind == "task_blocked":
        payload["details"] = {"summary": summary}
    record_event(project, payload)


def seed_demo(project: Path) -> None:
    """Create a rich disposable project; callers must supply an isolated directory."""
    (project / ".coder-ai").mkdir(parents=True)
    (project / ".coder-ai" / "identity.json").write_text(json.dumps({
        "project_id": "atlas-demo",
        "git_root_hash": root_hash(project),
        "remote_hash": "sha256:demo",
    }), encoding="utf-8")
    (project / ".coder-ai" / "project.yaml").write_text(
        "project:\n  id: atlas-demo\n", encoding="utf-8"
    )
    (project / "README.md").write_text(
        "# Atlas Demo\n\nA multilingual engineering intelligence workspace.\n",
        encoding="utf-8",
    )
    write_settings(project, {
        "enabled": True, "schemaVersion": SCHEMA_VERSION, "projectHash": root_hash(project),
        "theme": "black", "language": "auto", "timeZone": "UTC",
        "retentionDays": 3650, "gitMetadata": False,
    })
    now = datetime.now(UTC)
    tasks = [
        ("onboarding", "Simplify multilingual onboarding", "Developer Experience", "codex", 320, "completed"),
        ("auth", "Harden local session access", "Security", "claude-code", 180, "completed"),
        ("capture", "Capture structured task lifecycle", "Intelligence", "codex", 95, "completed"),
        ("graph", "Build human-readable project journey", "Visualization", "codex", 18, "active"),
        ("git", "Review optional Git evidence", "Evidence", "claude-code", 12, "blocked"),
        ("weekly", "Explain weekly engineering outcomes", "Review", "gemini", 5, "completed"),
        ("mobile", "Verify mobile interaction targets", "UI", "codex", 2, "completed"),
        ("export", "Create privacy-safe export", "Privacy", "claude-code", 1, "completed"),
    ]
    for task_id, title, theme, agent, age_days, status in tasks:
        _event(project, task_id, title, theme, "task_started",
               now - timedelta(days=age_days, hours=3), agent, f"Started: {title}")
        if status == "blocked":
            _event(project, task_id, title, theme, "task_blocked",
                   now - timedelta(days=age_days), agent, "Waiting for explicit human confirmation")
        elif status == "completed":
            _event(project, task_id, title, theme, "validation_passed",
                   now - timedelta(days=age_days, hours=1), agent, "Focused validation passed")
            _event(project, task_id, title, theme, "task_completed",
                   now - timedelta(days=age_days), agent, f"Delivered: {title}")
    with connect(project) as connection:
        connection.execute(
            "INSERT INTO task_links(task_id,related_task_id,link_type,created_at) VALUES(?,?,?,?)",
            ("graph", "capture", "depends_on", now.isoformat()),
        )
        connection.execute(
            "INSERT INTO task_links(task_id,related_task_id,link_type,created_at) VALUES(?,?,?,?)",
            ("weekly", "graph", "related_to", now.isoformat()),
        )
