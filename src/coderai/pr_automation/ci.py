"""Failing-check evidence for a wake (§63).

Bounded and redacted: CI logs are the most likely place for a token to appear in
plain text, and this text goes straight into a prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from coderai.pr_automation.github import Gh, GhError
from coderai.pr_automation.snapshot import Check

MAX_LOG = 12_000
MAX_CHECKS = 5
REDACTED = "[redacted]"

# Secret shapes worth refusing to forward into a prompt.
_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:authorization|bearer)\b[:=]?\s+[A-Za-z0-9._~+/-]{16,}=*"),
    re.compile(r"(?i)\b[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|APIKEY|API_KEY)[A-Z0-9_]*"
               r"\s*[:=]\s*\S+"),
)

_RUN_ID = re.compile(r"/actions/runs/(\d+)")


def redact(text: str) -> str:
    """Remove credential-shaped strings before any log reaches a model."""
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


@dataclass
class CheckEvidence:
    name: str
    workflow: str = ""
    url: str = ""
    required: bool = False
    log: str = ""
    log_error: str = ""

    def render(self) -> str:
        lines = [f"### {self.name}" + (" (required)" if self.required else ""),
                 f"workflow: {self.workflow}" if self.workflow else "",
                 f"url: {self.url}" if self.url else ""]
        if self.log:
            lines += ["", "```", self.log, "```"]
        elif self.log_error:
            lines += ["", f"(log unavailable: {self.log_error})"]
        return "\n".join(line for line in lines if line)


@dataclass
class CiReport:
    state: str
    failed: list[CheckEvidence] = field(default_factory=list)
    truncated: bool = False

    def render(self) -> str:
        if not self.failed:
            return f"CI: {self.state}"
        body = "\n\n".join(item.render() for item in self.failed)
        head = f"## Failing checks ({len(self.failed)})"
        if self.truncated:
            head += " — more failures exist than are shown"
        return f"{head}\n\n{body}"


def _run_id(url: str) -> str:
    match = _RUN_ID.search(url or "")
    return match.group(1) if match else ""


def gather(gh: Gh, checks: Iterable[Check], *, ci_state: str = "failed",
           max_checks: int = MAX_CHECKS, fetch_logs: bool = True) -> CiReport:
    """Collect bounded, redacted evidence for the checks that actually failed."""
    failures = [check for check in checks if check.failed]
    # Required checks first: an optional failure is rarely why the PR is stuck.
    failures.sort(key=lambda check: (not check.required, check.name))
    report = CiReport(state=ci_state, truncated=len(failures) > max_checks)

    for check in failures[:max_checks]:
        evidence = CheckEvidence(name=check.name, workflow=check.workflow, url=check.url,
                                 required=check.required)
        run = _run_id(check.url)
        if fetch_logs and run:
            try:
                result = gh.run("run", "view", run, "--log-failed", timeout=120)
                evidence.log = redact(result.stdout)[-MAX_LOG:]
            except GhError as error:
                evidence.log_error = str(error)[:300]
        report.failed.append(evidence)
    return report
