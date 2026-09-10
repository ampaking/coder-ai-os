"""The allow/deny matrix for a PR-automation session (§14, §15).

Pure decision functions: argv + environment + cwd + session scope in, a verdict
with a named reason out. No subprocesses, no filesystem writes — so every escape
vector can be unit-tested exhaustively.

The AI decides which git operation to attempt. This decides which are possible.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

# ---- deny codes (stable; audit records them) ------------------------------
DENY_GLOBAL_OPTION = "DENY_GLOBAL_OPTION"
DENY_ENV = "DENY_ENV"
DENY_OUTSIDE_WORKTREE = "DENY_OUTSIDE_WORKTREE"
DENY_SUBCOMMAND = "DENY_SUBCOMMAND"
DENY_FORCE_PUSH = "DENY_FORCE_PUSH"
DENY_PUSH_REMOTE = "DENY_PUSH_REMOTE"
DENY_PUSH_REF = "DENY_PUSH_REF"
DENY_DELETE_REF = "DENY_DELETE_REF"
DENY_PUSH_TAGS = "DENY_PUSH_TAGS"
DENY_PUSH_ALL = "DENY_PUSH_ALL"
DENY_NO_VERIFY = "DENY_NO_VERIFY"
DENY_CONFIG_WRITE = "DENY_CONFIG_WRITE"
DENY_FETCH_REFSPEC = "DENY_FETCH_REFSPEC"
DENY_CROSS_REPO_PUSH = "DENY_CROSS_REPO_PUSH"
DENY_GH_SUBCOMMAND = "DENY_GH_SUBCOMMAND"
DENY_GH_METHOD = "DENY_GH_METHOD"
DENY_GH_ENDPOINT = "DENY_GH_ENDPOINT"
DENY_MALFORMED = "DENY_MALFORMED"
ALLOW = "ALLOW"

# git subcommands an autonomous PR engineer needs, and nothing more.
GIT_ALLOWED = {
    "status", "diff", "log", "show", "add", "commit", "fetch", "push", "restore",
    "rm", "mv", "blame", "shortlog", "rev-parse", "rev-list", "ls-files", "ls-tree",
    "cat-file", "describe", "grep", "stash", "apply", "diff-tree", "merge-base",
    "symbolic-ref", "check-ignore", "count-objects", "whatchanged", "notes",
    "config",  # reads only — _decide_config refuses every write form
}
# Denied with a specific explanation rather than a generic "unknown subcommand".
GIT_DENIED = {
    "merge": "merging is a human decision; coder-ai-os never merges",
    "rebase": "rebasing rewrites history the PR branch already published",
    "reset": "reset can discard commits; use git restore for file-level undo",
    "checkout": "the worktree must stay on the PR branch; use git restore for files",
    "switch": "the worktree must stay on the PR branch",
    "cherry-pick": "history grafting is a human decision",
    "revert": "reverting published commits is a human decision",
    "branch": "branch creation/deletion is outside the PR scope",
    "tag": "tags are release artifacts, not PR repairs",
    "remote": "remote configuration is outside the PR scope",
    "worktree": "coder-ai-os owns the worktree lifecycle",
    "clean": "clean deletes untracked files, including the engineer's",
    "gc": "repository maintenance is outside the PR scope",
    "filter-branch": "history rewriting is never permitted",
    "replace": "object replacement is never permitted",
    "update-ref": "direct ref writes bypass the push boundary",
    "fast-import": "direct object import bypasses the push boundary",
    "send-pack": "low-level push bypasses the push boundary",
    "receive-pack": "low-level push bypasses the push boundary",
    "daemon": "serving the repository is never permitted",
    "credential": "credential access is never permitted",
    "submodule": "submodule updates are outside the PR scope",
    "bisect": "bisect moves HEAD off the PR branch",
    "am": "patch application by mail is outside the PR scope",
    "pull": "pull merges or rebases; coder-ai-os refreshes the worktree itself",
    "init": "creating repositories is outside the PR scope",
    "clone": "cloning is outside the PR scope",
}

# git options that redirect where git operates, or inject configuration.
GIT_GLOBAL_DENIED = ("-c", "--config-env", "--git-dir", "--work-tree", "-C",
                     "--exec-path", "--namespace", "--no-replace-objects")
# environment variables that would relocate or reconfigure git behind the guard.
ENV_DENIED = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT", "GIT_SSH_COMMAND", "GIT_SSH",
    "GIT_PROXY_COMMAND", "GIT_EXTERNAL_DIFF", "GIT_ALLOW_PROTOCOL",
    "GIT_NAMESPACE", "GIT_EXEC_PATH", "GIT_TEMPLATE_DIR",
)

PUSH_FORCE_FLAGS = ("--force", "-f", "--force-with-lease", "--force-if-includes")
PUSH_DENIED_FLAGS = {
    "--mirror": DENY_PUSH_ALL, "--all": DENY_PUSH_ALL, "--tags": DENY_PUSH_TAGS,
    "--follow-tags": DENY_PUSH_TAGS, "--delete": DENY_DELETE_REF, "-d": DENY_DELETE_REF,
    "--prune": DENY_DELETE_REF, "--receive-pack": DENY_PUSH_REF, "--exec": DENY_PUSH_REF,
    "--repo": DENY_PUSH_REMOTE,
}

# gh: read freely; write only PR discussion.
GH_READ_ONLY = {
    ("pr", "view"), ("pr", "list"), ("pr", "diff"), ("pr", "checks"), ("pr", "status"),
    ("issue", "view"), ("issue", "list"), ("run", "view"), ("run", "list"),
    ("repo", "view"), ("api", ""), ("auth", "status"), ("search", "issues"),
    ("search", "prs"), ("label", "list"), ("release", "list"),
}
GH_WRITE_ALLOWED = {("pr", "comment"), ("issue", "comment"), ("pr", "review")}
GH_API_WRITE_PATHS = (
    re.compile(r"^/?repos/[^/]+/[^/]+/issues/\d+/comments/?$"),
    re.compile(r"^/?repos/[^/]+/[^/]+/pulls/\d+/comments/?$"),
    re.compile(r"^/?repos/[^/]+/[^/]+/pulls/\d+/reviews/?$"),
    re.compile(r"^/?repos/[^/]+/[^/]+/pulls/comments/\d+/replies/?$"),
    re.compile(r"^/?repos/[^/]+/[^/]+/issues/comments/\d+/?$"),
)
GH_WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
# GraphQL is one endpoint for everything, so it is judged by the operation it asks
# for: reads are free; the only writes permitted are marking a review thread
# resolved or unresolved, which is PR discussion and fully reversible.
GH_GRAPHQL_MUTATIONS = {"resolvereviewthread", "unresolvereviewthread"}
_GRAPHQL_FIELD = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*[({]")


@dataclass(frozen=True)
class GuardConfig:
    """The exact scope one PR session is authorized to act in."""

    worktree: str
    remote: str
    head_branch: str
    local_branch: str
    session_dir: str = ""
    allow_push: bool = True

    @property
    def head_refs(self) -> tuple[str, ...]:
        return (self.head_branch, f"refs/heads/{self.head_branch}")

    @property
    def sources(self) -> tuple[str, ...]:
        return (self.local_branch, f"refs/heads/{self.local_branch}", "HEAD", "@")

    def to_dict(self) -> dict[str, object]:
        return {
            "worktree": self.worktree, "remote": self.remote,
            "head_branch": self.head_branch, "local_branch": self.local_branch,
            "session_dir": self.session_dir, "allow_push": self.allow_push,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "GuardConfig":
        return cls(
            worktree=str(value.get("worktree", "")), remote=str(value.get("remote", "")),
            head_branch=str(value.get("head_branch", "")),
            local_branch=str(value.get("local_branch", "")),
            session_dir=str(value.get("session_dir", "")),
            allow_push=bool(value.get("allow_push", True)),
        )


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


def _allow() -> Decision:
    return Decision(True, ALLOW)


def _deny(code: str, reason: str) -> Decision:
    return Decision(False, code, reason)


def _inside(cwd: str | Path, worktree: str | Path) -> bool:
    try:
        current = Path(cwd).resolve()
        root = Path(worktree).resolve()
    except OSError:
        return False
    return current == root or root in current.parents


def _check_environment(env: Mapping[str, str]) -> Decision:
    for name in ENV_DENIED:
        if env.get(name):
            return _deny(
                DENY_ENV,
                f"{name} is set; it would relocate or reconfigure git outside the PR worktree",
            )
    return _allow()


def _split_global_options(args: Sequence[str]) -> tuple[Decision, list[str]]:
    """Consume git's pre-subcommand options, refusing the ones that redirect it."""
    rest = list(args)
    while rest:
        item = rest[0]
        if not item.startswith("-"):
            break
        name = item.split("=", 1)[0]
        if name in GIT_GLOBAL_DENIED:
            return _deny(
                DENY_GLOBAL_OPTION,
                f"git {name} can redirect git outside the PR worktree or inject configuration",
            ), []
        if item in {"--no-pager", "-p", "--paginate", "--literal-pathspecs", "--bare"}:
            if item == "--bare":
                return _deny(DENY_GLOBAL_OPTION, "git --bare is not permitted"), []
            rest.pop(0)
            continue
        rest.pop(0)
    return _allow(), rest


