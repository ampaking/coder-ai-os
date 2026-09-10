"""`coder-ai status` — where am I?

One friendly answer to the four questions people actually have: is this project
set up, which agents are configured, what was I doing, and is anything running.

`coder-ai verify` remains the strict, exhaustive check; this is the human view.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HOME_MARKERS = (
    ("Claude", ".claude/CLAUDE.md"),
    ("Codex", ".codex/AGENTS.md"),
    ("Gemini", ".gemini/GEMINI.md"),
)
PROJECT_MARKERS = (
    ("Cursor", ".cursor/rules/coder-ai-os.mdc"),
    ("Copilot", ".github/copilot-instructions.md"),
)
MANAGED = "coder-ai-os:managed"
MAX_TASK_LINE = 90


@dataclass
class Status:
    project: str = ""
    is_repo: bool = False
    set_up: bool = False
    agents: list[tuple[str, bool]] = field(default_factory=list)
    atlas_maps: int = 0
    atlas: bool = False
    task: str = ""
    tasks_enabled: bool = False
    val: bool = False
    visual_ok: bool = True
    visual_note: str = ""
    sessions: list[str] = field(default_factory=list)
    other_sessions: int = 0

    def next_steps(self) -> list[str]:
        steps: list[str] = []
        if not self.set_up:
            steps.append("coder-ai setup            set this project up")
        elif not self.atlas:
            steps.append("coder-ai sync             build the code atlas")
        if not any(installed for _name, installed in self.agents):
            steps.append("coder-ai install          configure your agents")
        if self.visual_note:
            steps.append("coder-ai sync             repair the visual loop")
        if self.set_up and self.atlas and not steps:
            steps.append("coder-ai pr <n> -- claude  supervise a pull request")
            steps.append("coder-ai find <symbol>     locate code without scanning")
        return steps


def _git_root(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def _has_managed_block(path: Path) -> bool:
    try:
        return MANAGED in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _current_task(project: Path) -> str:
    path = project / ".ai" / "memory" / "CURRENT.md"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") and "current task checkpoint" not in stripped.lower():
            return stripped.lstrip("# ").strip()[:MAX_TASK_LINE]
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:MAX_TASK_LINE]
    return ""


def _remote_slug(project: Path) -> tuple[str, str] | None:
    """owner/repo from the git remote, without needing `gh` or the network."""
    import subprocess

    try:
        result = subprocess.run(["git", "-C", str(project), "remote", "get-url", "origin"],
                                capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    url = result.stdout.strip()
    match = re.search(r"[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?$", url)
    return (match.group(1), match.group(2)) if match else None


def collect(cwd: Path | None = None, *, state_root: Path | None = None,
            home: Path | None = None) -> Status:
    start = Path(cwd or os.getcwd())
    home = Path(home) if home else Path.home()
    root = _git_root(start)
    project = root or start
    status = Status(project=project.name, is_repo=root is not None)

    status.set_up = (project / ".coder-ai").is_dir()
    status.val = (project / ".coder-ai" / "val" / "run").exists()
    try:
        from coderai.evidence.val_link import check as check_visual

        chain = check_visual(project, home=home)
        status.visual_ok = chain.works or not chain.configured
        if chain.configured and not chain.works:
            broken = chain.broken[0]
            status.visual_note = f"{broken.name}: {broken.detail}"
    except Exception:
        pass
    status.tasks_enabled = (project / ".coder-ai" / "tasks" / "settings.json").is_file()

    for name, relative in HOME_MARKERS:
        status.agents.append((name, _has_managed_block(home / relative)))
    for name, relative in PROJECT_MARKERS:
        status.agents.append((name, (project / relative).exists()))

    symbols = project / ".ai" / "symbols"
    if symbols.is_dir():
        status.atlas_maps = sum(1 for _ in symbols.rglob("*.md"))
        status.atlas = status.atlas_maps > 0
    status.task = _current_task(project)

    status.sessions, status.other_sessions = _sessions(project, state_root)
    return status


def _sessions(project: Path, state_root: Path | None) -> tuple[list[str], int]:
    """This project's PR sessions first; everything else is only counted."""
    try:
        from coderai.pr_automation.state import list_sessions, sessions_root

        rows = list_sessions(state_root or sessions_root())
    except Exception:
        return [], 0
    slug = _remote_slug(project)
    mine: list[str] = []
    others = 0
    for item in rows:
        if slug and (item.owner.lower(), item.repo.lower()) == (slug[0].lower(),
                                                                slug[1].lower()):
            if not item.is_terminal:
                mine.append(f"#{item.number} {item.state} ({item.provider})")
        elif not item.is_terminal:
            others += 1
    return mine, others


def render(status: Status) -> str:
    def mark(value: bool) -> str:
        return "●" if value else "○"

    agents = "  ".join(f"{mark(installed)} {name}" for name, installed in status.agents)
    lines = [f"coder-ai · {status.project}", ""]
    lines.append(f"{'Project':<12}" + ("set up" if status.set_up
                                       else "not set up — run: coder-ai setup"))
    lines.append(f"{'Agents':<12}{agents}")
    lines.append(f"{'Atlas':<12}" + (f"{status.atlas_maps} maps" if status.atlas
                                     else "not built — run: coder-ai sync"))
    extras = []
    if status.val:
        extras.append("visual loop ready")
    if status.tasks_enabled:
        extras.append("project tasks on")
    if extras:
        lines.append(f"{'Enabled':<12}" + " · ".join(extras))
    if status.visual_note:
        lines.append(f"{'Visual loop':<12}broken — {status.visual_note}")
    lines.append(f"{'Task':<12}" + (status.task or "—"))
    if status.sessions:
        lines.append(f"{'PR sessions':<12}" + "; ".join(status.sessions))
    elif status.other_sessions:
        lines.append(f"{'PR sessions':<12}none here "
                     f"({status.other_sessions} in other projects)")
    else:
        lines.append(f"{'PR sessions':<12}none")

    steps = status.next_steps()
    if steps:
        lines += ["", "Next"]
        lines += [f"  {step}" for step in steps]
    return "\n".join(lines) + "\n"


USAGE = """usage:
  coder-ai status [--json]     where am I? project, agents, current task, PR sessions"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help", "help"}:
        print(USAGE)
        return 0
    status = collect()
    if "--json" in args:
        import json
        from dataclasses import asdict

        print(json.dumps(asdict(status), indent=2, sort_keys=True))
        return 0
    print(render(status), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
