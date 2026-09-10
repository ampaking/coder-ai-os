"""`coder-ai prove` — the report the harness writes, from what it observed.

The model does not get to say whether its work is done. This reads the evidence
ledger, the requirements the diff implies, the acceptance list, and the companion
work the repository's own conventions expect, and prints one table.

A claim with no evidence handle prints UNVERIFIED. An acceptance item with no work
prints NOT DONE. Evidence older than the last edit of the files it covers prints
STALE. Exit is 0 only when everything is verified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from coderai.evidence import acceptance as acceptance_module
from coderai.evidence import companions as companions_module
from coderai.evidence import ledger as ledger_module
from coderai.evidence import requirements as requirements_module
from coderai.evidence import split as split_module
from coderai.evidence import val_link as val_link_module

VERIFIED = "✓ verified"
UNVERIFIED = "UNVERIFIED"
NOT_DONE = "NOT DONE"
STALE = "STALE"
UNKNOWN = "unknown"

_BAD = {UNVERIFIED, NOT_DONE, STALE}


@dataclass
class Line:
    item: str
    status: str
    evidence: str = ""
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status not in _BAD


def hook_installed(project: Path) -> bool:
    """Is the PostToolUse observation hook actually registered for this repo?

    Dropping `.coder-ai/scripts/evidence-post-tool.py` is not the same as wiring it
    into .claude/settings.json, and only the second one makes anything observable.
    """
    import json

    settings = Path(project) / ".claude" / "settings.json"
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    for entries in (data.get("hooks") or {}).values():
        for block in entries if isinstance(entries, list) else []:
            for hook in (block.get("hooks") or []):
                if "evidence-post-tool" in str(hook.get("command", "")):
                    return True
    return False


@dataclass
class Verdict:
    lines: list[Line] = field(default_factory=list)
    observed: bool = True
    hooked: bool = True
    drift: acceptance_module.Drift | None = None
    plan: split_module.Plan | None = None
    task: str = "current"

    @property
    def failures(self) -> list[Line]:
        return [line for line in self.lines if not line.ok]

    @property
    def verified(self) -> bool:
        return self.observed and not self.failures

    @property
    def summary(self) -> str:
        total = len(self.lines)
        good = total - len(self.failures)
        if not self.observed:
            return "unverified — nothing was observed"
        if not total:
            return "nothing to prove"
        return ("verified" if good == total
                else f"partial — {good} of {total} verified")


def _requirement_lines(project: Path, records: list, changed: list[str]) -> list[Line]:
    lines: list[Line] = []
    for requirement in requirements_module.require(project, changed):
        if not requirement.known:
            lines.append(Line(item=requirement.kind, status=UNKNOWN,
                              note=f"{requirement.why} — no command documented"))
            continue
        matches = ledger_module.commands_matching(records, requirement.satisfied_by)
        passing = [record for record in matches if record.ok]
        if not passing:
            lines.append(Line(item=requirement.kind, status=UNVERIFIED,
                              note=f"{requirement.why} · run: {requirement.how}"))
            continue
        latest = max(record.at for record in passing)
        edited = ledger_module.last_edit(records, requirement.paths)
        newest = max(passing, key=lambda record: record.at)
        if edited and latest < edited:
            lines.append(Line(item=requirement.kind, status=STALE,
                              evidence=newest.command,
                              note="ran before the last edit of these files"))
        else:
            lines.append(Line(item=requirement.kind, status=VERIFIED,
                              evidence=newest.command))
    return lines


def _acceptance_lines(items: list, requirement_lines: list[Line]) -> list[Line]:
    """An item is only as verified as the surface it belongs to."""
    by_surface = {line.item: line for line in requirement_lines}
    surface_check = {"ui": "visual", "api": "tests", "schema": "contract", "i18n": "visual"}
    lines: list[Line] = []
    for item in items:
        if not item.checked:
            lines.append(Line(item=item.text, status=NOT_DONE,
                              note="not marked complete"))
            continue
        surface = split_module.surface_of_text(item.text)
        related = by_surface.get(surface_check.get(surface, ""))
        if related is None:
            lines.append(Line(item=item.text, status=UNVERIFIED,
                              note="claimed complete; no check covers it"))
        elif related.status == VERIFIED:
            lines.append(Line(item=item.text, status=VERIFIED, evidence=related.evidence))
        else:
            lines.append(Line(item=item.text, status=UNVERIFIED,
                              note=f"{surface} work is {related.status.lower()}"))
    return lines


def _companion_lines(project: Path, changed: list[str]) -> list[Line]:
    added = [path for path in changed if not (Path(project) / path).is_dir()]
    lines: list[Line] = []
    for companion in companions_module.infer(Path(project), added):
        if not companion.strong or (Path(project) / companion.expected).exists():
            continue      # a weak analogy is not worth interrupting anyone over
        lines.append(Line(item=f"{companion.kind}: {companion.expected}",
                          status=NOT_DONE,
                          note=f"expected because {companion.analogue} exists — "
                               "dismiss if the analogy does not hold"))
    return lines[:6]


_UI_SUFFIXES = {".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".html"}


def _visual_chain_lines(project: Path, changed: list[str],
                        already_verified: bool) -> list[Line]:
    """A silently broken visual loop is why unverified UI ships.

    Skipped when a visual run was actually observed: evidence that the loop ran
    outranks inspection of whether it could. Reported only when this task touched
    something that looks like interface work — including when the watch globs are
    wrong, which is exactly the case that matches nothing and says nothing.
    `coder-ai doctor` and `coder-ai status` report a broken chain regardless.
    """
    if already_verified:
        return []
    chain = val_link_module.check(Path(project))
    if not chain.configured or chain.works:
        return []
    touched_ui = any(Path(path).suffix in _UI_SUFFIXES for path in changed)
    if not touched_ui and not val_link_module.pending(Path(project)):
        return []
    first = chain.broken[0]
    return [Line(item="visual loop", status=UNVERIFIED,
                 note=f"{first.name}: {first.detail} · {first.fix}")]


def prove(project: Path, task: str | None = None, *,
          changed: list[str] | None = None) -> Verdict:
    project = Path(project)
    task = task or ledger_module.current_task(project)
    records = ledger_module.read(project, task)
    changed = changed if changed is not None else requirements_module.changed_files(project)

    verdict = Verdict(observed=bool(records), hooked=hook_installed(project), task=task)
    verdict.lines = _requirement_lines(project, records, changed)

    listing = acceptance_module.load(project, task)
    if listing is not None:
        verdict.drift = acceptance_module.drift_since_start(project, task, listing)
        verdict.lines = _acceptance_lines(listing.items, verdict.lines) + verdict.lines
        verdict.plan = split_module.plan(listing, companions_module.infer(project, changed))

    verdict.lines += _companion_lines(project, changed)
    visual_ok = any(line.item == "visual" and line.status == VERIFIED
                    for line in verdict.lines)
    verdict.lines += _visual_chain_lines(project, changed, visual_ok)

    if not verdict.observed:
        for line in verdict.lines:
            if line.status == VERIFIED:
                line.status = UNVERIFIED
                line.note = "no evidence was recorded for this task"
    return verdict


def render(verdict: Verdict) -> str:
    width = min(52, max((len(line.item) for line in verdict.lines), default=20))
    out = [f"RESULT  {verdict.summary}", ""]

    if not verdict.observed:
        out.append("  No evidence was recorded for this task.")
        if verdict.hooked:
            # The hook is wired; this run simply has nothing behind it yet. Telling the
            # user to sync here sent them to a command that would change nothing.
            out.append("  Nothing below is verified — run the work's checks so they can "
                       "be observed.")
        else:
            out.append("  The observation hook is not registered in .claude/settings.json"
                       " — run: coder-ai sync")
        out.append("")

    if verdict.lines:
        out.append(f"  {'item':<{width}}  {'status':<12} evidence")
        for line in verdict.lines:
            evidence = line.evidence or line.note
            out.append(f"  {line.item[:width]:<{width}}  {line.status:<12} {evidence[:60]}")
        out.append("")

    drift = verdict.drift
    if drift and drift.shrank:
        out.append("  The acceptance list changed since it was written:")
        for text in drift.removed:
            out.append(f"    removed:  {text[:70]}")
        for before, after in drift.reworded:
            out.append(f"    reworded: {before[:34]} → {after[:34]}")
        out.append("")
    if drift and drift.retrospective:
        out += ["  The acceptance list was written after the work began.", ""]

    if verdict.plan and verdict.plan.split:
        out.append(f"  This work crosses {len(verdict.plan.children)} surfaces "
                   "— consider splitting it:")
        for child in verdict.plan.order():
            out.append(f"    {child.surface:<8} {child.describe()}")
        out.append("")

    if verdict.failures:
        out.append(f"  {len(verdict.failures)} item(s) are not verified. "
                   "Say so plainly rather than reporting success.")
    return "\n".join(out) + "\n"
