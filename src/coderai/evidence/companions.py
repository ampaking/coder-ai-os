"""What else this work implies — answered by the repository, not by a checklist.

"We created the API, so we need the UI — where and how?" The project already
contains the answer: find the feature most like this one and see what it is made
of. If invoices has a card, a test and ja/en strings, then subscriptions needs
them too — and the file that justifies each expectation is named, so a wrong
analogy can be seen and dismissed rather than obeyed.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILES = 6000
MIN_SCORE = 0.6           # below this the analogy is not worth asserting at all
STRONG = 0.75
MAX_COMPANIONS = 8
MAX_FAMILY = 8            # a larger "family" is a subsystem, not a feature
GENERIC = {"core", "util", "utils", "lib", "common", "base", "main", "shared", "helper",
           "evidence", "delivery", "runner", "client", "server", "config", "setup"}

_TOKEN = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|\d+")
_SKIP_DIRS = ("node_modules", ".git", "dist", "build", "vendor", "__pycache__",
              ".venv", "target")


@dataclass
class Companion:
    kind: str                  # what sort of file is missing, in this project's terms
    expected: str              # where it would go, by this project's own convention
    analogue: str              # the existing file that justifies the expectation
    source: str                # the new file that triggered it
    confidence: float = 0.0

    @property
    def strong(self) -> bool:
        return self.confidence >= STRONG

    def describe(self) -> str:
        strength = "" if self.strong else "  (weak analogy)"
        return f"{self.expected}  — because {self.analogue} exists{strength}"


def tokens(text: str) -> list[str]:
    return [item.lower() for item in _TOKEN.findall(text)]


def _root(name: str) -> str:
    """The identifying word of a file: 'InvoiceCard.tsx' -> 'invoice'."""
    parts = tokens(Path(name).stem)
    parts = [item for item in parts
             if item not in {"test", "tests", "spec", "index", "main", "card", "page",
                             "view", "screen", "component", "service", "repository", "en", "ja"}]
    if not parts:
        parts = tokens(Path(name).stem)
    word = parts[0] if parts else Path(name).stem.lower()
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is", "os", "as")):
        return word[:-1]
    return word


def repository_files(project: Path) -> list[str]:
    try:
        result = subprocess.run(["git", "-C", str(project), "ls-files"],
                                capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines()
            if line and not any(part in line.split("/") for part in _SKIP_DIRS)][:MAX_FILES]


def _score(new_path: str, candidate: str) -> float:
    """How alike are these two files, structurally and by name?"""
    if new_path == candidate:
        return 0.0
    new, other = Path(new_path), Path(candidate)
    score = 0.0
    if new.parent == other.parent:
        score += 0.5
    elif new.parts[:1] == other.parts[:1]:
        score += 0.2
    if new.suffix == other.suffix:
        score += 0.3
    left, right = set(tokens(new.stem)), set(tokens(other.stem))
    if left and right:
        score += 0.2 * (len(left & right) / len(left | right))
    return score


def nearest_analogue(new_path: str, existing: list[str]) -> tuple[str, float]:
    """The existing feature file most like this new one."""
    best, best_score = "", 0.0
    for candidate in existing:
        value = _score(new_path, candidate)
        if value > best_score:
            best, best_score = candidate, value
    return best, best_score


def _family(analogue: str, existing: list[str]) -> list[str]:
    """Every file that belongs to the analogue's feature, by its identifying word."""
    root = _root(analogue)
    if len(root) < 3:
        return []
    return [path for path in existing if root in "".join(tokens(path))]


def _counterpart(path: str, old_root: str, new_root: str) -> str:
    """Where the equivalent file would live, keeping this project's naming style."""
    def replace(match: re.Match) -> str:
        word = match.group(0)
        if word.isupper():
            return new_root.upper()
        if word[:1].isupper():
            return new_root.capitalize()
        return new_root

    pattern = re.compile(re.escape(old_root) + "(s?)", re.IGNORECASE)
    return pattern.sub(lambda m: replace(m) + m.group(1), path)


def _kind(path: str) -> str:
    lowered = path.lower()
    segments = set(lowered.split("/"))
    if segments & {"locales", "locale", "i18n", "lang", "translations", "messages"}:
        return "translations"
    if "test" in lowered or "spec" in lowered:
        return "test"
    if Path(path).suffix in {".tsx", ".jsx", ".vue", ".svelte"}:
        return "UI surface"
    if "migration" in lowered or "alembic" in lowered:
        return "migration"
    if Path(path).suffix in {".md", ".rst"}:
        return "documentation"
    return "module"


MAX_ADDED = 20            # beyond this the change is a subsystem, not a feature
_INFRA = ("bin", "scripts", "config", "build", "docs", ".github", "fixtures", ".ai")


def _is_feature_file(path: str) -> bool:
    parts = set(Path(path).parts)
    return not (parts & set(_INFRA))


def infer(project: Path, added: list[str], *, existing: list[str] | None = None
          ) -> list[Companion]:
    """Companion work implied by this project's own conventions. Empty when unsure."""
    project = Path(project)
    files = existing if existing is not None else repository_files(project)
    added = [path for path in added if _is_feature_file(path)]
    if not files or not added or len(added) > MAX_ADDED:
        return []          # nothing to reason from, or too much to reason about
    present = set(files)
    planned = set(added)
    companions: list[Companion] = []
    seen: set[str] = set()

    for new_path in added:
        analogue, score = nearest_analogue(new_path, [f for f in files if f not in planned])
        if not analogue or score < MIN_SCORE:
            continue                      # no credible analogy: say nothing
        # A feature lives beside its analogue. Inferring across top-level trees
        # produced confident nonsense — "evidence/ needs a Dockerfile because val/
        # has one" — so the analogy must stay inside one tree.
        if Path(new_path).parts[:1] != Path(analogue).parts[:1]:
            continue
        old_root, new_root = _root(analogue), _root(new_path)
        if old_root == new_root or len(new_root) < 3:
            continue
        if new_root in GENERIC or old_root in GENERIC:
            continue                      # a generic name is not a feature name
        family = _family(analogue, files)
        if len(family) > MAX_FAMILY:
            continue                      # this is a subsystem; it implies nothing
        for relative in family:
            if relative == analogue or Path(relative).parent == Path(new_path).parent:
                continue
            expected = _counterpart(relative, old_root, new_root)
            if expected in present or expected in planned or expected in seen:
                continue
            seen.add(expected)
            companions.append(Companion(
                kind=_kind(relative), expected=expected, analogue=relative,
                source=new_path, confidence=round(score, 2),
            ))
    companions.sort(key=lambda item: (-item.confidence, item.expected))
    return companions[:MAX_COMPANIONS]
