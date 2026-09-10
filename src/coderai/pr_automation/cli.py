"""Argument contract for `coder-ai pr` — pure parsing, no side effects.

    coder-ai pr <pr|branch|url> [coder-ai options] -- <native provider argv...>
    coder-ai pr status [<pr>] | attach <pr> | stop <pr> | log <pr>

Everything after the first standalone `--` belongs to the provider CLI and is
passed through byte-for-byte; coder-ai-os never inspects or rewrites it.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Sequence

PROVIDERS = ("claude", "codex")
DEFAULT_WATCH_SECONDS = 3600
MAX_WATCH_SECONDS = 7 * 24 * 3600
SUBCOMMANDS = ("status", "attach", "stop", "log")

USAGE = """usage:
  coder-ai pr <pr|branch|url> [--watch <duration>] [--bg] -- <provider> [provider args...]
  coder-ai pr status [<pr>] [--json]   list supervised PRs, or show one
  coder-ai pr attach <pr>        stream a running session's status view
  coder-ai pr stop <pr>          stop a session (USER_STOP) and drop elevated permission
  coder-ai pr log <pr>           render a session's audit log

  --watch <duration>   0 | 30m | 2h | 90s | 1d | until-close     (default: 1h)
  --bg                 detach and supervise in the background
  --dry-run            show what a session would do, without doing any of it

examples:
  coder-ai pr 1420 -- claude --model claude-fable-5
  coder-ai pr feature/worker-retry --watch 3h -- codex --model gpt-5.6-sol
  coder-ai pr https://github.com/org/repo/pull/1420 --bg --watch 4h -- claude"""

_DURATION = re.compile(r"^(\d+)(s|m|h|d)?$")


class CliError(ValueError):
    """A command line that cannot be interpreted safely."""


@dataclass(frozen=True)
class WatchWindow:
    """How long coder-ai-os supervises the PR.

    mode "single"      one pass over current state, then exit  (--watch 0)
    mode "bounded"     watch for `seconds`, then WATCH_TIMEOUT (default 1h)
    mode "until-close" watch until a terminal PR state
    """

    mode: str
    seconds: int | None = None

    @property
    def is_single_pass(self) -> bool:
        return self.mode == "single"

    @property
    def is_unbounded(self) -> bool:
        return self.mode == "until-close"

    def describe(self) -> str:
        if self.mode == "single":
            return "single pass"
        if self.mode == "until-close":
            return "until the PR closes"
        return _humanize(int(self.seconds or 0))


@dataclass(frozen=True)
class PrRun:
    """A request to supervise one PR with one provider command."""

    target: str
    watch: WatchWindow
    background: bool
    provider: str
    provider_argv: tuple[str, ...] = field(default_factory=tuple)
    dry_run: bool = False

    kind: str = "run"


@dataclass(frozen=True)
class PrCommand:
    """A session-management subcommand (status / attach / stop / log)."""

    name: str
    target: str | None = None
    as_json: bool = False

    kind: str = "command"


def _humanize(seconds: int) -> str:
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def parse_watch(value: str) -> WatchWindow:
    """Parse a --watch value. Raises CliError on anything unrecognized."""
    text = value.strip().lower()
    if text in {"until-close", "until_close", "untilclose"}:
        return WatchWindow("until-close")
    match = _DURATION.match(text)
    if not match:
        raise CliError(
            f"--watch does not understand {value!r}; use 0, 30m, 2h, 90s, 1d, or until-close"
        )
    amount = int(match.group(1))
    unit = match.group(2) or "s"
    seconds = amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    if seconds == 0:
        return WatchWindow("single", 0)
    if seconds > MAX_WATCH_SECONDS:
        raise CliError(
            f"--watch {value} exceeds the {_humanize(MAX_WATCH_SECONDS)} maximum; "
            "use until-close for full-lifetime supervision"
        )
    return WatchWindow("bounded", seconds)


