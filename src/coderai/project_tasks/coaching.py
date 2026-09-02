"""Evidence-bound, non-scoring project explanations for local AI clients."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from coderai.project_tasks.analytics import period_insights, task_evidence_summaries
from coderai.project_tasks.ingestion import redact_text


def explain_project(project: Path, question: object, period: str = "week",
                    anchor: str | None = None) -> dict[str, Any]:
    text, redactions = redact_text(question, 500)
    text = text or "Explain the current project situation"
    tasks = task_evidence_summaries(project)
    current = period_insights(project, period, anchor)
    scope = {"day": "selected day", "week": "selected week",
             "month": "selected month", "year": "selected year"}[period]
    active = [task for task in tasks if task["status"] in {"active", "paused", "needs_validation"}]
    blocked = [task for task in tasks if task["status"] == "blocked"]
    completed = [task for task in tasks if task["status"] == "completed"]
    evidence = [f"{len(tasks)} structured task record(s)", f"{len(active)} active or awaiting proof",
                f"{len(blocked)} blocked", f"{current['totals']['completedTasks']} task(s) completed in the {scope}",
                f"{current['totals']['passed']} passing proof record(s) in the {scope}"]
    lowered = text.casefold()
    if "block" in lowered or "stuck" in lowered:
        explanation = (f"The clearest blocker to review is: {blocked[0]['title']}"
                       if blocked else "No blocked task is recorded.")
        suggestion = "Confirm whether the blocker still applies, then preserve the decision before switching work."
    elif "effort" in lowered or "time" in lowered:
        known = [task for task in tasks if task.get("actual_minutes") is not None]
        explanation = f"Reported actual effort exists for {len(known)} of {len(tasks)} tasks. Missing effort is unknown, not zero."
        suggestion = "Use effort for planning and workload reflection, never as engineering value or performance."
    elif "prompt" in lowered or "ask ai" in lowered or "request" in lowered:
        explanation = "AI requests work better with one outcome, boundaries, expected behavior, edge cases, and proof."
        suggestion = "Ask for one bounded change and require uncertainty to be explained before risky edits."
    else:
        explanation = f"This project records {len(completed)} completed task(s), {len(active)} active task(s), and {len(blocked)} blocker(s)."
        suggestion = "Review the oldest unresolved item, preserve its decision, and choose one observable next outcome."
    return {"question": text, "explanation": explanation, "suggestion": suggestion,
            "evidence": evidence,
            "uncertainty": "Only structured local records are considered. Missing records may change this explanation.",
            "caution": "Personal project feedback only. Do not rank people, optimize appearances, or judge engineering value.",
            "period": current["period"], "anchor": current["anchor"],
            "aiContext": {"purpose": "Explain this project and suggest a safe next step.", "question": text,
                          "facts": evidence, "instructions": ["Separate facts from inference.",
                          "Explain missing evidence.", "Suggest small reversible actions.",
                          "Do not score productivity or engineering value."]},
            "redactions": redactions, "stored": False}
