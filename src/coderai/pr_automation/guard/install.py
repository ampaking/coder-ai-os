"""Install the guard for one session: PATH shims, scope file, and the push hook.

Defense in depth (§15) — three independent layers:

  1. PATH shims          decide and explain every `git` / `gh` the agent runs
  2. pre-push hook       runs for any git binary in the worktree, including one
                         invoked by absolute path, so it catches PATH bypasses
  3. post-push checks    Task 14 verifies afterwards that only the PR head moved

Residual gap, stated rather than papered over: `git --no-verify` from an
absolute path skips layers 1 and 2. The shim denies `--no-verify`, the provider
profile (Task 06) denies absolute-path git, and layer 3 still detects it after
the fact.
"""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from coderai.pr_automation.gitcmd import Git
from coderai.pr_automation.guard.policy import GuardConfig
from coderai.pr_automation.state import write_json

GUARD_DIR = "guard"
CONFIG_FILE = "guard.json"
SOURCE = Path(__file__).resolve().parent
PACKAGE_ROOT = SOURCE.parents[2]  # .../src


class GuardInstallError(RuntimeError):
    """The permission boundary could not be established — do not start the session."""


@dataclass(frozen=True)
class InstalledGuard:
    bin_dir: Path
    config_path: Path
    config: GuardConfig
    hook_path: Path | None
    env: dict[str, str]


def _real_binary(name: str) -> str:
    """Resolve the genuine binary, ignoring any shim already on PATH."""
    path = os.environ.get("PATH", "")
    parts = [item for item in path.split(os.pathsep)
             if item and GUARD_DIR not in Path(item).parts]
    found = shutil.which(name, path=os.pathsep.join(parts))
    return found or ""


def _install_hook(worktree: Path, config: GuardConfig, config_path: Path,
                  hooks_dir: Path) -> Path | None:
    """Install the pre-push guard in a hooks directory owned by THIS worktree.

    Worktrees share the common `.git/hooks`, so writing there would also constrain
    the engineer's own checkout — exactly what §12/§13 forbid. Per-worktree config
    (`core.hooksPath` under `extensions.worktreeConfig`) keeps the hook scoped to
    the automation workspace, while still applying to any git binary run inside it.
    """
    git = Git(worktree)
    if not git.is_repo():
        return None
    directory = Path(hooks_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    git.run("config", "extensions.worktreeConfig", "true")
    scoped = git.run("config", "--worktree", "core.hooksPath", str(directory))
    if not scoped.ok:
        # Without per-worktree config the hook would leak into the human's checkout.
        return None
    target = directory / "pre-push"
    template = (SOURCE / "pre-push").read_text(encoding="utf-8")
    body = (template
            .replace("@CONFIG@", str(config_path))
            .replace("@HEAD_BRANCH@", config.head_branch))
    target.write_text(body, encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def install(session_dir: Path, worktree: Path, *, remote: str, head_branch: str,
            local_branch: str, allow_push: bool = True) -> InstalledGuard:
    """Create the session's permission boundary and return the child environment."""
    session_dir = Path(session_dir)
    worktree = Path(worktree)
    if not worktree.is_dir():
        raise GuardInstallError(f"worktree does not exist: {worktree}")

    config = GuardConfig(
        worktree=str(worktree.resolve()), remote=remote, head_branch=head_branch,
        local_branch=local_branch, session_dir=str(session_dir.resolve()),
        allow_push=allow_push,
    )
    session_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    config_path = session_dir / CONFIG_FILE
    write_json(config_path, config.to_dict())

    bin_dir = session_dir / GUARD_DIR
    bin_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ("git", "gh"):
        target = bin_dir / name
        body = (SOURCE / name).read_text(encoding="utf-8").replace(
            "@PYTHONPATH@", str(PACKAGE_ROOT))
        target.write_text(body, encoding="utf-8")
        target.chmod(0o700)

    real_git = _real_binary("git")
    if not real_git:
        raise GuardInstallError("cannot find the real git binary to guard")
    real_gh = _real_binary("gh")

    hook = _install_hook(worktree, config, config_path, session_dir / "hooks")

    env = {
        "PATH": os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]),
        "CODER_AI_GUARD_CONFIG": str(config_path),
        "CODER_AI_GUARD_PYTHONPATH": str(PACKAGE_ROOT),
        "CODER_AI_REAL_GIT": real_git,
        "CODER_AI_PR_SESSION": f"{config.head_branch}",
    }
    if real_gh:
        env["CODER_AI_REAL_GH"] = real_gh
    return InstalledGuard(bin_dir=bin_dir, config_path=config_path, config=config,
                          hook_path=hook, env=env)


def uninstall(installed: InstalledGuard, worktree: Path | None = None) -> None:
    """Remove the boundary's artifacts when the session ends (§17)."""
    shutil.rmtree(installed.bin_dir, ignore_errors=True)
    installed.config_path.unlink(missing_ok=True)
    if installed.hook_path and installed.hook_path.exists():
        installed.hook_path.unlink(missing_ok=True)
        shutil.rmtree(installed.hook_path.parent, ignore_errors=True)
    if worktree is not None and Path(worktree).is_dir():
        Git(Path(worktree)).run("config", "--worktree", "--unset", "core.hooksPath")