def _split_provider_argv(argv: Sequence[str]) -> tuple[list[str], list[str] | None]:
    """Split at the FIRST standalone '--'. The tail is opaque provider argv."""
    for index, item in enumerate(argv):
        if item == "--":
            return list(argv[:index]), list(argv[index + 1:])
    return list(argv), None


def parse_args(argv: Sequence[str]) -> PrRun | PrCommand:
    """Turn `coder-ai pr` arguments into one validated invocation. No side effects."""
    rig_argv, provider_argv = _split_provider_argv(argv)

    if not rig_argv:
        raise CliError("coder-ai pr needs a PR number, branch, or PR URL\n\n" + USAGE)

    head = rig_argv[0]
    if head in SUBCOMMANDS:
        if provider_argv is not None:
            raise CliError(f"coder-ai pr {head} does not take a provider command after '--'")
        return _parse_subcommand(head, rig_argv[1:])

    if head.startswith("-"):
        raise CliError(
            f"expected a PR number, branch, or PR URL before options, got {head!r}\n\n" + USAGE
        )

    target = head
    watch = WatchWindow("bounded", DEFAULT_WATCH_SECONDS)
    background = False
    dry_run = False

    rest = rig_argv[1:]
    index = 0
    while index < len(rest):
        item = rest[index]
        if item in {"--watch", "-w"}:
            index += 1
            if index >= len(rest):
                raise CliError("--watch needs a duration (0, 30m, 2h, until-close)")
            watch = parse_watch(rest[index])
        elif item.startswith("--watch="):
            watch = parse_watch(item.split("=", 1)[1])
        elif item in {"--bg", "--background"}:
            background = True
        elif item in {"--dry-run", "--dry"}:
            dry_run = True
        elif item.startswith("-"):
            raise CliError(
                f"unknown coder-ai option {item!r}; provider options belong after '--'\n\n" + USAGE
            )
        else:
            raise CliError(
                f"unexpected argument {item!r}; the provider command belongs after '--'\n\n" + USAGE
            )
        index += 1

    if provider_argv is None:
        raise CliError(
            "missing '--' before the provider command\n\n"
            f"  coder-ai pr {target} -- claude\n"
            f"  coder-ai pr {target} --watch 2h -- codex --model gpt-5.6-sol"
        )
    if not provider_argv:
        raise CliError("'--' must be followed by a provider command, for example: -- claude")

    provider = provider_argv[0]
    if provider.startswith("-"):
        raise CliError(f"expected a provider command after '--', got the option {provider!r}")
    if provider not in PROVIDERS:
        raise CliError(
            f"unsupported provider {provider!r}; PR automation supports: {', '.join(PROVIDERS)}"
        )

    if dry_run and background:
        raise CliError("--dry-run and --bg cannot be combined: a rehearsal has "
                       "nothing to run in the background")

    return PrRun(
        target=target,
        watch=watch,
        background=background,
        provider=provider,
        provider_argv=tuple(provider_argv),
        dry_run=dry_run,
    )


def _parse_subcommand(name: str, rest: Sequence[str]) -> PrCommand:
    values = [item for item in rest if not item.startswith("-")]
    options = [item for item in rest if item.startswith("-")]
    as_json = "--json" in options
    options = [item for item in options if item != "--json"]
    if options:
        raise CliError(f"coder-ai pr {name} does not take {options[0]!r}")
    if as_json and name != "status":
        raise CliError(f"coder-ai pr {name} does not produce JSON")
    if len(values) > 1:
        raise CliError(f"coder-ai pr {name} takes at most one PR")
    if name != "status" and not values:
        raise CliError(f"coder-ai pr {name} needs a PR (for example: coder-ai pr {name} 1420)")
    return PrCommand(name=name, target=values[0] if values else None, as_json=as_json)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        print(USAGE)
        return 0
    try:
        invocation = parse_args(args)
    except CliError as error:
        print(f"coder-ai pr: {error}", file=sys.stderr)
        return 2

    from coderai.pr_automation.runner import dispatch  # deferred: keeps parsing side-effect free

    return dispatch(invocation)


if __name__ == "__main__":
    raise SystemExit(main())
