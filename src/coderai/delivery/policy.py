"""The delivery allow/deny matrix.

A declaration is data written by a repository. §25 says repository data may direct
engineering and never widen the boundary — that has to hold here too. A project
that declares `git push --force origin main` gets a refusal, not a force push.

Deliberately a sibling of the PR matrix rather than a flag on it: a `coder-ai pr`
session must not silently gain `rebase` and branch creation because delivery
needed them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from coderai.pr_automation.guard.policy import (
    ALLOW, DENY_CONFIG_WRITE, DENY_DELETE_REF, DENY_ENV, DENY_FORCE_PUSH,
    DENY_GH_ENDPOINT, DENY_GH_SUBCOMMAND, DENY_GLOBAL_OPTION, DENY_NO_VERIFY,
    DENY_OUTSIDE_WORKTREE, DENY_PUSH_ALL, DENY_PUSH_REF, DENY_PUSH_REMOTE,
    DENY_PUSH_TAGS, DENY_SUBCOMMAND, Decision, _allow, _check_environment, _deny,
    _inside, _split_global_options,
)

DENY_BRANCH = "DENY_BRANCH"
DENY_REBASE_TARGET = "DENY_REBASE_TARGET"
DENY_PROGRAM = "DENY_PROGRAM"

PROTECTED = {"main", "master", "trunk", "release", "production", "develop", "HEAD"}

# git the delivery flow needs, and nothing more.
GIT_ALLOWED = {
    "status", "diff", "log", "show", "rev-parse", "rev-list", "ls-files", "merge-base",
    "fetch", "add", "commit", "push", "switch", "checkout", "rebase", "branch",
    "symbolic-ref", "config", "restore",
}
GIT_DENIED = {
    "merge": "merging is a human decision",
    "reset": "reset can discard commits",
    "tag": "tags are release artifacts, not delivery steps",
    "remote": "remote configuration is outside delivery",
    "worktree": "worktrees are outside delivery",
    "clean": "clean deletes untracked files",
    "filter-branch": "history rewriting is never permitted",
    "update-ref": "direct ref writes bypass the push boundary",
    "send-pack": "low-level push bypasses the push boundary",
    "credential": "credential access is never permitted",
    "gc": "repository maintenance is outside delivery",
    "pull": "pull merges or rebases implicitly; the workflow declares fetch and rebase",
}
PUSH_FORCE = ("--force", "-f", "--force-with-lease", "--force-if-includes")
PUSH_DENIED = {
    "--mirror": DENY_PUSH_ALL, "--all": DENY_PUSH_ALL, "--tags": DENY_PUSH_TAGS,
    "--follow-tags": DENY_PUSH_TAGS, "--delete": DENY_DELETE_REF, "-d": DENY_DELETE_REF,
    "--prune": DENY_DELETE_REF, "--receive-pack": DENY_PUSH_REF, "--exec": DENY_PUSH_REF,
}
PUSH_FLAGS_OK = {"-u", "--set-upstream", "-q", "--quiet", "-v", "--verbose", "--porcelain",
                 "--atomic", "--progress", "--no-progress", "--dry-run", "-n", "--verify"}

# Build tooling a workflow may declare. Anything else must be named explicitly.
PROGRAMS = {"make", "just", "task", "npm", "npx", "pnpm", "yarn", "bun", "python", "python3",
            "pytest", "tox", "uv", "poetry", "pip", "cargo", "go", "gradle", "mvn", "dotnet",
            "rake", "bundle", "composer", "php", "node", "deno", "swift", "ruby", "sh"}
PROGRAMS -= {"sh"}          # never a shell

GH_READ = {("pr", "view"), ("pr", "list"), ("pr", "status"), ("pr", "diff"), ("pr", "checks"),
           ("issue", "view"), ("issue", "list"), ("repo", "view"), ("auth", "status"),
           ("run", "view"), ("run", "list")}
GH_WRITE = {("pr", "create"), ("issue", "create"), ("pr", "comment"), ("issue", "comment")}


@dataclass(frozen=True)
class DeliveryScope:
    project: str
    remote: str
    base: str                    # e.g. origin/develop
    branch_pattern: str

    @property
    def base_branch(self) -> str:
        return self.base.split("/", 1)[1] if "/" in self.base else self.base

    def matches(self, branch: str) -> bool:
        return bool(branch and re.match(self.branch_pattern, branch))

    def protected(self, name: str) -> bool:
        cleaned = name.replace("refs/heads/", "")
        return cleaned in PROTECTED or cleaned == self.base_branch


def _branch_from_refspec(refspec: str) -> str:
    destination = refspec.split(":")[-1]
    return destination.replace("refs/heads/", "")


def _decide_push(args: Sequence[str], scope: DeliveryScope) -> Decision:
    positional: list[str] = []
    for item in args:
        name = item.split("=", 1)[0]
        if item in PUSH_FORCE or name in PUSH_FORCE:
            return _deny(DENY_FORCE_PUSH,
                         f"{name} would overwrite published history")
        if name in PUSH_DENIED:
            return _deny(PUSH_DENIED[name], f"git push {name} is outside delivery")
        if item.startswith("-"):
            if item == "--no-verify":
                return _deny(DENY_NO_VERIFY, "--no-verify skips the repository's own hooks")
            if item in PUSH_FLAGS_OK:
                continue
            return _deny(DENY_PUSH_REF, f"git push {item} is not permitted")
        positional.append(item)

    if not positional:
        return _deny(DENY_PUSH_REF,
                     "delivery pushes name their remote and branch explicitly")
    remote, refspecs = positional[0], positional[1:]
    if remote != scope.remote:
        return _deny(DENY_PUSH_REMOTE,
                     f"delivery pushes to {scope.remote!r}, not {remote!r}")
    if not refspecs:
        return _deny(DENY_PUSH_REF, "delivery pushes name the branch explicitly")
    for refspec in refspecs:
        if refspec.startswith("+"):
            return _deny(DENY_FORCE_PUSH, f"the '+' in {refspec!r} means a force update")
        if refspec.startswith(":") or refspec.endswith(":"):
            return _deny(DENY_DELETE_REF, f"{refspec!r} would delete a remote ref")
        branch = _branch_from_refspec(refspec)
        if scope.protected(branch):
            return _deny(DENY_PUSH_REF,
                         f"{branch!r} is a protected branch; delivery pushes feature branches")
        if not scope.matches(branch):
            return _deny(DENY_PUSH_REF,
                         f"{branch!r} does not match this project's branch pattern "
                         f"{scope.branch_pattern!r}")
    return _allow()


def _decide_branch_move(subcommand: str, args: Sequence[str], scope: DeliveryScope) -> Decision:
    creating = any(item in {"-c", "-b", "-C", "-B"} for item in args)
    targets = [item for item in args if not item.startswith("-")]
    if not creating:
        return _deny(DENY_BRANCH,
                     f"git {subcommand} without creating a branch moves off the work; "
                     "delivery only creates its own feature branch")
    if not targets:
        return _deny(DENY_BRANCH, f"git {subcommand} needs a branch name")
    name = targets[0]
    if scope.protected(name):
        return _deny(DENY_BRANCH, f"{name!r} is a protected branch")
    if not scope.matches(name):
        return _deny(DENY_BRANCH,
                     f"{name!r} does not match {scope.branch_pattern!r}")
    return _allow()


def _decide_rebase(args: Sequence[str], scope: DeliveryScope) -> Decision:
    for item in args:
        if item in {"-i", "--interactive", "--exec", "-x", "--onto", "--root"}:
            return _deny(DENY_REBASE_TARGET, f"git rebase {item} is outside delivery")
    targets = [item for item in args if not item.startswith("-")]
    if not targets:
        return _deny(DENY_REBASE_TARGET, "delivery rebases onto its declared base explicitly")
    if targets[0] not in {scope.base, scope.base_branch}:
        return _deny(DENY_REBASE_TARGET,
                     f"delivery rebases onto {scope.base!r}, not {targets[0]!r}")
    return _allow()


def _decide_branch(args: Sequence[str], scope: DeliveryScope) -> Decision:
    for item in args:
        if item in {"-d", "-D", "-m", "-M", "--delete", "--move"}:
            return _deny(DENY_BRANCH, f"git branch {item} is outside delivery")
    return _allow()


def _decide_config(args: Sequence[str]) -> Decision:
    if any(item in {"--get", "--get-all", "--get-regexp", "--list", "-l"} for item in args):
        return _allow()
    return _deny(DENY_CONFIG_WRITE, "git config writes could re-point the remote")


def decide_git(argv: Sequence[str], env: Mapping[str, str], cwd: str | Path,
               scope: DeliveryScope) -> Decision:
    args = list(argv)[1:]
    if not args:
        return _allow()
    verdict = _check_environment(env)
    if not verdict:
        return verdict
    if not _inside(cwd, scope.project):
        return _deny(DENY_OUTSIDE_WORKTREE,
                     f"delivery runs inside {scope.project}; refusing git in {cwd}")
    verdict, rest = _split_global_options(args)
    if not verdict or not rest:
        return verdict if not verdict else _allow()

    subcommand, options = rest[0], rest[1:]
    if subcommand in GIT_DENIED:
        return _deny(DENY_SUBCOMMAND, f"git {subcommand}: {GIT_DENIED[subcommand]}")
    if subcommand not in GIT_ALLOWED:
        return _deny(DENY_SUBCOMMAND, f"git {subcommand} is not part of delivery")
    if subcommand == "push":
        return _decide_push(options, scope)
    if subcommand in {"switch", "checkout"}:
        return _decide_branch_move(subcommand, options, scope)
    if subcommand == "rebase":
        return _decide_rebase(options, scope)
    if subcommand == "branch":
        return _decide_branch(options, scope)
    if subcommand == "config":
        return _decide_config(options)
    if subcommand == "commit" and any(item in {"--no-verify", "-n"} for item in options):
        return _deny(DENY_NO_VERIFY, "--no-verify skips the repository's own hooks")
    return _allow()


def decide_gh(argv: Sequence[str], env: Mapping[str, str], cwd: str | Path,
              scope: DeliveryScope) -> Decision:
    args = list(argv)[1:]
    if not args:
        return _allow()
    if not _inside(cwd, scope.project):
        return _deny(DENY_OUTSIDE_WORKTREE, f"refusing gh in {cwd}")
    group = args[0]
    action = next((item for item in args[1:] if not item.startswith("-")), "")

    if (group, action) == ("pr", "create"):
        for index, item in enumerate(args):
            if item == "--base" and index + 1 < len(args):
                if args[index + 1] not in {scope.base_branch, scope.base}:
                    return _deny(DENY_GH_ENDPOINT,
                                 f"delivery opens pull requests against {scope.base_branch!r}, "
                                 f"not {args[index + 1]!r}")
        return _allow()
    if (group, action) in GH_WRITE or (group, action) in GH_READ:
        return _allow()
    if group == "api":
        return _deny(DENY_GH_ENDPOINT, "delivery does not call the API directly")
    return _deny(DENY_GH_SUBCOMMAND, f"gh {group} {action} is outside delivery")


def decide(argv: Sequence[str], env: Mapping[str, str], cwd: str | Path,
           scope: DeliveryScope) -> Decision:
    """Decide any declared step. Unknown programs are refused, not assumed safe."""
    if not argv:
        return _allow()
    program = Path(argv[0]).name
    if program == "git":
        return decide_git(argv, env, cwd, scope)
    if program.startswith("gh"):
        return decide_gh(argv, env, cwd, scope)
    if program in PROGRAMS:
        if not _inside(cwd, scope.project):
            return _deny(DENY_OUTSIDE_WORKTREE, f"refusing {program} in {cwd}")
        return _allow()
    return _deny(DENY_PROGRAM,
                 f"{program!r} is not a build tool delivery recognises; declare the work "
                 "through the project's own task runner")
