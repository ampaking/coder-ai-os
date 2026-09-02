"""Tolerant, content-safe normalization for untrusted agent lifecycle input."""

from __future__ import annotations

import re
from typing import Any

KNOWN_FIELDS = {
    "action", "actualMinutes", "agent", "agentVersion", "confidence", "durationMs",
    "estimatedMinutes", "eventId", "model", "nativeSessionId", "proof", "resumeSupported",
    "sessionId", "summary", "taskId", "theme", "title", "validationCategory", "occurredAt",
}

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|password|authorization)\s*[:=]\s*[^\s,;]{4,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


def redact_text(value: Any, maximum: int) -> tuple[str, list[str]]:
    text = " ".join(str(value or "").split())
    findings: list[str] = []
    for index, pattern in enumerate(SECRET_PATTERNS, start=1):
        text, count = pattern.subn("[redacted]", text)
        if count:
            findings.append(f"credential-pattern-{index}")
    if len(text) > maximum:
        text = text[:maximum].rstrip() + "…"
        findings.append("truncated")
    return text, findings


def normalize_agent_input(value: Any, action: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"accepted": False, "questions": ["What task should be recorded?"],
                "ignoredFields": [], "redactions": []}
    known = {key: value[key] for key in KNOWN_FIELDS if key in value}
    if action:
        known["action"] = action
    ignored = sorted(str(key) for key in value if key not in KNOWN_FIELDS)[:40]
    redactions: list[str] = []
    for field, maximum in (("title", 240), ("summary", 1200), ("theme", 120), ("proof", 500)):
        if field in known:
            known[field], found = redact_text(known[field], maximum)
            redactions.extend(f"{field}:{item}" for item in found)
    title = str(known.get("title") or "").strip()
    summary = str(known.get("summary") or title).strip()
    questions: list[str] = []
    if not title:
        questions.append("What short task title should be used?")
    if not summary:
        questions.append("What outcome or change should this task record?")
    known["summary"] = summary
    return {"accepted": not questions, "data": known, "questions": questions,
            "ignoredFields": ignored, "redactions": redactions}
