"""The project's delivery contract: `.coder-ai/delivery.yaml`.

Everything downstream executes what this file says, so it must be impossible for
it to say something dangerous. Commands are split into argv at load time and
never see a shell; substitution values stay single tokens; and a declaration that
asks for a force push simply fails to load.

Enablement is deliberately NOT in this file. The contract is committed and shared
with the team; whether it may run on *this* machine lives in `.coder-ai/local/`.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FILE = "delivery.yaml"
ALT_FILE = "delivery.json"
LOCAL = "local/delivery.json"

PROTECTED = ("main", "master", "trunk", "release", "production", "develop")
SUBSTITUTIONS = ("${branch}", "${base}", "${title}", "${remote}")
FORBIDDEN_TEXT = ("|", ";", "&", "`", "$(", ">", "<", "\n", "\r", "&&", "||")
FORBIDDEN_HEAD = ("sudo", "doas", "su", "eval", "exec", "source", ".", "bash", "sh", "zsh")
TOP_LEVEL = {"enabled", "base", "branch_pattern", "remote", "steps", "issue", "pr"}
STEP_KEYS = {"name", "run", "optional"}
MAX_STEPS = 20


class DeclarationError(ValueError):
    """A declaration that cannot be trusted to describe safe work."""


@dataclass
class Step:
    name: str
    argv: list[str]
    optional: bool = False

    @property
    def program(self) -> str:
        return self.argv[0] if self.argv else ""

    def render(self, values: dict[str, str] | None = None) -> list[str]:
        """Substitute placeholders as whole argv tokens — never re-split."""
        values = values or {}
        out: list[str] = []
        for token in self.argv:
            replaced = token
            for name, value in values.items():
                replaced = replaced.replace("${" + name + "}", value)
            out.append(replaced)
        return out

    def text(self, values: dict[str, str] | None = None) -> str:
        return " ".join(self.render(values))


@dataclass
class Delivery:
    base: str
    branch_pattern: str
    steps: list[Step] = field(default_factory=list)
    remote: str = "origin"
    path: str = ""

    @property
    def base_branch(self) -> str:
        return self.base.split("/", 1)[1] if "/" in self.base else self.base

    def matches_branch(self, branch: str) -> bool:
        return bool(re.match(self.branch_pattern, branch or ""))

    def step(self, name: str) -> Step | None:
        return next((item for item in self.steps if item.name == name), None)


# ---------------------------------------------------------------- parsing


def _parse_scalar(value: str) -> Any:
    text = value.strip()
    if text.startswith(("'", '"')) and text.endswith(("'", '"')) and len(text) > 1:
        return text[1:-1]
    lowered = text.lower()
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def _parse_yaml(text: str) -> dict[str, Any]:
    """The documented subset: a `delivery:` map whose `steps:` is a list of maps."""
    try:
        import yaml  # type: ignore

        value = yaml.safe_load(text) or {}
        return value if isinstance(value, dict) else {}
    except ImportError:
        pass

    root: dict[str, Any] = {}
    section: dict[str, Any] | None = None
    steps: list[dict[str, Any]] | None = None
    current: dict[str, Any] | None = None

    for raw in text.splitlines():
        line = raw.split(" #", 1)[0] if " #" in raw else raw
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()

        if indent == 0 and body.endswith(":"):
            section = {}
            root[body[:-1].strip()] = section
            steps, current = None, None
            continue
        if section is None:
            continue
        if body.startswith("- "):
            if steps is None:
                continue
            item = body[2:].strip()
            if item.startswith("{") and item.endswith("}"):
                current = {}
                for pair in item[1:-1].split(","):
                    if ":" in pair:
                        key, _, value = pair.partition(":")
                        current[key.strip()] = _parse_scalar(value)
                steps.append(current)
                current = None
            else:
                current = {}
                key, _, value = item.partition(":")
                if _:
                    current[key.strip()] = _parse_scalar(value)
                steps.append(current)
            continue
        key, _, value = body.partition(":")
        if not _:
            continue
        key, value = key.strip(), value.strip()
        if value == "":
            if key == "steps":
                steps = []
                section["steps"] = steps
                current = None
            continue
        if current is not None and steps is not None and indent >= 4:
            current[key] = _parse_scalar(value)
        else:
            section[key] = _parse_scalar(value)
            steps = None if key != "steps" else steps
    return root


# ---------------------------------------------------------------- validation


def _validate_command(name: str, raw: str) -> list[str]:
    if not isinstance(raw, str) or not raw.strip():
        raise DeclarationError(f"step {name!r} has no command")
    for token in FORBIDDEN_TEXT:
        if token in raw:
            raise DeclarationError(
                f"step {name!r} contains {token!r}; delivery steps are single commands, "
                "never shell expressions")
    try:
        argv = shlex.split(raw)
    except ValueError as error:
        raise DeclarationError(f"step {name!r} cannot be parsed: {error}") from error
    if not argv:
        raise DeclarationError(f"step {name!r} has no command")
    if argv[0] in FORBIDDEN_HEAD:
        raise DeclarationError(f"step {name!r} runs {argv[0]!r}, which is never permitted")
    for token in argv:
        if token.startswith("${") and token not in SUBSTITUTIONS:
            raise DeclarationError(f"step {name!r} uses unknown substitution {token}")
    return argv


def validate(value: dict[str, Any]) -> Delivery:
    """Turn a parsed document into a Delivery, or refuse it with a reason."""
    section = value.get("delivery") if isinstance(value.get("delivery"), dict) else value
    if not isinstance(section, dict) or not section:
        raise DeclarationError("no `delivery:` section")
    unknown = set(section) - TOP_LEVEL
    if unknown:
        raise DeclarationError(f"unknown key(s): {', '.join(sorted(unknown))}")

    base = str(section.get("base") or "").strip()
    if not base:
        raise DeclarationError("`base` is required, for example origin/develop")
    pattern = str(section.get("branch_pattern") or "").strip()
    if not pattern:
        raise DeclarationError("`branch_pattern` is required")
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        raise DeclarationError(f"branch_pattern is not a valid regex: {error}") from error
    base_branch = base.split("/", 1)[1] if "/" in base else base
    for protected in {*PROTECTED, base_branch}:
        if compiled.match(protected):
            raise DeclarationError(
                f"branch_pattern matches {protected!r}; delivery may never target a "
                "protected branch")

    raw_steps = section.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise DeclarationError("`steps` must be a non-empty list")
    if len(raw_steps) > MAX_STEPS:
        raise DeclarationError(f"more than {MAX_STEPS} steps is not a workflow, it is a script")

    steps: list[Step] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_steps, 1):
        if not isinstance(item, dict):
            raise DeclarationError(f"step {index} is not a mapping")
        extra = set(item) - STEP_KEYS
        if extra:
            raise DeclarationError(f"step {index} has unknown key(s): {', '.join(sorted(extra))}")
        name = str(item.get("name") or f"step-{index}").strip()
        if name in seen:
            raise DeclarationError(f"duplicate step name {name!r}")
        seen.add(name)
        steps.append(Step(name=name, argv=_validate_command(name, item.get("run", "")),
                          optional=bool(item.get("optional"))))

    return Delivery(base=base, branch_pattern=pattern, steps=steps,
                    remote=str(section.get("remote") or base.split("/", 1)[0] or "origin"))


def load(project: Path) -> Delivery | None:
    """The project's contract, or None when it declares nothing."""
    project = Path(project)
    for name in (FILE, ALT_FILE):
        path = project / ".coder-ai" / name
        if not path.is_file() or path.is_symlink():
            continue
        text = path.read_text(encoding="utf-8")
        value = (json.loads(text) if name.endswith(".json") else _parse_yaml(text))
        delivery = validate(value if isinstance(value, dict) else {})
        delivery.path = str(path.relative_to(project))
        return delivery
    return None


def is_enabled(project: Path) -> bool:
    """Whether THIS machine agreed to run it. A committed `enabled: true` is not consent."""
    path = Path(project) / ".coder-ai" / LOCAL
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(isinstance(value, dict) and value.get("enabled"))
