"""Opt-in: propose a workflow, ask once, remember the answer on this machine."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from coderai.delivery import declaration as declaration_module
from coderai.delivery.discovery import propose

LOCAL = "local/delivery.json"


def decision_path(project: Path) -> Path:
    return Path(project) / ".coder-ai" / LOCAL


def record(project: Path, enabled: bool, fingerprint: str = "") -> None:
    target = decision_path(project)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_text(json.dumps({"enabled": bool(enabled), "at": round(time.time(), 3),
                                  "proposal": fingerprint}, indent=2) + "\n",
                      encoding="utf-8")
    os.chmod(target, 0o600)


def answered(project: Path) -> dict | None:
    try:
        value = json.loads(decision_path(project).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def ask_base(project: Path, *, interactive: bool, out=print) -> str:
    """Which branch do you develop from? Feature branches are cut from it, and it
    is the one branch delivery may never push to."""
    from coderai.delivery.discovery import PREFERRED_BASE, base_candidates

    candidates = base_candidates(project)
    default = candidates[0] if candidates else f"origin/{PREFERRED_BASE}"
    if not interactive:
        return default
    shown = ", ".join(candidates) if candidates else "none found"
    out(f"Which branch do you develop from? Feature branches are cut from it, and")
    out(f"delivery may never push to it.  (found: {shown})")
    try:
        answer = input(f"Base branch [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return answer or default


def propose_and_record(project: Path, *, assume_yes: bool = False,
                       interactive: bool | None = None, base_branch: str = "",
                       reconsider: bool = False, out=print) -> int:
    """Show what was discovered and ask. Silence means no.

    `reconsider` is for an explicit `coder-ai ship enable`: the user asked to be
    asked, so a remembered answer must not short-circuit them into a dead end.
    """
    project = Path(project)
    can_ask_now = (interactive if interactive is not None
                   else (sys.stdin.isatty() and sys.stdout.isatty()))
    existing = declaration_module.load(project)
    previous = answered(project)

    # Settle "have we already asked?" BEFORE asking anything. Probing with the default
    # base first means a re-sync of an answered project stays silent; asking for the
    # base branch and then discarding the answer is worse than not asking at all.
    if existing is not None:
        probe = None
        probe_fingerprint = existing.path
    else:
        probe = propose(project, base_branch)
        probe_fingerprint = probe.fingerprint() if probe else ""
    if (previous is not None and not assume_yes and not reconsider and not base_branch
            and previous.get("proposal") == probe_fingerprint):
        state = "enabled" if previous.get("enabled") else "declined"
        out(f"coder-ai: delivery already {state} for this project "
            f"(change it with `coder-ai ship enable`).")
        return 0

    chosen = base_branch or (ask_base(project, interactive=bool(can_ask_now), out=out)
                             if existing is None else "")
    proposal = propose(project, chosen) if existing is None else None

    if existing is None and proposal is None:
        out("coder-ai: this project documents no delivery workflow — nothing to enable.")
        return 1

    fingerprint = existing.path if existing else (proposal.fingerprint() if proposal else "")
    if (previous is not None and previous.get("proposal") == fingerprint
            and not assume_yes and not reconsider):
        state = "enabled" if previous.get("enabled") else "declined"
        out(f"coder-ai: delivery already {state} for this project "
            f"(change it with `coder-ai ship enable`).")
        return 0

    if proposal is not None:
        out("Proposed delivery workflow for this project:")
        out(proposal.render())
    else:
        out(f"This project declares a delivery workflow in {existing.path}:")
        for step in existing.steps:
            out(f"  {step.name:<10} {step.text()}")

    if assume_yes:
        answer = True
    else:
        can_ask = (interactive if interactive is not None
                   else (sys.stdin.isatty() and sys.stdout.isatty()))
        if not can_ask:
            out("coder-ai: not enabling delivery automation (no answer available).")
            record(project, False, fingerprint)
            return 1
        try:
            answer = input("Enable delivery automation for this project? [y/N] ").strip().lower() \
                in {"y", "yes"}
        except (EOFError, KeyboardInterrupt):
            answer = False

    if answer and proposal is not None:
        proposal.write(project)
    record(project, answer, fingerprint)
    out("coder-ai: delivery enabled — the agent may now run `coder-ai ship`." if answer
        else "coder-ai: delivery not enabled. Nothing changed.")
    return 0 if answer else 1
