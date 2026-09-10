"""`coder-ai pr <n> --dry-run` — what a session would do, without doing any of it.

Safe first contact with a real pull request: it resolves the PR, collects one
snapshot, and reports the plan. No worktree is created, no guard is installed, no
elevated environment exists, and no model is ever invoked.
"""

from __future__ import annotations

from pathlib import Path

from coderai.pr_automation import states
from coderai.pr_automation.cli import PrRun
from coderai.pr_automation.delta import compare
from coderai.pr_automation.findings import Ledger, apply_snapshot
from coderai.pr_automation.github import Gh
from coderai.pr_automation.reactions import WakeRequest, react
from coderai.pr_automation.resolve import PullRequest, resolve
from coderai.pr_automation.snapshot import Snapshot, collect
from coderai.pr_automation.state import Session, new_session, resume, worktrees_root
from coderai.pr_automation.worktree import local_branch, worktree_path


def rehearse(run: PrRun, cwd: Path, *, gh: Gh | None = None,
             state_root: Path | None = None,
             worktree_root: Path | None = None) -> str:
    """Return the report. Read-only: nothing on disk or on GitHub is changed."""
    cwd = Path(cwd)
    client = gh or Gh(cwd)
    pull_request: PullRequest = resolve(run.target, cwd, client)
    snapshot: Snapshot = collect(client, pull_request.owner, pull_request.repo,
                                 pull_request.number)

    previous, reason = resume(pull_request.owner, pull_request.repo,
                              pull_request.number, root=state_root)
    session = new_session(pull_request, provider=run.provider,
                          provider_argv=list(run.provider_argv),
                          watch_mode=run.watch.mode, watch_seconds=run.watch.seconds)
    ledger = apply_snapshot(Ledger(), snapshot)
    delta = compare(None, snapshot)
    reaction = react(delta, snapshot, session, gh=None)
    tree = worktree_path(pull_request.owner, pull_request.repo, pull_request.number,
                         root=worktree_root or worktrees_root(), create=False)

    open_findings = ledger.open_findings()
    lines = [
        "coder-ai · dry run — nothing was created, changed, or pushed",
        "",
        f"{'Pull request':<14}#{pull_request.number}  {pull_request.title}",
        f"{'Repository':<14}{pull_request.owner}/{pull_request.repo} "
        f"(remote: {pull_request.remote})",
        f"{'Branch':<14}{pull_request.head_branch} → {pull_request.base_branch}",
        f"{'HEAD':<14}{snapshot.head_sha[:12] or '—'}",
        f"{'State':<14}{states.classify(snapshot, ledger)}"
        f"{'  (draft)' if snapshot.is_draft else ''}",
        f"{'CI':<14}{snapshot.ci_state}"
        + (f" — failing: {', '.join(c.name for c in snapshot.failed_checks)}"
           if snapshot.failed_checks else ""),
        "",
        f"{'Would watch':<14}{run.watch.describe()}",
        f"{'Would use':<14}{' '.join(run.provider_argv)}",
        f"{'Worktree':<14}{tree}",
        f"{'Local branch':<14}{local_branch(pull_request.number)} "
        f"→ pushes to {pull_request.head_branch}",
        f"{'Prior session':<14}"
        + ("none — this would start fresh" if previous is None
           else f"{reason} (state {previous.state}, {previous.wake_count} wakes so far)"),
    ]

    if pull_request.cross_repository:
        lines += ["", "This PR's head branch lives in another repository (a fork), so the",
                  "session would be read-only: triage and replies, but no pushes."]

    lines += ["", f"Conversation   {len(snapshot.comments)} comment(s), "
                  f"{len(snapshot.reviews)} review(s), "
                  f"{len(snapshot.review_comments)} inline comment(s)"]
    lines.append(f"Findings       {len(ledger)} tracked, {len(open_findings)} needing attention")
    for finding in open_findings[:10]:
        where = finding.anchor() or "(no anchor)"
        excerpt = finding.excerpt.strip().splitlines()[0][:60] if finding.excerpt else ""
        lines.append(f"  {finding.id}  {finding.state:<10} {where[:38]:<38} {excerpt}")
    if len(open_findings) > 10:
        lines.append(f"  … and {len(open_findings) - 10} more")

    lines += ["", "First action"]
    if isinstance(reaction, WakeRequest):
        lines.append(f"  wake {run.provider} — {reaction.reason}: {reaction.summary}")
    else:
        lines.append(f"  stay asleep — {reaction}")

    if snapshot.truncated or snapshot.partial:
        lines += ["", "Collection notes"]
        for note in snapshot.truncated:
            lines.append(f"  capped: {note}")
        for note in snapshot.partial_reasons:
            lines.append(f"  incomplete: {note}")

    lines += ["", "Run it for real with:",
              f"  coder-ai pr {pull_request.number} --watch {run.watch.describe()} "
              f"-- {' '.join(run.provider_argv)}", ""]
    return "\n".join(lines)
