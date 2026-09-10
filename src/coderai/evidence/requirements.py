"""What must be evidenced, derived from the diff — never from the claim.

The model does not get to decide what counts as proof for its own work. Changed
files decide: a UI file demands a visual run, source demands the project's own
test command, a schema demands its contract check.

Nothing here invents a command. If the repository does not document one, the
requirement says so rather than guessing (§22–23).
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

VISUAL = "visual"
TESTS = "tests"
CONTRACT = "contract"
UNKNOWN = "UNKNOWN — human confirmation required"

MAX_FILES = 2000


@dataclass
class Requirement:
    id: str
    kind: str
    why: str                       # the path that triggered it
    how: str                       # the command that would satisfy it
    paths: list[str] = field(default_factory=list)
    satisfied_by: list[str] = field(default_factory=list)
    blocking: bool = True

    @property
    def known(self) -> bool:
        return self.how != UNKNOWN


def changed_files(project: Path, base: str = "HEAD") -> list[str]:
    """Everything this working tree has touched, tracked or not."""
    project = Path(project)
    files: list[str] = []
    for argv in (["git", "-C", str(project), "diff", "--name-only", base],
                 ["git", "-C", str(project), "diff", "--name-only", "--cached", base],
                 ["git", "-C", str(project), "ls-files", "--others", "--exclude-standard"]):
        try:
            result = subprocess.run(argv, capture_output=True, text=True, check=False,
                                    timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            files.extend(line.strip() for line in result.stdout.splitlines() if line.strip())
    return sorted(dict.fromkeys(files))[:MAX_FILES]


def _val_configs(project: Path) -> list[Path]:
    root = Path(project) / ".coder-ai" / "val"
    configs = [root / "config.json", *sorted((root / "apps").glob("*/config.json"))]
    return [path for path in configs if path.is_file() and not path.is_symlink()]


def _expand(pattern: str) -> list[str]:
    start, end = pattern.find("{"), pattern.find("}")
    expanded = [pattern] if start < 0 or end < start else [
        pattern[:start] + item + pattern[end + 1:] for item in pattern[start + 1:end].split(",")
    ]
    return expanded + [item.replace("/**/", "/") for item in expanded if "/**/" in item]


def ui_paths(project: Path, changed: list[str]) -> dict[str, list[str]]:
    """Changed files that this project's own watchGlobs call user interface."""
    matched: dict[str, list[str]] = {}
    for config in _val_configs(project):
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        globs = [str(item) for item in (data.get("watchGlobs") or [])]
        app = config.parent.name if config.parent.name != "val" else "default"
        hits = [path for path in changed
                if any(fnmatch.fnmatchcase(path, expanded)
                       for pattern in globs for expanded in _expand(pattern))]
        if hits:
            matched[app] = hits
    return matched


def _test_command(project: Path) -> str:
    """The project's own test command, discovered — never invented."""
    try:
        from coderai.orchestrator import discover_validation_commands

        for command in discover_validation_commands(Path(project)):
            if any(word in command for word in ("test", "pytest")):
                return " ".join(command)
    except Exception:
        pass
    return UNKNOWN


_CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb", ".java", ".kt",
                  ".php", ".cs", ".swift", ".mjs", ".cjs"}
_SCHEMA_HINTS = ("migration", "migrations", "schema", "alembic")


def require(project: Path, changed: list[str] | None = None) -> list[Requirement]:
    """The checks this change must be able to show evidence for."""
    project = Path(project)
    changed = changed if changed is not None else changed_files(project)
    requirements: list[Requirement] = []

    for app, hits in ui_paths(project, changed).items():
        wrapper = ".coder-ai/val/run"
        command = (f"{wrapper} run --task <task>" if app == "default"
                   else f"{wrapper} --app {app} run --task <task>")
        requirements.append(Requirement(
            id=f"visual:{app}", kind=VISUAL,
            why=f"{hits[0]} changed" + (f" (+{len(hits) - 1} more)" if len(hits) > 1 else ""),
            how=command, paths=hits,
            satisfied_by=["val/run", "val run", "coder-ai val", "coder-ai-os val"],
        ))

    code = [path for path in changed if Path(path).suffix in _CODE_SUFFIXES]
    if code:
        command = _test_command(project)
        requirements.append(Requirement(
            id="tests", kind=TESTS,
            why=f"{len(code)} source file(s) changed",
            how=command, paths=code,
            satisfied_by=[command] if command != UNKNOWN else [],
            blocking=command != UNKNOWN,
        ))

    schema = [path for path in changed
              if any(hint in path.lower() for hint in _SCHEMA_HINTS)]
    if schema:
        requirements.append(Requirement(
            id="contract", kind=CONTRACT,
            why=f"{schema[0]} changed",
            how=UNKNOWN, paths=schema, blocking=False,
        ))
    return requirements
