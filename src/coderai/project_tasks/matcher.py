"""Conservative task matching for lifecycle events without an explicit task ID."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from coderai.project_tasks.project import root_hash
from coderai.project_tasks.storage import connect

ACTIVE = ("active", "paused", "blocked", "needs_validation")


def _words(value: str) -> set[str]:
    return {word for word in re.findall(r"[\w-]+", value.casefold()) if len(word) > 1}


def _similarity(left: str, right: str) -> float:
    a, b = _words(left), _words(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def match_task(project: Path, data: dict[str, Any]) -> dict[str, Any]:
    if str(data.get("taskId") or "").strip():
        return {"taskId": str(data["taskId"]).strip(), "confidence": 1.0, "candidates": []}
    if str(data.get("action") or "").strip().lower() == "start":
        return {"taskId": None, "confidence": 1.0, "candidates": []}
    title = str(data.get("title") or "").strip()
    theme = str(data.get("theme") or "").strip().casefold()
    native = str(data.get("nativeSessionId") or "").strip()
    agent = str(data.get("agent") or "unknown").strip()
    with connect(project) as connection:
        if native:
            rows = connection.execute(
                "SELECT DISTINCT tasks.id,tasks.title,tasks.status FROM sessions "
                "JOIN task_events ON task_events.session_id=sessions.id "
                "JOIN tasks ON tasks.id=task_events.task_id WHERE sessions.project_hash=? "
                "AND sessions.agent_name=? AND sessions.native_session_id=? "
                "AND tasks.status IN ('active','paused','blocked','needs_validation')",
                (root_hash(project), agent, native),
            ).fetchall()
            if len(rows) == 1:
                return {"taskId": rows[0]["id"], "confidence": 0.98, "candidates": [dict(rows[0])]}
        rows = connection.execute(
            "SELECT id,title,theme,status FROM tasks WHERE project_hash=? "
            "AND status IN ('active','paused','blocked','needs_validation') ORDER BY updated_at DESC LIMIT 30",
            (root_hash(project),),
        ).fetchall()
    scored = []
    for row in rows:
        score = _similarity(title, str(row["title"]))
        if theme and theme == str(row["theme"]).casefold():
            score = min(1.0, score + 0.12)
        scored.append({**dict(row), "confidence": round(score, 3)})
    scored.sort(key=lambda item: item["confidence"], reverse=True)
    best = scored[0] if scored else None
    runner_up = scored[1]["confidence"] if len(scored) > 1 else 0.0
    if best and best["confidence"] >= 0.82 and best["confidence"] - runner_up >= 0.15:
        return {"taskId": best["id"], "confidence": best["confidence"], "candidates": scored[:3]}
    return {"taskId": None, "confidence": best["confidence"] if best else 0.0, "candidates": scored[:3]}