def _decide_push(args: Sequence[str], config: GuardConfig) -> Decision:
    if not config.allow_push:
        return _deny(
            DENY_CROSS_REPO_PUSH,
            "this PR's head branch lives in another repository; coder-ai-os cannot push to it",
        )
    positional: list[str] = []
    for item in args:
        name = item.split("=", 1)[0]
        if item in PUSH_FORCE_FLAGS or name in PUSH_FORCE_FLAGS:
            return _deny(DENY_FORCE_PUSH,
                         f"{name} would overwrite published history on the PR branch")
        if name in PUSH_DENIED_FLAGS:
            return _deny(PUSH_DENIED_FLAGS[name], f"git push {name} is outside the PR scope")
        if item.startswith("-"):
            if item in {"-u", "--set-upstream", "--quiet", "-q", "--verbose", "-v",
                        "--porcelain", "--atomic", "--no-verify", "--dry-run", "-n",
                        "--progress", "--no-progress", "--verify"}:
                if item == "--no-verify":
                    return _deny(DENY_NO_VERIFY,
                                 "--no-verify skips the pre-push guard hook")
                continue
            return _deny(DENY_PUSH_REF, f"git push {item} is not permitted")
        positional.append(item)

    if not positional:
        return _allow()  # bare `git push` — push.default=upstream pins the destination

    remote, refspecs = positional[0], positional[1:]
    if remote != config.remote:
        return _deny(DENY_PUSH_REMOTE,
                     f"pushes are scoped to the remote {config.remote!r}, not {remote!r}")
    if not refspecs:
        return _allow()
    for refspec in refspecs:
        verdict = _check_refspec(refspec, config)
        if not verdict:
            return verdict
    return _allow()


