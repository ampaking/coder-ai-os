"""What 'done' means for this task — written at the start, audited at the end.

The agent proposes the list. It cannot quietly shrink it: the item set is
fingerprinted when first seen, and anything removed or materially reworded since
is reported. Adding items is free — discovering more work is honest.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

FILE = "acceptance.md"
STATE = "acceptance.json"
MIN_WORDS = 3
VAGUE = ("looks good", "works well", "improve", "better", "nice", "clean up", "polish")

_ITEM = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s+(.+?)\s*$")
_WHITESPACE = re.compile(r"\s+")


@dataclass
class Item:
    id: str
    text: str
    checked: bool = False
    vague: bool = False

    def normalized(self) -> str:
        return _WHITESPACE.sub(" ", self.text).strip().lower()


@dataclass
class Acceptance:
    items: list[Item] = field(default_factory=list)
    path: str = ""
    started_at: float = 0.0
    retrospective: bool = False

    def __len__(self) -> int:
        return len(self.items)

    def fingerprint(self) -> str:
        blob = "\n".join(sorted(item.normalized() for item in self.items))
        return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:16]


def item_id(text: str) -> str:
    return hashlib.sha256(_WHITESPACE.sub(" ", text).strip().lower().encode()).hexdigest()[:8]


def parse(text: str) -> list[Item]:
    items: list[Item] = []
    for line in text.splitlines():
        match = _ITEM.match(line)
        if not match:
            continue
        body = match.group(2).strip()
        if not body:
            continue
        lowered = body.lower()
        items.append(Item(
            id=item_id(body), text=body[:300], checked=match.group(1).lower() == "x",
            vague=len(body.split()) < MIN_WORDS or any(word in lowered for word in VAGUE),
        ))
    return items


def path_for(project: Path, task: str) -> Path:
    return Path(project) / ".ai" / task / FILE


def load(project: Path, task: str) -> Acceptance | None:
    path = path_for(project, task)
    if not path.is_file():
        return None
    return Acceptance(items=parse(path.read_text(encoding="utf-8", errors="replace")),
                      path=str(path.relative_to(Path(project))))


def _state_path(project: Path, task: str) -> Path:
    from coderai.evidence.ledger import directory

    return directory(Path(project)) / f"{task}.{STATE}"


def record_start(project: Path, task: str, acceptance: Acceptance, *,
                 first_edit_at: float = 0.0, now: float | None = None) -> None:
    """Fingerprint the list as first seen, so shrinkage is detectable later."""
    moment = time.time() if now is None else now
    state = {
        "fingerprint": acceptance.fingerprint(),
        "items": [asdict(item) for item in acceptance.items],
        "at": moment,
        "retrospective": bool(first_edit_at and moment > first_edit_at),
    }
    target = _state_path(project, task)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def started(project: Path, task: str) -> dict | None:
    path = _state_path(project, task)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


@dataclass
class Drift:
    removed: list[str] = field(default_factory=list)
    reworded: list[tuple[str, str]] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    retrospective: bool = False

    @property
    def shrank(self) -> bool:
        return bool(self.removed or self.reworded)


def _similar(left: str, right: str) -> float:
    a, b = set(left.split()), set(right.split())
    return len(a & b) / max(1, len(a | b))


def drift_since_start(project: Path, task: str, current: Acceptance) -> Drift:
    """What changed between the list written at the start and the list now."""
    state = started(project, task)
    if state is None:
        return Drift()
    before = {item["id"]: item["text"] for item in state.get("items", [])}
    now = {item.id: item.text for item in current.items}
    drift = Drift(retrospective=bool(state.get("retrospective")))

    for identifier, text in before.items():
        if identifier in now:
            continue
        # A typo fix keeps most of its words; a rewrite does not.
        match = next((candidate for candidate in current.items
                      if _similar(_WHITESPACE.sub(" ", text).lower(),
                                  candidate.normalized()) >= 0.6), None)
        if match is None:
            drift.removed.append(text)
        elif _similar(_WHITESPACE.sub(" ", text).lower(), match.normalized()) < 0.9:
            drift.reworded.append((text, match.text))
    drift.added = [text for identifier, text in now.items() if identifier not in before]
    return drift
