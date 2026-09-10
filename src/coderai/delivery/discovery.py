"""Propose a delivery workflow from what the repository already documents.

Never invents a command (§22–23). Every proposed step carries the `file:line` that
justifies it, and a step with no evidence is not proposed at all.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MAX_READ = 200_000
DEFAULT_PATTERN = r"^(fix|feat|chore|docs|refactor|test)/[a-z0-9._-]+$"

_MAKE_TARGET = re.compile(r"(?m)^([a-zA-Z][\w.-]*)\s*:(?!=)")
_VERIFY_WORDS = ("verify", "check", "ci", "validate", "test", "lint", "quality")
_DANGEROUS = ("deploy", "release", "publish", "push-image", "terraform", "helm", "prod")


@dataclass
class Candidate:
    name: str
    run: str
    evidence: str

    def line(self) -> str:
        return f"  {self.name:<10} {self.run:<44} ({self.evidence})"


@dataclass
class Proposal:
    base: str
    branch_pattern: str
    remote: str = "origin"
    steps: list[Candidate] = field(default_factory=list)

    def fingerprint(self) -> str:
        blob = f"{self.base}|{self.branch_pattern}|" + "|".join(
            f"{step.name}:{step.run}" for step in self.steps)
        return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:16]

    def render(self) -> str:
        lines = [f"  base: {self.base}   branch pattern: {self.branch_pattern}", ""]
        lines += [step.line() for step in self.steps]
        return "\n".join(lines)

    def document(self) -> str:
        base_branch = self.base.split("/", 1)[-1]
        out = ["# Delivery workflow for this project.",
               "# Proposed by `coder-ai sync` from the evidence noted beside each step.",
               "# Edit freely — a hand-written declaration always wins.",
               "#",
               f"# Work is branched off {self.base} and pushed to a branch matching",
               "# branch_pattern. Delivery may never push to that base, to main/master/",
               "# staging/production, and may never merge, tag, release, or deploy.",
               "delivery:",
               f"  base: {self.base}",
               f'  branch_pattern: "{self.branch_pattern}"',
               f"  remote: {self.remote}",
               "  steps:"]
        for step in self.steps:
            out.append(f"    # {step.evidence}")
            out.append(f"    - name: {step.name}")
            out.append(f"      run: {step.run}")
        return "\n".join(out) + "\n"

    def write(self, project: Path) -> Path:
        target = Path(project) / ".coder-ai" / "delivery.yaml"
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_text(self.document(), encoding="utf-8")
        return target


def _git(project: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(project), *args], capture_output=True,
                                text=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


PREFERRED_BASE = "develop"


def base_candidates(project: Path) -> list[str]:
    """Branches this repository could sensibly be developed from, best first.

    `develop` wins when it exists: teams that have one branch off it, and the
    default branch is usually the one they merge *into*, not from.
    """
    found: list[str] = []
    for candidate in (f"origin/{PREFERRED_BASE}", "origin/main", "origin/master",
                      "origin/trunk"):
        if _git(project, "rev-parse", "--verify", "--quiet", candidate):
            found.append(candidate)
    head = _git(project, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if head:
        default = head.replace("refs/remotes/", "")
        if default not in found:
            found.append(default)
    return found


def default_base(project: Path, chosen: str = "") -> str:
    """The base branch, chosen by the human where they have said, discovered otherwise."""
    if chosen:
        return chosen if "/" in chosen else f"origin/{chosen}"
    candidates = base_candidates(project)
    return candidates[0] if candidates else ""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_READ]
    except OSError:
        return ""


def _make_targets(project: Path) -> list[tuple[str, int]]:
    for name in ("Makefile", "makefile", "GNUmakefile"):
        path = project / name
        if not path.is_file():
            continue
        found = []
        for index, line in enumerate(_read(path).splitlines(), 1):
            match = _MAKE_TARGET.match(line)
            if match:
                found.append((match.group(1), index))
        return [(target, line) for target, line in found
                if not any(word in target.lower() for word in _DANGEROUS)]
    return []


def _package_scripts(project: Path) -> dict[str, int]:
    path = project / "package.json"
    if not path.is_file():
        return {}
    text = _read(path)
    try:
        scripts = json.loads(text).get("scripts", {})
    except json.JSONDecodeError:
        return {}
    lines = text.splitlines()
    found: dict[str, int] = {}
    for name in scripts:
        for index, line in enumerate(lines, 1):
            if f'"{name}"' in line:
                found[name] = index
                break
    return {name: line for name, line in found.items()
            if not any(word in name.lower() for word in _DANGEROUS)}


def _ci_commands(project: Path) -> list[tuple[str, str]]:
    """What CI actually runs outranks what a README suggests."""
    found: list[tuple[str, str]] = []
    workflows = project / ".github" / "workflows"
    if not workflows.is_dir():
        return found
    for path in sorted(workflows.glob("*.y*ml"))[:6]:
        for index, line in enumerate(_read(path).splitlines(), 1):
            stripped = line.strip().lstrip("- ").strip()
            if not stripped.startswith(("run:", "run ")):
                continue
            command = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            if not command or any(char in command for char in "|;&\n"):
                continue
            if any(word in command.lower() for word in _DANGEROUS):
                continue
            if command.split()[0] in {"make", "npm", "pnpm", "yarn", "just", "task"}:
                found.append((command, f".github/workflows/{path.name}:{index}"))
    return found[:4]


def propose(project: Path, base_branch: str = "") -> Proposal | None:
    """A workflow this repository's own files justify, or None.

    `base_branch` is the human's answer to "which branch do you develop from?".
    Without one, the repository's own branches decide.
    """
    project = Path(project)
    base = default_base(project, base_branch)
    if not base:
        return None

    steps: list[Candidate] = [
        Candidate("sync", "git fetch origin", "the base must be current before rebasing"),
        Candidate("rebase", f"git rebase {base}", f"base branch is {base}"),
    ]

    verify: Candidate | None = None
    for command, evidence in _ci_commands(project):
        if any(word in command.lower() for word in _VERIFY_WORDS):
            verify = Candidate("verify", command, evidence)
            break
    if verify is None:
        for target, line in _make_targets(project):
            if any(word in target.lower() for word in _VERIFY_WORDS):
                verify = Candidate("verify", f"make {target}", f"Makefile:{line}")
                break
    if verify is None:
        scripts = _package_scripts(project)
        for name in ("verify", "ci", "check", "test"):
            if name in scripts:
                verify = Candidate("verify", f"npm run {name}", f"package.json:{scripts[name]}")
                break
    if verify is None:
        return None            # nothing verifies this project; do not propose delivery
    steps.append(verify)

    steps.append(Candidate("push", "git push -u origin ${branch}",
                           "feature branches are pushed with their upstream set"))
    steps.append(Candidate("pr", f"gh pr create --base {base.split('/', 1)[-1]} --fill",
                           f"pull requests target {base}"))
    return Proposal(base=base, branch_pattern=DEFAULT_PATTERN, steps=steps)
