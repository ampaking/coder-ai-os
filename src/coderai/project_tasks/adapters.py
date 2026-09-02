"""Portable agent-to-Project-Tasks event adapter."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from coderai.project_tasks.storage import StorageError, new_id, record_event
from coderai.project_tasks.ingestion import normalize_agent_input
from coderai.project_tasks.matcher import match_task


LIFECYCLE_EVENTS = {
    "start": "task_started",
    "continue": "task_continued",
    "pause": "task_paused",
    "block": "task_blocked",
    "validate": "validation_passed",
    "fail": "validation_failed",
    "complete": "task_completed",
    "end": "session_ended",
}


def stable_id(prefix: str, *parts: str) -> str:
    material = "\0".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()[:20]}"


def record_lifecycle(project: Any, data: dict[str, Any]) -> dict[str, str]:
    """Translate a small, agent-neutral lifecycle record into the event schema."""
    action = str(data.get("action", "")).strip().lower()
    if action not in LIFECYCLE_EVENTS:
        raise StorageError(f"unsupported lifecycle action: {action}")
    title = str(data.get("title", "")).strip()
    summary = str(data.get("summary") or title).strip()
    if not title or not summary:
        raise StorageError("title and summary are required")
    agent = str(data.get("agent") or "unknown").strip()
    native_session = str(data.get("nativeSessionId") or "").strip()
    session_id = str(data.get("sessionId") or "").strip()
    if not session_id:
        session_id = stable_id("session", agent, native_session or summary)
    task_id = str(data.get("taskId") or "").strip()
    if not task_id:
        task_id = stable_id("task", title, str(data.get("theme") or ""))
    payload: dict[str, Any] = {
        "eventId": str(data.get("eventId") or new_id("event")),
        "type": LIFECYCLE_EVENTS[action],
        "summary": summary,
        "confidence": float(data.get("confidence", 1.0)),
        "task": {"id": task_id, "title": title, "theme": str(data.get("theme") or ""),
                 "estimatedMinutes": data.get("estimatedMinutes"), "actualMinutes": data.get("actualMinutes")},
        "session": {
            "id": session_id,
            "agentName": agent,
            "agentVersion": str(data.get("agentVersion") or ""),
            "modelName": str(data.get("model") or ""),
            "nativeSessionId": native_session,
            "resumeSupported": bool(data.get("resumeSupported")),
        },
    }
    if data.get("occurredAt"):
        payload["occurredAt"] = data["occurredAt"]
    if action in {"validate", "fail"}:
        payload["validation"] = {
            "id": stable_id("validation", payload["eventId"]),
            "category": str(data.get("validationCategory") or "test"),
            "commandSummary": str(data.get("proof") or ""),
            "durationMs": data.get("durationMs"),
        }
    if action == "block":
        payload["details"] = {"summary": summary}
    return record_event(project, payload)


def ingest_lifecycle(project: Any, value: Any, action: str) -> dict[str, Any]:
    normalized = normalize_agent_input(value, action)
    if not normalized["accepted"]:
        return normalized
    try:
        match = match_task(project, normalized["data"])
    except (StorageError, sqlite3.DatabaseError, OSError) as exc:
        return {"accepted": False, "questions": ["Project Tasks storage needs repair before recording."],
                "ignoredFields": normalized["ignoredFields"], "redactions": normalized["redactions"],
                "reason": type(exc).__name__}
    if match["taskId"]:
        normalized["data"]["taskId"] = match["taskId"]
    elif action != "start" and match["candidates"]:
        return {"accepted": False, "questions": ["Which active task should this continue?"],
                "candidates": match["candidates"], "ignoredFields": normalized["ignoredFields"],
                "redactions": normalized["redactions"]}
    elif action != "start":
        return {"accepted": False, "questions": ["What task ID should this lifecycle event update?"],
                "candidates": [], "ignoredFields": normalized["ignoredFields"],
                "redactions": normalized["redactions"]}
    try:
        result: dict[str, Any] = record_lifecycle(project, normalized["data"])
    except (StorageError, TypeError, ValueError) as exc:
        question = ("Project Tasks storage needs repair before recording."
                    if "repair" in str(exc).casefold()
                    else "Please provide a smaller structured task summary.")
        return {"accepted": False, "questions": [question],
                "ignoredFields": normalized["ignoredFields"], "redactions": normalized["redactions"],
                "reason": str(exc)}
    result.update({"accepted": True, "ignoredFields": normalized["ignoredFields"],
                   "redactions": normalized["redactions"], "questions": [],
                   "matchConfidence": match["confidence"]})
    return result
