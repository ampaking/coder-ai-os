"""The executable side of the guard: decide, record, then exec the real binary.

`<session>/bin/git` and `<session>/bin/gh` are placed first on the child's PATH.
Every invocation is decided by `policy.decide`, recorded in `audit.jsonl`, and
either executed or refused with the reason — a silent failure teaches the agent
nothing and wastes a wake.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Sequence

CONFIG_ENV = "CODER_AI_GUARD_CONFIG"
REAL_GIT_ENV = "CODER_AI_REAL_GIT"
REAL_GH_ENV = "CODER_AI_REAL_GH"
DENY_EXIT = 13


def _load_config():
    from coderai.pr_automation.guard.policy import GuardConfig

    path = os.environ.get(CONFIG_ENV, "")
    if not path or not Path(path).is_file():
        return None
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return GuardConfig.from_dict(value) if isinstance(value, dict) else None


def _record(config, argv: Sequence[str], decision) -> None:
    if not config or not config.session_dir:
        return
    try:
        from coderai.pr_automation import audit

        audit.append(
            Path(config.session_dir),
            "guard_allow" if decision.allowed else "guard_deny",
            command=list(argv)[:40], code=decision.code, reason=decision.reason,
            cwd=os.getcwd(),
        )
    except Exception:  # audit must never break the guarded command
        pass


def main(argv: Sequence[str]) -> int:
    from coderai.pr_automation.guard.policy import decide

    argv = list(argv)
    program = Path(argv[0]).name
    real = os.environ.get(REAL_GH_ENV if program.startswith("gh") else REAL_GIT_ENV, "")
    config = _load_config()

    if config is None:
        # No session scope means no elevation: refuse rather than fall through.
        sys.stderr.write(
            "coder-ai guard: no PR-automation scope in this environment; refusing "
            f"{program}\n"
        )
        return DENY_EXIT

    decision = decide(argv, os.environ, os.getcwd(), config)
    _record(config, argv, decision)
    if not decision.allowed:
        sys.stderr.write(
            f"coder-ai guard: refused `{' '.join(argv[:8])}`\n"
            f"  reason: {decision.reason}\n"
            f"  code:   {decision.code}\n"
            f"  scope:  PR branch {config.head_branch!r} on remote {config.remote!r}, "
            f"worktree {config.worktree}\n"
        )
        return DENY_EXIT

    if not real or not Path(real).exists():
        sys.stderr.write(f"coder-ai guard: cannot locate the real {program} binary\n")
        return 127
    try:
        os.execv(real, [real, *argv[1:]])
    except OSError as error:
        sys.stderr.write(f"coder-ai guard: could not run {real}: {error}\n")
        return 126
    return 0  # unreachable: execv replaces the process
