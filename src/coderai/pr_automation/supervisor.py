"""End-to-end supervision of one Pull Request — the §77 production flow.

    resolve -> worktree -> session -> guard -> notice -> watch
      -> (meaningful change -> wake AI -> decision -> verify push) -> watch
      -> terminal: persist, drop elevated permission, archive
"""

from __future__ import annotations

import dataclasses
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from coderai.pr_automation import audit, daemon, findings as findings_module, providers, states
from coderai.pr_automation import push as push_module
from coderai.pr_automation import decision as decision_module
from coderai.pr_automation.cli import PrRun
from coderai.pr_automation.delta import Delta
from coderai.pr_automation.findings import Ledger
from coderai.pr_automation.github import Gh
from coderai.pr_automation.gitcmd import Git
from coderai.pr_automation.guard import install as guard_install
from coderai.pr_automation.notice import permission_notice, watch_expired_report
from coderai.pr_automation.reactions import WakeRequest, react
from coderai.pr_automation.resolve import PullRequest, ResolveError, resolve
from coderai.pr_automation.snapshot import Snapshot, collect
from coderai.pr_automation.state import (
    Session, SessionLock, StateError, ensure_dir, list_sessions, new_session,
    save_session, session_dir, sessions_root,
)
from coderai.pr_automation.wake import REVIEW_INSTRUCTIONS, RunOutcome, wake
from coderai.pr_automation.watch import RealClock, WatchResult, run_watch
from coderai.pr_automation.worktree import ensure_worktree, release_worktree


class SupervisorError(RuntimeError):
    """The session cannot start or continue safely."""


@dataclass
class SessionReport:
    session: Session
    result: WatchResult | None = None
    outcomes: list[RunOutcome] = field(default_factory=list)
    pushes: list[push_module.PushVerification] = field(default_factory=list)
    notice: str = ""

    @property
    def reason(self) -> str:
        return self.result.reason if self.result else ""


def _merge_session(previous: Session | None, pull_request: PullRequest, run: PrRun,
                   now: float) -> Session:
    """Resume prior context where it exists; never re-derive it from zero (§73)."""
    session = new_session(
        pull_request, provider=run.provider, provider_argv=list(run.provider_argv),
        watch_mode=run.watch.mode, watch_seconds=run.watch.seconds,
        background=run.background, now=now,
    )
    if previous is not None:
        session.created_at = previous.created_at or session.created_at
        session.wake_count = previous.wake_count
        session.last_push_sha = previous.last_push_sha
        session.last_push_at = previous.last_push_at
        session.last_snapshot_digest = previous.last_snapshot_digest
        session.last_ci_state = previous.last_ci_state
        session.ci_attempts = dict(previous.ci_attempts)
        session.source_repo = previous.source_repo
    return session