def _check_refspec(refspec: str, config: GuardConfig) -> Decision:
    if refspec.startswith("+"):
        return _deny(DENY_FORCE_PUSH, f"the '+' in {refspec!r} means a force update")
    if refspec.startswith(":"):
        return _deny(DENY_DELETE_REF, f"{refspec!r} would delete a remote branch")
    if ":" in refspec:
        source, _, destination = refspec.partition(":")
        if not destination:
            return _deny(DENY_DELETE_REF, f"{refspec!r} would delete a remote ref")
        if destination not in config.head_refs:
            return _deny(
                DENY_PUSH_REF,
                f"pushes are scoped to {config.head_branch!r}; {refspec!r} targets "
                f"{destination!r}",
            )
        if source not in config.sources:
            return _deny(DENY_PUSH_REF,
                         f"{source!r} is not this session's branch ({config.local_branch})")
        return _allow()
    if refspec in config.sources or refspec in config.head_refs:
        return _allow()
    return _deny(DENY_PUSH_REF,
                 f"pushes are scoped to {config.head_branch!r}; {refspec!r} is another ref")


def _decide_commit(args: Sequence[str]) -> Decision:
    for item in args:
        if item == "--no-verify" or item == "-n":
            return _deny(DENY_NO_VERIFY,
                         "--no-verify skips repository hooks; checks may not be disabled")
    return _allow()


