"""Read `config/pr_automation.yaml` — the one place PR automation is configured.

Only the values that are genuinely tunable are read here. The allow/deny matrix
is deliberately NOT configurable: it lives in guard/policy.py precisely so that
configuration — or a repository that edits its own instructions — cannot widen
the boundary (§25). Unsafe values are refused, not applied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "pr_automation.yaml"

DEFAULT_WATCH = 3600
DEFAULT_POLL = {"active": 45, "review": 180, "idle": 600, "ready": 900}
DEFAULT_CI_BUDGET = 3

_DURATION = re.compile(r"^(\d+)\s*(s|m|h|d)?$")


class ConfigError(ValueError):
    """A configuration value that would weaken the boundary, or make no sense."""


def parse_duration(value: Any, *, default: int) -> int:
    if value is None:
        return default
    text = str(value).strip().lower()
    match = _DURATION.match(text)
    if not match:
        raise ConfigError(f"not a duration: {value!r}")
    return int(match.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2) or "s"]


def _load_yaml(path: Path) -> dict[str, Any]:
    """Parse the small, flat subset this file uses.

    PyYAML when available; otherwise an indentation parser for nested maps of
    scalars and simple `[a, b]` lists — enough for this file and nothing more.
    """
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except ImportError:
        pass

    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip() if " #" in raw else raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        key, _, value = line.strip().partition(":")
        if not _:
            continue
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else root
        value = value.strip()
        if value == "":
            child: dict[str, Any] = {}
            parent[key.strip()] = child
            stack.append((indent, child))
        else:
            parent[key.strip()] = _scalar(value)
    return root


def _scalar(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        return [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
    lowered = value.lower()
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value.strip("'\"")


@dataclass
class PrConfig:
    watch_default: int = DEFAULT_WATCH
    watch_maximum: int = 7 * 86400
    poll: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_POLL))
    commit_identity: str = "repository"
    commit_trailers: bool = False
    cross_provider_review: str = "prefer-opposite"
    fallback_allowed: bool = True

    def poll_seconds(self, name: str) -> int:
        return int(self.poll.get(name, DEFAULT_POLL.get(name, DEFAULT_POLL["idle"])))


def validate(value: dict[str, Any]) -> None:
    """Refuse configuration that would contradict the safety model."""
    profiles = value.get("execution_profiles") or {}
    normal = profiles.get("normal") or {}
    elevated = profiles.get("pr_automation") or {}
    if normal.get("git_write") not in (False, None):
        raise ConfigError("execution_profiles.normal.git_write must stay false — "
                          "PR automation may never elevate ordinary sessions")
    for name, profile in (("normal", normal), ("pr_automation", elevated)):
        for key in ("deploy", "secrets"):
            if profile.get(key) not in (False, None):
                raise ConfigError(f"execution_profiles.{name}.{key} must stay false")
    if elevated.get("git_write") not in ("scoped", None):
        raise ConfigError("execution_profiles.pr_automation.git_write must be 'scoped'; "
                          "unscoped git write is not offered")
    commit = value.get("commit") or {}
    if commit.get("trailers") not in (False, None):
        raise ConfigError("commit.trailers must stay false — Git history carries no "
                          "AI-Agent/AI-Model metadata (§31)")


def load(path: Path | None = None) -> PrConfig:
    value = _load_yaml(Path(path) if path else CONFIG_PATH)
    validate(value)
    watch = value.get("watch") or {}
    poll = watch.get("poll") or {}
    commit = value.get("commit") or {}
    routing = value.get("routing") or {}
    return PrConfig(
        watch_default=parse_duration(watch.get("default"), default=DEFAULT_WATCH),
        watch_maximum=parse_duration(watch.get("maximum"), default=7 * 86400),
        poll={name: parse_duration(poll.get(name), default=DEFAULT_POLL[name])
              for name in DEFAULT_POLL},
        commit_identity=str(commit.get("identity") or "repository"),
        commit_trailers=bool(commit.get("trailers") or False),
        cross_provider_review=str(routing.get("cross_provider_review") or "prefer-opposite"),
        fallback_allowed=str(routing.get("fallback", "allowed")).lower() != "denied",
    )