def run_session(run: PrRun, cwd: Path, *, gh: Gh | None = None,
                state_root: Path | None = None, worktree_root: Path | None = None,
                clock=None, out: Callable[[str], None] = print,
                runner=None, timeout: int = 3600) -> SessionReport:
    """Supervise one PR for one watch window."""
    cwd = Path(cwd)
    client = gh or Gh(cwd)
    try:
        pull_request = resolve(run.target, cwd, client)
    except ResolveError as error:
        raise SupervisorError(f"{error.reason}: {error.message}") from error

    now = (clock or RealClock()).now()
    directory = ensure_dir(session_dir(pull_request.owner, pull_request.repo,
                                       pull_request.number, root=state_root), state_root)
    from coderai.pr_automation.state import resume as resume_session

    previous, _reason = resume_session(pull_request.owner, pull_request.repo,
                                       pull_request.number, root=state_root)
    session = _merge_session(previous, pull_request, run, now)

    with SessionLock(directory, root=state_root):
        # A stop request from a previous session must not stop this one.
        daemon.clear_stop(directory)
        daemon.write_pid(directory)
        tree = ensure_worktree(session, cwd, root=worktree_root,
                               cross_repository=pull_request.cross_repository,
                               session_directory=directory)
        session.worktree = str(tree.path)
        session.pid = os.getpid()
        save_session(session, root=state_root)

        guard = guard_install.install(directory, tree.path, remote=session.remote,
                                      head_branch=session.head_branch,
                                      local_branch=tree.branch,
                                      allow_push=tree.push_supported)
        notice = permission_notice(session, str(tree.path), allow_push=tree.push_supported)
        out(notice)
        audit.append(directory, "session_started", pr=session.number,
                     provider=session.provider, worktree=str(tree.path),
                     watch=session.watch_mode, resumed=previous is not None)

        ledger = findings_module.load(directory)
        findings_module.recover_in_flight(ledger)
        health = providers.detect(providers.load(directory))
        providers.save(directory, health)

        report = SessionReport(session=session, notice=notice)
        git = Git(tree.path)
        # What WE published. Both sets are shared by reference with the watch loop,
        # so a commit we pushed or a reply we posted never wakes us as if a human
        # had done it.
        own_heads: set[str] = {session.last_push_sha} if session.last_push_sha else set()
        own_comments: set[str] = set()
        identity = _gh_identity(client, session)

        latest: dict[str, Snapshot] = {}

        def collect_snapshot() -> Snapshot:
            snapshot = collect(client, session.owner, session.repo, session.number)
            latest["snapshot"] = snapshot
            return snapshot

        def record_own_comments(before: set[str]) -> None:
            """Anything the agent posted during its own run is ours, not new input."""
            after = latest.get("snapshot")
            if after is None or not identity:
                return
            for comment in after.comments:
                if comment.id not in before and comment.author.lower() == identity:
                    own_comments.add(comment.id)
            for item in after.review_comments:
                if item.id not in before and item.author.lower() == identity:
                    own_comments.add(item.id)

        def on_wake(snapshot: Snapshot, delta: Delta) -> str:
            reaction = react(delta, snapshot, session, gh=client)
            if not isinstance(reaction, WakeRequest):
                audit.append(directory, "wake_skipped", reason=reaction)
                return reaction

            assignment = providers.select(providers.TRIAGE, session, health)
            if assignment is None:
                audit.append(directory, "no_provider_available")
                return "NO_PROVIDER"
            session.provider, session.provider_argv = assignment.provider, assignment.argv

            seen_before = ({item.id for item in snapshot.comments}
                           | {item.id for item in snapshot.review_comments})
            transaction = push_module.PushTransaction(
                git, session.remote, session.head_branch, directory=directory).open()
            extra = (f"\n## CI evidence\n{reaction.evidence}" if reaction.evidence else "")
            outcome = wake(session, snapshot, delta, guard=guard, worktree=tree.path,
                           directory=directory, ledger=ledger, runner=runner,
                           timeout=timeout, refresh=collect_snapshot,
                           instructions=_instructions(extra))
            report.outcomes.append(outcome)

            record_own_comments(seen_before)

            if (outcome.decision is not None
                    and outcome.decision.state == decision_module.REVIEW_NEEDED):
                _cross_review(session, snapshot, delta, health, guard, tree.path,
                              directory, ledger, runner, timeout, out, client)

            verification = transaction.close()
            report.pushes.append(verification)
            if verification.pushed:
                own_heads.add(verification.after_sha)
                session.last_push_sha = verification.after_sha
                session.last_push_at = time.time()
            if verification.needs_human:
                out(f"coder-ai: {verification.reason} — stopping mutation for a human.")
            if outcome.decision is None:
                state = (providers.AVAILABLE if outcome.stale
                         else providers.classify_failure(
                             f"{outcome.error}\n{outcome.output}"))
                providers.record(health, assignment.provider, state, note=outcome.error)
                if state != providers.AVAILABLE:
                    audit.append(directory, "provider_unhealthy",
                                 provider=assignment.provider, state=state,
                                 reason=outcome.error[:300])
            else:
                providers.record(health, assignment.provider, providers.AVAILABLE)
                out(_render_decision(outcome, verification))
            providers.save(directory, health)
            return outcome.decision.state if outcome.decision else outcome.error

        result = run_watch(
            session, collect=collect_snapshot, wake=on_wake, clock=clock,
            directory=directory, ledger=ledger, state_root=state_root,
            stop=lambda: daemon.stop_requested(directory),
            own_head_shas=own_heads, own_comment_ids=own_comments,
        )
        report.result = result

        if result.reason in {states.MERGED, states.CLOSED, states.USER_STOP}:
            release_worktree(session, cwd, result.reason, root=worktree_root,
                             session_directory=directory)
        guard_install.uninstall(guard, tree.path)
        daemon.clear_stop(directory)
        daemon.clear_pid(directory)
        save_session(session, root=state_root)
        audit.append(directory, "session_finished", reason=result.reason,
                     state=session.state, wakes=result.wakes, polls=result.polls)

        if result.reason == states.WATCH_TIMEOUT:
            out(watch_expired_report(session, ci=session.last_ci_state or "unknown",
                                     open_findings=len(ledger.open_findings())))
    return report


