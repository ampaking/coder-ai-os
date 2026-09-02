"""Small, deterministic request shaping helpers."""

from __future__ import annotations

import re
from typing import Any


def shape_request(text: str) -> dict[str, Any]:
    request = " ".join(text.strip().split())
    if not request:
        raise ValueError("request text is required")
    if len(request) > 2000:
        raise ValueError("request text exceeds 2000 characters")
    lower = request.lower()
    mixed_markers = len(re.findall(r"\b(?:and|also|plus|while)\b", lower))
    has_proof = any(word in lower for word in ("test", "verify", "validate", "proof", "pass"))
    has_boundary = any(phrase in lower for phrase in ("do not", "don't", "without changing", "only"))
    suggestions: list[str] = []
    if mixed_markers >= 2:
        suggestions.append("This may contain several outcomes; split independently testable work.")
    if not has_boundary:
        suggestions.append("Name what must not change or be included.")
    if not has_proof:
        suggestions.append("Add the command or observation that proves completion.")
    return {
        "goal": request,
        "context": "Describe the current behavior and the evidence already observed.",
        "expected": ["State the required behavior.", "Name important edge cases."],
        "boundary": "State what must not change in this task.",
        "proof": "Name the validation command or observable result.",
        "suggestions": suggestions,
    }
