"""The PR-automation execution profile (§5, §13, §14, §16, §25).

Elevation is bound to this repository + this PR + this worktree + this session.
It is never written into the user's configuration: the provider is launched with
a session-lifetime settings file and a child environment, both removed on exit.

The provider's permission config is a convenience layer — it tells the agent what
it may attempt. The boundary that *enforces* it is the guard (Task 05), which is
why a repository cannot widen this profile by editing its own instructions.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from coderai.pr_automation.guard.install import InstalledGuard

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE_PERMISSIONS = REPO_ROOT / "claude" / "permissions.json"

# Denied in the normal profile, granted (scoped) here. Removed from `deny` and
# added to `allow` — a rule in both lists would otherwise deny.
# Commit and push are NOT listed: a normal session may already run them, so they are
# inherited rather than granted. Only what a plain session cannot do belongs here.
GIT_WRITE_ALLOW = (
    "Bash(git add:*)",
    "Bash(git restore:*)",
    "Bash(gh pr comment:*)",
    "Bash(gh pr view:*)",
    "Bash(gh pr diff:*)",
    "Bash(gh pr checks:*)",
    "Bash(gh api:*)",
)
# Nothing left to un-deny: the rules a PR session needs are no longer denied to a
# normal one. Kept as the seam for the next rule that has to be lifted per session.
GIT_WRITE_UNDENY = ()
# The guard lives on PATH; an absolute-path git would skip it (the pre-push hook
# still catches pushes, but denying it here removes the temptation entirely).
ABSOLUTE_GIT_DENY = (
    "Bash(/usr/bin/git:*)", "Bash(/usr/local/bin/git:*)", "Bash(/opt/homebrew/bin/git:*)",
    "Bash(/usr/bin/env git:*)", "Bash(env git:*)",
)
PR_MODE_DENY = (
    "Bash(git push --force:*)", "Bash(git push -f:*)", "Bash(gh pr merge:*)",
    "Bash(gh pr close:*)", "Bash(gh release:*)", "Bash(gh secret:*)",
    "Bash(gh workflow run:*)",
    # Ordinary git — merge, pull, rebase, reset, checkout — is NOT listed. A PR session
    # runs unattended in its own worktree and has to finish the branch without a human;
    # blocking the integration commands is how automation stalls halfway. What stays
    # denied is only what cannot be undone: force push, and closing or merging the PR.
    *ABSOLUTE_GIT_DENY,
)

# Provider arguments that would dismantle the boundary. Refused, not overridden.
FORBIDDEN_ARGS = {
    "claude": (
        "--dangerously-skip-permissions", "--settings", "--permission-mode",
        "--allowed-tools", "--allowedTools", "--add-dir",
    ),
    "codex": (
        "--dangerously-bypass-approvals-and-sandbox", "--sandbox", "--cd",
        "--ask-for-approval", "--full-auto", "-C",
    ),
}


class ProfileError(RuntimeError):
    """The elevated session cannot be launched safely."""


@dataclass
class LaunchPlan:
    argv: list[str]
    env: dict[str, str]
    cwd: Path
    settings_path: Path | None = None
    artifacts: list[Path] = field(default_factory=list)

    def cleanup(self) -> None:
        """Elevation must not outlive the session (§17)."""
        for path in self.artifacts:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue


def sanitize_provider_argv(provider: str, argv: Sequence[str]) -> list[str]:
    """Refuse provider arguments that would weaken or relocate the boundary."""
    forbidden = FORBIDDEN_ARGS.get(provider, ())
    for item in list(argv)[1:]:
        name = item.split("=", 1)[0]
        if name in forbidden:
            raise ProfileError(
                f"{item} cannot be used with coder-ai pr: coder-ai-os owns the permission scope "
                "for a PR-automation session"
            )
    return list(argv)


def _base_permissions() -> dict:
    if BASE_PERMISSIONS.is_file():
        try:
            value = json.loads(BASE_PERMISSIONS.read_text(encoding="utf-8"))
            if isinstance(value, dict) and isinstance(value.get("permissions"), dict):
                return value
        except (OSError, json.JSONDecodeError):
            pass
    return {"permissions": {"defaultMode": "auto", "allow": [], "deny": []}}


def build_claude_settings(guard: InstalledGuard) -> dict:
    """The normal profile, with git writes granted and every other denial kept."""
    base = _base_permissions()
    permissions = dict(base.get("permissions", {}))
    allow = list(permissions.get("allow", []))
    deny = [item for item in permissions.get("deny", []) if item not in GIT_WRITE_UNDENY]
    for rule in GIT_WRITE_ALLOW:
        if rule not in allow:
            allow.append(rule)
    for rule in PR_MODE_DENY:
        if rule not in deny:
            deny.append(rule)
    return {
        "permissions": {
            "defaultMode": permissions.get("defaultMode", "auto"),
            "allow": sorted(set(allow)),
            "deny": sorted(set(deny)),
        },
        "skipAutoPermissionPrompt": True,
    }


def _write_settings(guard: InstalledGuard, value: dict) -> Path:
    target = guard.bin_dir.parent / "claude-settings.json"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(target, 0o600)
    return target


def build_profile(provider: str, provider_argv: Sequence[str], *, guard: InstalledGuard,
                  worktree: Path, session_slug: str = "") -> LaunchPlan:
    """Produce the exact argv, environment, and cwd for the elevated provider run."""
    argv = sanitize_provider_argv(provider, provider_argv)
    worktree = Path(worktree)
    if not worktree.is_dir():
        raise ProfileError(f"worktree does not exist: {worktree}")

    env = {**os.environ, **guard.env}
    # Never leak a relocating git environment into the child, whatever we inherited.
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_GLOBAL",
                 "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_SSH_COMMAND"):
        env.pop(name, None)
    env["CODER_AI_PR_MODE"] = "1"
    env["CODER_AI_PR_WORKTREE"] = str(worktree)
    env["CODER_AI_PR_BRANCH"] = guard.config.head_branch
    env["CODER_AI_PR_REMOTE"] = guard.config.remote
    if session_slug:
        env["CODER_AI_PR_SLUG"] = session_slug

    artifacts: list[Path] = []
    settings_path: Path | None = None

    if provider == "claude":
        settings_path = _write_settings(guard, build_claude_settings(guard))
        artifacts.append(settings_path)
        launch = [argv[0], "--settings", str(settings_path), *argv[1:]]
    elif provider == "codex":
        launch = [argv[0], "--cd", str(worktree), "--sandbox", "workspace-write",
                  "--ask-for-approval", "never", *argv[1:]]
    else:
        raise ProfileError(f"unsupported provider for PR automation: {provider}")

    return LaunchPlan(argv=launch, env=env, cwd=worktree, settings_path=settings_path,
                      artifacts=artifacts)