def _decide_config(args: Sequence[str]) -> Decision:
    read_only = {"--get", "--get-all", "--get-regexp", "--list", "-l", "--get-urlmatch"}
    if any(item in read_only for item in args):
        return _allow()
    return _deny(DENY_CONFIG_WRITE,
                 "git config writes could re-point the remote or disable the guard")


def _decide_fetch(args: Sequence[str]) -> Decision:
    for item in args:
        if item.startswith("-"):
            continue
        if ":" in item:
            return _deny(DENY_FETCH_REFSPEC,
                         f"fetch refspec {item!r} writes local refs; fetch without a refspec")
    return _allow()


def decide_git(argv: Sequence[str], env: Mapping[str, str], cwd: str | Path,
               config: GuardConfig) -> Decision:
    """Decide one `git ...` invocation inside a PR-automation session."""
    args = list(argv)[1:]
    if not args:
        return _allow()  # bare `git` prints usage

    verdict = _check_environment(env)
    if not verdict:
        return verdict
    if not _inside(cwd, config.worktree):
        return _deny(
            DENY_OUTSIDE_WORKTREE,
            f"PR automation runs only inside {config.worktree}; refusing git in {cwd}",
        )

    verdict, rest = _split_global_options(args)
    if not verdict:
        return verdict
    if not rest:
        return _allow()

    subcommand, options = rest[0], rest[1:]
    if subcommand in GIT_DENIED:
        return _deny(DENY_SUBCOMMAND, f"git {subcommand}: {GIT_DENIED[subcommand]}")
    if subcommand not in GIT_ALLOWED:
        return _deny(DENY_SUBCOMMAND,
                     f"git {subcommand} is not part of the PR-automation profile")
    if subcommand == "push":
        return _decide_push(options, config)
    if subcommand == "commit":
        return _decide_commit(options)
    if subcommand == "config":
        return _decide_config(options)
    if subcommand == "fetch":
        return _decide_fetch(options)
    return _allow()


def _api_method(args: Sequence[str]) -> str:
    for index, item in enumerate(args):
        if item in {"-X", "--method"} and index + 1 < len(args):
            return args[index + 1].upper()
        if item.startswith("--method="):
            return item.split("=", 1)[1].upper()
    # `gh api` with -f/-F fields defaults to POST.
    if any(item in {"-f", "-F", "--field", "--raw-field", "--input"} or
           item.startswith(("-f", "-F", "--field=", "--raw-field=", "--input="))
           for item in args):
        return "POST"
    return "GET"


def _api_endpoint(args: Sequence[str]) -> str:
    skip_next = False
    for index, item in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if item.startswith("-"):
            if item in {"-X", "--method", "-f", "-F", "--field", "--raw-field", "-H",
                        "--header", "--input", "--jq", "-q", "--template", "-t",
                        "--hostname", "--cache"}:
                skip_next = True
            continue
        return item
    return ""


