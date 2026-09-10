"""The startup permission notice (§16) and the watch-expiry report (§11).

Shown once, then coder-ai-os operates autonomously without re-asking permission for
normal repair work.
"""

from __future__ import annotations

from coderai.pr_automation.state import Session

ALLOWED = (
    "edit isolated PR workspace",
    "run repository checks",
    "create commits",
    "non-force push {branch}",
    "reply to PR discussion",
)
BLOCKED = (
    "push other branches",
    "force push",
    "merge",
    "deploy",
    "secrets",
    "branch protection",
)


def permission_notice(session: Session, worktree: str, *, allow_push: bool = True) -> str:
    allowed = [item.format(branch=session.head_branch) for item in ALLOWED]
    blocked = list(BLOCKED)
    if not allow_push:
        allowed = [item for item in allowed if not item.startswith("non-force push")]
        allowed = [item for item in allowed if item != "create commits"]
        blocked.insert(0, "push (this PR's head lives in another repository)")
    lines = [
        "coder-ai · PR Automation",
        "",
        f"PR          #{session.number}  {session.title}".rstrip(),
        f"Branch      {session.head_branch}",
        f"Workspace   {worktree}",
        f"Controller  {session.provider.capitalize()}",
        "",
        "Allowed",
        *[f"✓ {item}" for item in allowed],
        "",
        "Blocked",
        *[f"✗ {item}" for item in blocked],
        "",
    ]
    return "\n".join(lines)


def watch_expired_report(session: Session, *, ci: str = "unknown",
                         open_findings: int = 0) -> str:
    return "\n".join([
        f"coder-ai · PR #{session.number}",
        "",
        "Watch window ended.",
        "",
        f"PR       {session.state}",
        f"HEAD     {session.head_sha[:7]}",
        f"CI       {ci}",
        f"Findings {open_findings} open",
        "AI       sleeping",
        "",
        "Session state saved.",
        f"Continue with: coder-ai pr {session.number} --watch 2h -- {session.provider}",
        "",
    ])
