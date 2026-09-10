"""Turn CI and review events into silence or a well-evidenced wake (§61–64).

CI passing costs nothing. CI failing wakes the AI with the failing job attached.
Reviewer feedback arriving mid-CI does not wait for CI to finish.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from coderai.pr_automation import delta as delta_module
from coderai.pr_automation.ci import CiReport, gather
from coderai.pr_automation.delta import Delta
from coderai.pr_automation.github import Gh
from coderai.pr_automation.snapshot import Snapshot
from coderai.pr_automation.state import Session

# How many times coder-ai-os will wake the AI for the same failing check at the same HEAD
# before it stops and lets a person look (§63, flaky/infrastructure failures).
CI_RETRY_BUDGET = 3

SLEEP_CI_PENDING = "CI_PENDING"
SLEEP_CI_PASSED = "CI_PASSED"
SLEEP_NO_REASON = "NOTHING_TO_DO"
SLEEP_CI_BUDGET = "CI_RETRY_BUDGET_SPENT"


@dataclass
class WakeRequest:
    reason: str
    summary: str
    evidence: str = ""
    checks: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return True


def _ci_key(head_sha: str, names: list[str]) -> str:
    return f"{head_sha[:12]}:{','.join(sorted(names))}"


def react(delta: Delta, snapshot: Snapshot, session: Session, *, gh: Gh | None = None,
          fetch_logs: bool = True) -> WakeRequest | str:
    """A WakeRequest, or a named sleep reason. One decision, always explained."""
    if not delta.should_wake:
        if snapshot.ci_state == "pending":
            return SLEEP_CI_PENDING
        if delta.ci_changed and snapshot.ci_state == "passed":
            return SLEEP_CI_PASSED
        return delta.sleep_reason or SLEEP_NO_REASON

    if delta.wake_reason == delta_module.CI_FAILED:
        failed = [check for check in snapshot.failed_checks]
        required = [check for check in failed if check.required]
        relevant = required or failed
        if failed and not required and all(not check.required for check in failed):
            # An optional check failing does not, by itself, need engineering judgment.
            return SLEEP_NO_REASON

        names = [check.name for check in relevant]
        key = _ci_key(snapshot.head_sha, names)
        spent = session.ci_attempts.get(key, 0)
        if spent >= CI_RETRY_BUDGET:
            return SLEEP_CI_BUDGET
        session.ci_attempts[key] = spent + 1

        report: CiReport | None = None
        if gh is not None:
            report = gather(gh, relevant, ci_state=snapshot.ci_state,
                            fetch_logs=fetch_logs)
        summary = f"{len(relevant)} failing check(s): {', '.join(names)}"
        if spent:
            summary += f" (attempt {spent + 1} of {CI_RETRY_BUDGET})"
        return WakeRequest(reason=delta.wake_reason, summary=summary,
                           evidence=report.render() if report else "", checks=names)

    # Reviewer feedback and new commits never wait for CI to finish (§64).
    return WakeRequest(reason=delta.wake_reason, summary=delta.summary())
