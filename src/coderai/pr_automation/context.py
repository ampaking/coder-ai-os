"""The context bundle handed to a woken AI (§46, §49).

coder-ai-os gathers; the AI interprets. This deliberately carries raw conversation and
raw evidence — never a pre-chewed conclusion about what a reviewer "meant".

The bundle is bounded, and every omission is marked, so the model can tell
"nothing there" from "capped".
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from coderai.pr_automation.delta import Delta
from coderai.pr_automation.findings import Ledger, finding_id
from coderai.pr_automation.snapshot import Snapshot
from coderai.pr_automation.state import Session

MAX_BUNDLE = 60_000
# Structured AI reviews arrive as ONE long comment carrying several findings
# (CRITICAL / WARNING / SUGGESTION plus a checklist). Clipping the newest one at a
# few thousand characters would silently drop findings, so recent comments get a
# far larger budget than historical ones — and every clip says so.
MAX_COMMENT_BODY = 16_000
MAX_OLDER_BODY = 2_500
FULL_BODY_COMMENTS = 6
RECENT_COMMENTS = 25
RECENT_REVIEWS = 15
MAX_CHANGED_FILES = 100

# Files that tell an agent how this repository expects work to be done (§21).
GUIDANCE_FILES = (
    "AGENTS.md", "CLAUDE.md", "AI_DEV_PROTOCOL.md", "CONTRIBUTING.md", "DEVELOPMENT.md",
    "README.md", "Makefile", "makefile", "Taskfile.yml", "justfile", "package.json",
    "pyproject.toml", "pytest.ini", "tox.ini", "Cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "docker-compose.yml", "compose.yaml",
)


def discover_guidance(worktree: Path, changed_files: Iterable[str] = ()) -> list[str]:
    """List which guidance files exist — the AI reads them; coder-ai-os never summarises."""
    found: list[str] = []
    root = Path(worktree)
    for name in GUIDANCE_FILES:
        if (root / name).is_file():
            found.append(name)
    if (root / ".github" / "workflows").is_dir():
        for path in sorted((root / ".github" / "workflows").glob("*.y*ml"))[:10]:
            found.append(f".github/workflows/{path.name}")
    # Component-level instructions next to the changed code (§24).
    seen: set[str] = set()
    for changed in list(changed_files)[:MAX_CHANGED_FILES * 5]:
        parent = Path(changed).parent
        while str(parent) not in {".", "/", ""}:
            if str(parent) in seen:
                break
            seen.add(str(parent))
            for name in ("AGENTS.md", "CLAUDE.md", "Makefile", "package.json"):
                candidate = root / parent / name
                if candidate.is_file():
                    found.append(f"{parent}/{name}")
            parent = parent.parent
    return sorted(dict.fromkeys(found))[:60]


def _clip_body(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    return (f"{text[:limit]}\n[clipped {dropped} characters by coder-ai-os — "
            "read the full comment on the PR if this matters]")


def _conversation(snapshot: Snapshot, ledger: Ledger) -> list[str]:
    """Raw conversation, newest given room, each with a stable id to act against."""
    lines: list[str] = []
    comments = snapshot.comments[-RECENT_COMMENTS:]
    if len(snapshot.comments) > len(comments):
        lines.append(f"[{len(snapshot.comments) - len(comments)} older comments omitted]")
    recent = {item.id for item in comments[-FULL_BODY_COMMENTS:]}
    for item in comments:
        who = f"{item.author}{' (bot)' if item.is_bot else ''}"
        limit = MAX_COMMENT_BODY if item.id in recent else MAX_OLDER_BODY
        lines.append(f"--- comment {item.id} by {who} at {item.created_at}"
                     f"  [candidate finding id: {finding_id(item.id)}]")
        lines.append(_clip_body(item.body, limit))
    for item in snapshot.reviews[-RECENT_REVIEWS:]:
        who = f"{item.author}{' (bot)' if item.is_bot else ''}"
        lines.append(f"--- review {item.id} by {who}: {item.state} at {item.submitted_at}"
                     f"  [candidate finding id: {finding_id('review:' + item.id)}]")
        if item.body:
            lines.append(_clip_body(item.body, MAX_COMMENT_BODY))
    return lines


def _threads(snapshot: Snapshot) -> list[str]:
    by_thread: dict[str, list] = {}
    for item in snapshot.review_comments:
        by_thread.setdefault(item.thread_id, []).append(item)
    lines: list[str] = []
    for thread_id, items in by_thread.items():
        head = items[0]
        status = ("RESOLVED" if head.resolved else
                  "OUTDATED" if head.outdated else "OPEN")
        if head.resolved and head.resolved_by:
            status += f" by {head.resolved_by}"
            if head.resolved_by.lower() == (snapshot.author or "").lower():
                status += " (the PR author, not the reviewer)"
        location = f"{head.path}:{head.line}" if head.path else "(no anchor)"
        lines.append(f"--- thread {thread_id} [{status}] {location}")
        lines.append(f"    [finding id: {finding_id(thread_id)}]")
        for item in items:
            who = f"{item.author}{' (bot)' if item.is_bot else ''}"
            lines.append(f"    {who}: {_clip_body(item.body, MAX_COMMENT_BODY)}")
    return lines


def _checks(snapshot: Snapshot) -> list[str]:
    lines = [f"CI rollup: {snapshot.ci_state}"]
    for check in snapshot.checks[:40]:
        mark = "FAIL" if check.failed else ("…" if check.pending else "ok")
        required = " (required)" if check.required else ""
        lines.append(f"  [{mark}] {check.name}{required} {check.url}".rstrip())
    return lines


def build_bundle(session: Session, snapshot: Snapshot, delta: Delta, ledger: Ledger,
                 worktree: Path, *, extra: str = "") -> str:
    """Assemble the wake context. Bounded, marked when truncated, evidence-only."""
    guidance = discover_guidance(worktree, snapshot.changed_files)
    sections: list[str] = [
        "# coder-ai-os PR automation — wake context",
        "",
        f"Repository   {session.owner}/{session.repo}",
        f"Pull request #{snapshot.number}  {snapshot.title}",
        f"Author       {snapshot.author}",
        f"Branch       {snapshot.head_branch} -> {snapshot.base_branch}",
        f"HEAD         {snapshot.head_sha}",
        f"PR state     {snapshot.state}{' (draft)' if snapshot.is_draft else ''}",
        f"Worktree     {worktree}   (your isolated workspace; edit here)",
        "",
        f"## Why you are awake: {delta.wake_reason or 'triage'}",
        delta.summary(),
        "",
        "## Pull request description",
        snapshot.body[:MAX_COMMENT_BODY] or "(empty)",
        "",
        "## Known findings (coder-ai-os ledger — do not re-analyse settled ones)",
        ledger.view() or "(none yet)",
        "",
        "## Conversation",
        *_conversation(snapshot, ledger),
        "",
        "## Inline review threads",
        *(_threads(snapshot) or ["(none)"]),
        "",
        "## Checks",
        *_checks(snapshot),
        "",
        "## Changed files",
        *[f"  {name}" for name in snapshot.changed_files[:MAX_CHANGED_FILES]],
        *([f"  [{len(snapshot.changed_files) - MAX_CHANGED_FILES} more changed files not "
           f"listed — inspect the worktree]"]
          if len(snapshot.changed_files) > MAX_CHANGED_FILES else []),
        "",
        "## Repository guidance present in this worktree (read what applies)",
        *([f"  {name}" for name in guidance] or ["  (none found)"]),
        "",
        "## Linked issues",
        *([f"  {name}" for name in snapshot.linked_issues] or ["  (none)"]),
    ]
    if snapshot.truncated:
        sections += ["", f"## Truncation: {', '.join(snapshot.truncated)}"]
    if snapshot.partial:
        sections += ["", f"## Incomplete collection: {', '.join(snapshot.partial_reasons)}",
                     "Treat missing sections as unknown, not as absent."]
    if extra:
        sections += ["", extra]

    text = "\n".join(sections)
    if len(text) > MAX_BUNDLE:
        text = text[:MAX_BUNDLE] + "\n\n[context truncated by coder-ai-os at "
        text += f"{MAX_BUNDLE} characters — ask for specifics from the worktree instead]"
    return text