def _cross_review(session: Session, snapshot: Snapshot, delta: Delta, health,
                  guard, worktree: Path, directory: Path, ledger: Ledger, runner,
                  timeout: int, out: Callable[[str], None], client: Gh) -> None:
    """§44: a second opinion, preferably from the opposite provider. Read-only."""
    assignment = providers.select(providers.REVIEW, session, health)
    if assignment is None:
        audit.append(directory, "review_skipped", reason="no provider available")
        return

    reviewer = dataclasses.replace(session, provider=assignment.provider,
                       provider_argv=list(assignment.argv))
    audit.append(directory, "review_started", provider=assignment.provider,
                 opposite=assignment.provider != session.provider)
    outcome = wake(reviewer, snapshot, delta, guard=guard, worktree=worktree,
                   directory=directory, ledger=ledger, runner=runner, timeout=timeout,
                   role=providers.REVIEW, instructions=REVIEW_INSTRUCTIONS)
    if outcome.decision is None:
        providers.record(health, assignment.provider,
                         providers.classify_failure(f"{outcome.error}\n{outcome.output}"),
                         note=outcome.error)
        out(f"coder-ai: the {assignment.provider} review did not complete "
            f"({outcome.error}).")
        return
    providers.record(health, assignment.provider, providers.AVAILABLE)
    out(f"{assignment.provider.capitalize()} review → {outcome.decision.state}\n"
        f"{outcome.decision.summary}\n")


def _gh_identity(client: Gh, session: Session) -> str:
    """The account coder-ai acts as, so its own posts are not read as feedback."""
    try:
        value = client.json("api", "user", "--jq", ".login", timeout=30)
    except Exception:
        return ""
    return str(value or "").strip().lower()


def _instructions(extra: str) -> str:
    from coderai.pr_automation.wake import TRIAGE_INSTRUCTIONS

    return TRIAGE_INSTRUCTIONS + (extra or "")


def _render_decision(outcome: RunOutcome, verification) -> str:
    decision = outcome.decision
    if decision is None:
        return f"coder-ai: the {outcome.provider} run produced no decision ({outcome.error})."
    lines = [f"{outcome.provider.capitalize()} → {decision.state}", decision.summary]
    if decision.changed_files:
        lines += ["", "Changed", *[f"  {name}" for name in decision.changed_files[:20]]]
    if decision.validation:
        lines += ["", "Validation", *[f"  {item}" for item in decision.validation[:20]]]
    if decision.commits:
        lines += ["", "Commits", *[f"  {item}" for item in decision.commits[:10]]]
    if verification.pushed:
        lines += ["", f"Pushed {verification.after_sha[:7]} to {verification.head_ref}"]
    elif verification.status == push_module.UNPUSHED_COMMITS:
        lines += ["", f"{verification.unpushed} commit(s) are not pushed yet"]
    return "\n".join(lines) + "\n"


def status_rows(state_root: Path | None = None) -> list[Session]:
    """Every supervised PR on this machine (§74)."""
    return sorted(list_sessions(state_root or sessions_root()),
                  key=lambda item: (item.owner, item.repo, item.number))
