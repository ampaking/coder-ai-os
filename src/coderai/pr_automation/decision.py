"""The structured result an awake AI returns (§38–45).

Eight states, validated the way `orchestrator._validate_agent_result` validates:
untrusted output, bounded fields, typed failure. A malformed decision is an
error — never silently downgraded to NO_ACTION, which would look like "nothing
to do" when the truth is "we don't know".
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

NO_ACTION = "NO_ACTION"
WAIT = "WAIT"
REPLY_NEEDED = "REPLY_NEEDED"
VERIFY_NEEDED = "VERIFY_NEEDED"
FIX_NEEDED = "FIX_NEEDED"
REVIEW_NEEDED = "REVIEW_NEEDED"
HUMAN_NEEDED = "HUMAN_NEEDED"
DONE = "DONE"

STATES = (NO_ACTION, WAIT, REPLY_NEEDED, VERIFY_NEEDED, FIX_NEEDED, REVIEW_NEEDED,
          HUMAN_NEEDED, DONE)

# States that mean the AI changed the repository during this run.
MUTATING = {FIX_NEEDED, DONE}
# States that stop mutation and hand back to a person.
ESCALATING = {HUMAN_NEEDED}

MAX_SUMMARY = 4000
MAX_ITEMS = 100
MAX_ITEM = 1000

DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "state": {"type": "string", "enum": list(STATES)},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "state": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["id", "state"],
                "additionalProperties": False,
            },
        },
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "commits": {"type": "array", "items": {"type": "string"}},
        "pushed_sha": {"type": "string"},
        "replies": {"type": "array", "items": {"type": "string"}},
        "validation": {"type": "array", "items": {"type": "string"}},
        "human_reason": {"type": "string"},
        "remaining": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["state", "summary"],
    "additionalProperties": False,
}


class DecisionError(RuntimeError):
    """The AI returned something that is not a usable decision."""


@dataclass
class Decision:
    state: str
    summary: str
    findings: list[dict[str, str]] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    pushed_sha: str = ""
    replies: list[str] = field(default_factory=list)
    validation: list[str] = field(default_factory=list)
    human_reason: str = ""
    remaining: list[str] = field(default_factory=list)

    @property
    def mutated(self) -> bool:
        return bool(self.commits or self.pushed_sha or self.changed_files)

    @property
    def needs_human(self) -> bool:
        return self.state in ESCALATING

    @property
    def sleeps(self) -> bool:
        """Every decision ends with the AI exiting — this names the quiet ones."""
        return self.state in {NO_ACTION, WAIT, DONE}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _strings(value: Any, limit: int = MAX_ITEMS) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:MAX_ITEM] for item in value[:limit]]


def validate(value: Any) -> Decision:
    """Turn untrusted provider output into a Decision, or raise."""
    if not isinstance(value, dict):
        raise DecisionError("decision is not a JSON object")
    state = value.get("state")
    if not isinstance(state, str) or state.strip().upper() not in STATES:
        raise DecisionError(
            f"decision state must be one of {', '.join(STATES)}; got {state!r}")
    summary = value.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise DecisionError("decision has no summary")

    findings: list[dict[str, str]] = []
    for item in (value.get("findings") or [])[:MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("id") or "").strip()
        finding_state = str(item.get("state") or "").strip().upper()
        if not identifier or not finding_state:
            continue
        findings.append({"id": identifier[:200], "state": finding_state[:40],
                         "note": str(item.get("note") or "")[:MAX_ITEM]})

    return Decision(
        state=state.strip().upper(),
        summary=summary[:MAX_SUMMARY],
        findings=findings,
        changed_files=_strings(value.get("changed_files")),
        commits=_strings(value.get("commits")),
        pushed_sha=str(value.get("pushed_sha") or "")[:64],
        replies=_strings(value.get("replies")),
        validation=_strings(value.get("validation")),
        human_reason=str(value.get("human_reason") or "")[:MAX_ITEM],
        remaining=_strings(value.get("remaining")),
    )


def parse_provider_output(provider: str, stdout: str, result_path: Any = None) -> Decision:
    """Read a decision from whichever shape the provider produced."""
    raw = ""
    if result_path is not None:
        try:
            if result_path.is_file():
                raw = result_path.read_text(encoding="utf-8")
        except OSError:
            raw = ""
    if not raw:
        raw = stdout or ""
    if not raw.strip():
        raise DecisionError(f"{provider} returned no output")

    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        value = _last_json_object(raw)
        if value is None:
            raise DecisionError(f"{provider} output is not JSON: {exc}") from exc

    if isinstance(value, dict) and provider == "claude":
        for key in ("structured_output", "result"):
            if key in value:
                inner = value[key]
                if isinstance(inner, str):
                    try:
                        inner = json.loads(inner)
                    except json.JSONDecodeError:
                        continue
                if isinstance(inner, dict):
                    value = inner
                    break
    return validate(value)


def _last_json_object(text: str) -> Any:
    """Recover a decision an agent wrapped in prose, without guessing at content."""
    depth = 0
    end = -1
    for index in range(len(text) - 1, -1, -1):
        char = text[index]
        if char == "}":
            if depth == 0:
                end = index
            depth += 1
        elif char == "{":
            depth -= 1
            if depth == 0 and end != -1:
                try:
                    return json.loads(text[index:end + 1])
                except json.JSONDecodeError:
                    end = -1
                    depth = 0
    return None