def decide_gh(argv: Sequence[str], env: Mapping[str, str], cwd: str | Path,
              config: GuardConfig) -> Decision:
    """Decide one `gh ...` invocation. Read freely; write only PR discussion."""
    args = [item for item in list(argv)[1:]]
    if not args:
        return _allow()
    if not _inside(cwd, config.worktree):
        return _deny(DENY_OUTSIDE_WORKTREE,
                     f"PR automation runs only inside {config.worktree}; refusing gh in {cwd}")

    group = args[0]
    action = next((item for item in args[1:] if not item.startswith("-")), "")

    if group == "api":
        method = _api_method(args)
        endpoint = _api_endpoint(args[1:])
        path = endpoint.split("?", 1)[0].strip("/")
        if path == "graphql":
            return _decide_graphql(args)
        if method not in GH_WRITE_METHODS:
            return _allow()
        if any(pattern.match(endpoint.split("?", 1)[0]) for pattern in GH_API_WRITE_PATHS):
            return _allow()
        return _deny(DENY_GH_ENDPOINT,
                     f"gh api {method} {endpoint or '<endpoint>'} is outside PR discussion")

    if (group, action) in GH_WRITE_ALLOWED:
        if group == "pr" and action == "review":
            if any(item in {"--approve", "-a", "--request-changes", "-r"} for item in args):
                return _deny(DENY_GH_SUBCOMMAND,
                             "approving or requesting changes is a human reviewer's act")
        return _allow()
    if (group, action) in GH_READ_ONLY or (group, "") in GH_READ_ONLY:
        return _allow()
    if group in {"pr", "issue", "repo", "release", "workflow", "secret", "auth", "ssh-key",
                 "gpg-key", "alias", "extension", "gist", "run", "cache", "variable", "ruleset"}:
        return _deny(DENY_GH_SUBCOMMAND,
                     f"gh {group} {action} is outside the PR-automation profile")
    return _deny(DENY_GH_SUBCOMMAND, f"gh {group} is outside the PR-automation profile")


def _graphql_document(args: Sequence[str]) -> str:
    """The query text, however it was passed (-f query=..., --field, --raw-field)."""
    parts: list[str] = []
    for index, item in enumerate(args):
        value = ""
        if item in {"-f", "-F", "--field", "--raw-field"} and index + 1 < len(args):
            value = args[index + 1]
        elif item.startswith(("-f", "-F")) and "=" in item:
            value = item[2:]
        elif item.startswith(("--field=", "--raw-field=")):
            value = item.split("=", 1)[1]
        if value.startswith(("query=", "query ")):
            parts.append(value.split("=", 1)[1] if "=" in value else value)
        elif value:
            parts.append(value)
    return "\n".join(parts)


def _decide_graphql(args: Sequence[str]) -> Decision:
    """Reads are free; writes are limited to resolving/unresolving a review thread."""
    document = _graphql_document(args)
    if "mutation" not in document.lower():
        return _allow()

    # Every field invoked anywhere in the document must be an allowed mutation or a
    # selection on its result — so a second operation cannot ride along.
    requested = {name.lower() for name in _GRAPHQL_FIELD.findall(document)}
    disallowed = {name for name in requested
                  if name.endswith(("ref", "branch", "release", "repository", "secret"))
                  or name.startswith(("delete", "merge", "close", "create", "update",
                                      "add", "remove", "transfer", "enable", "disable"))}
    if disallowed:
        return _deny(DENY_GH_ENDPOINT,
                     f"gh api graphql mutation requests {', '.join(sorted(disallowed))}, "
                     "which is outside PR discussion")
    if not (requested & GH_GRAPHQL_MUTATIONS):
        return _deny(DENY_GH_ENDPOINT,
                     "the only GraphQL mutations permitted are resolveReviewThread and "
                     "unresolveReviewThread")
    return _allow()


def decide(argv: Sequence[str], env: Mapping[str, str], cwd: str | Path,
           config: GuardConfig) -> Decision:
    """Route by program name — the shim passes its own argv straight through."""
    program = Path(argv[0]).name if argv else ""
    if program.startswith("gh"):
        return decide_gh(argv, env, cwd, config)
    return decide_git(argv, env, cwd, config)
