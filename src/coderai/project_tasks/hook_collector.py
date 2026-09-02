"""Content-free adapter for supported agent lifecycle hooks."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coderai.project_tasks.project import load_settings, root_hash
from coderai.project_tasks.storage import cleanup, connect

MAX_HOOK_BYTES = 131_072
SUPPORTED_EVENTS = {"PostToolUse", "Stop"}
SUPPORTED_TOOLS = {"Bash", "Edit", "MultiEdit", "Write"}


def _identifier(*parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:32]
    return digest


def capture_hook(project: Path, provider: str, raw: bytes) -> dict[str, Any]:
    settings = load_settings(project)
    if not settings or not settings.get("enabled") or not settings.get("automaticCollection"):
        return {"status": "disabled", "captured": False}
    if provider != "claude":
        return {"status": "unsupported-provider", "captured": False}
    if len(raw) > MAX_HOOK_BYTES:
        return {"status": "rejected", "captured": False, "reason": "input-too-large"}
    try:
        payload = json.loads(raw or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "rejected", "captured": False, "reason": "invalid-json"}
    if not isinstance(payload, dict):
        return {"status": "rejected", "captured": False, "reason": "invalid-shape"}
    event = str(payload.get("hook_event_name") or "")[:40]
    session = str(payload.get("session_id") or "")[:500]
    tool = str(payload.get("tool_name") or "")[:40]
    event_key = str(payload.get("tool_use_id") or session)[:500]
    if event not in SUPPORTED_EVENTS or not session or not event_key:
        return {"status": "ignored", "captured": False}
    if event == "PostToolUse" and tool not in SUPPORTED_TOOLS:
        return {"status": "ignored", "captured": False}
    kind = "agent-session" if event == "Stop" else "agent-tool-use"
    summary = "AI coding session observed" if event == "Stop" else "AI coding activity observed"
    timestamp = datetime.now(UTC).isoformat()
    identity = _identifier(provider, event, session, event_key)
    observation_id = f"observation_hook_{identity}"
    receipt_id = f"receipt_hook_{identity}"
    with connect(project) as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO observations("
            "id,project_hash,source,kind,scope,outcome,summary,confidence,occurred_at,collected_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (observation_id, root_hash(project), "agent-hook", kind, "project", "information",
             summary, 1.0, timestamp, timestamp),
        )
        duplicate = not bool(cursor.rowcount)
        connection.execute(
            "INSERT OR IGNORE INTO collector_receipts("
            "id,project_hash,collector,trigger_kind,status,observed_count,accepted_count,"
            "duplicate_count,rejected_count,error_code,contract_version,started_at,finished_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (receipt_id, root_hash(project), f"{provider}-lifecycle", "agent-hook", "success", 1,
             0 if duplicate else 1, 1 if duplicate else 0, 0, "", 1, timestamp, timestamp),
        )
    # A Claude Stop event means only that one response ended. It is not evidence that a
    # repository task started, proposed completion, or still needs validation. Keep this
    # adapter observational; explicit task/validation events own lifecycle transitions.
    result = {"status": "duplicate" if duplicate else "captured", "captured": not duplicate}
    if event == "Stop":
        cleanup(project)
    return result


def capture_validation_result(
    project: Path, collector: str, run_id: str, outcome: str,
) -> dict[str, Any]:
    settings = load_settings(project)
    if not settings or not settings.get("enabled") or not settings.get("automaticCollection"):
        return {"status": "disabled", "captured": False}
    if collector not in {"val"} or outcome not in {"passed", "failed", "warning"}:
        return {"status": "rejected", "captured": False}
    run_key = str(run_id)[:240]
    if not run_key:
        return {"status": "rejected", "captured": False}
    timestamp = datetime.now(UTC).isoformat()
    identity = _identifier(collector, run_key)
    observation_id = f"observation_validation_{identity}"
    receipt_id = f"receipt_validation_{identity}"
    summary = {"passed": "Automated validation passed", "failed": "Automated validation needs attention",
               "warning": "Automated validation was inconclusive"}[outcome]
    with connect(project) as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO observations("
            "id,project_hash,source,kind,scope,outcome,summary,confidence,occurred_at,collected_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (observation_id, root_hash(project), collector, "validation-run", "project", outcome,
             summary, 1.0, timestamp, timestamp),
        )
        duplicate = not bool(cursor.rowcount)
        connection.execute(
            "INSERT OR IGNORE INTO collector_receipts("
            "id,project_hash,collector,trigger_kind,status,observed_count,accepted_count,"
            "duplicate_count,rejected_count,error_code,contract_version,started_at,finished_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (receipt_id, root_hash(project), collector, "validation-hook", "success", 1,
             0 if duplicate else 1, 1 if duplicate else 0, 0, "", 1, timestamp, timestamp),
        )
    return {"status": "duplicate" if duplicate else "captured", "captured": not duplicate}
