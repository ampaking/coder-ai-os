"""Can UI changes actually be verified in this project?

The visual loop failed in the worst possible way: installed, hooked, and
unreachable. Every link has to be checked, because any one of them breaking means
UI ships unlooked-at and nothing says so.

    config → watchGlobs match real files → wrapper → edit hook → permission
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

OK = "ok"
BROKEN = "broken"

MARKER = "ui-validation.pending"
WRAPPER = ".coder-ai/val/run"
HOOK = "val-post-edit.py"
EVIDENCE_HOOK = "evidence-post-tool.py"


@dataclass
class Link:
    name: str
    status: str
    detail: str = ""
    fix: str = ""

    @property
    def ok(self) -> bool:
        return self.status == OK


@dataclass
class Chain:
    links: list[Link] = field(default_factory=list)
    configured: bool = False

    @property
    def broken(self) -> list[Link]:
        return [link for link in self.links if not link.ok]

    @property
    def works(self) -> bool:
        return self.configured and not self.broken

    def render(self) -> str:
        if not self.configured:
            return ("  visual loop   not configured here — UI changes cannot be verified\n"
                    "                run: coder-ai setup\n")
        out = []
        for link in self.links:
            out.append(f"  {'●' if link.ok else '✗'} {link.name:<16} {link.detail}")
            if link.fix and not link.ok:
                out.append(f"    → {link.fix}")
        out.append("  the visual loop will run here" if self.works else
                   "  UI changes will NOT be verified here until the above is fixed")
        return "\n".join(out) + "\n"


def _configs(project: Path) -> list[Path]:
    root = Path(project) / ".coder-ai" / "val"
    return [path for path in [root / "config.json", *sorted((root / "apps").glob("*/config.json"))]
            if path.is_file() and not path.is_symlink()]


def _expand(pattern: str) -> list[str]:
    start, end = pattern.find("{"), pattern.find("}")
    expanded = [pattern] if start < 0 or end < start else [
        pattern[:start] + item + pattern[end + 1:] for item in pattern[start + 1:end].split(",")
    ]
    return expanded + [item.replace("/**/", "/") for item in expanded if "/**/" in item]


def _tracked(project: Path) -> list[str]:
    """Files in the repository — including new ones.

    Tracked-only would call the chain broken in a project whose interface is all
    newly added, which is exactly when the loop matters most.
    """
    found: list[str] = []
    for argv in (["git", "-C", str(project), "ls-files"],
                 ["git", "-C", str(project), "ls-files", "--others", "--exclude-standard"]):
        try:
            result = subprocess.run(argv, capture_output=True, text=True, check=False,
                                    timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            found.extend(result.stdout.splitlines())
    return sorted(dict.fromkeys(found))


def _read_settings(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _hook_installed(settings: dict, needle: str) -> bool:
    return needle in json.dumps(settings.get("hooks", {}))


def _permitted(project: Path, home: Path | None = None) -> bool:
    """A rule that lets the wrapper run without a prompt, project or global."""
    rules: list[str] = []
    for settings in (_read_settings(Path(project) / ".claude" / "settings.json"),
                     _read_settings((Path(home) if home else Path.home())
                                    / ".claude" / "settings.json")):
        rules += [str(item) for item in (settings.get("permissions", {}) or {}).get("allow", [])]
    return any("val/run" in rule or "coder-ai val" in rule or "coder-ai-os val" in rule
               for rule in rules)


def pending(project: Path) -> list[Path]:
    """Markers saying UI changed and was never looked at."""
    return [config.parent / MARKER for config in _configs(project)
            if (config.parent / MARKER).is_file()]


def check(project: Path, *, home: Path | None = None) -> Chain:
    project = Path(project)
    configs = _configs(project)
    chain = Chain(configured=bool(configs))
    if not configs:
        return chain

    globs: list[str] = []
    for config in configs:
        data = _read_settings(config)
        globs += [str(item) for item in (data.get("watchGlobs") or [])]
    chain.links.append(
        Link("config", OK, f"{len(configs)} app(s), {len(globs)} watch glob(s)") if globs else
        Link("config", BROKEN, "no watchGlobs — nothing counts as UI",
             "set watchGlobs in .coder-ai/val/config.json"))

    tracked = _tracked(project)
    matched = [path for path in tracked
               if any(fnmatch.fnmatchcase(path, expanded)
                      for pattern in globs for expanded in _expand(pattern))]
    chain.links.append(
        Link("watch globs", OK, f"match {len(matched)} file(s) here") if matched else
        Link("watch globs", BROKEN, "match nothing here — the loop can never fire",
             "point watchGlobs at where the UI actually lives"))

    wrapper = project / WRAPPER
    chain.links.append(
        Link("wrapper", OK, WRAPPER)
        if wrapper.is_file() and os.access(wrapper, os.X_OK) else
        Link("wrapper", BROKEN, f"{WRAPPER} missing or not executable", "run: coder-ai sync"))

    settings = _read_settings(project / ".claude" / "settings.json")
    chain.links.append(
        Link("edit hook", OK, "marks UI validation pending after a matching edit")
        if _hook_installed(settings, HOOK) else
        Link("edit hook", BROKEN, "not installed — edits are never noticed", "run: coder-ai sync"))
    chain.links.append(
        Link("evidence hook", OK, "records what actually ran")
        if _hook_installed(settings, EVIDENCE_HOOK) else
        Link("evidence hook", BROKEN, "not installed — claims cannot be verified here",
             "run: coder-ai sync"))
    chain.links.append(
        Link("permission", OK, "the agent can run it without a prompt")
        if _permitted(project, home) else
        Link("permission", BROKEN,
             "no allow rule — every attempt prompts, so it gets skipped",
             "run: coder-ai install"))

    waiting = pending(project)
    if waiting:
        chain.links.append(Link("pending", BROKEN,
                                f"{len(waiting)} UI change(s) edited but never verified",
                                f"run: {WRAPPER} run --task <task>"))
    return chain


def main(argv: list[str] | None = None) -> int:
    chain = check(Path.cwd())
    print(chain.render(), end="")
    return 0 if chain.works else 1


if __name__ == "__main__":
    raise SystemExit(main())
