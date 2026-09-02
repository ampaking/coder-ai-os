---
name: task-lifecycle
description: Resume or run a complex repository task from portable checked-in state. Use when continuing work across sessions, agents, models, or context compaction.
---
<!-- coder-ai-os:generated -->

# Task lifecycle

1. Read `.ai/memory/INDEX.md`, `.ai/memory/CURRENT.md`, the active epic plan, and its current task. Re-read the original Definition of Done.
2. If the checkpoint is incomplete, reconstruct only from the working tree, task artifacts, and observed validation; record unknowns instead of guessing.
3. Before editing a unit, snapshot its structure: `.coder-ai/scripts/update-ai-context.sh --symbols <dir> --baseline`. Locate symbols via the unit's atlas maps: QUERY them, never read a map whole — `rg -w '<name>' .ai/symbols/` answers in one line; open only the section the task needs (each map's header lists the sed recipes). Run `--symbols <dir> --check` first; if STALE, regenerate.
4. Execute exactly one atomic task. Retrieve context query-first; for feature/epic work load
   `native-orchestration`, use native same-provider subagents or direct invocation of the other installed
   CLI, and keep all writes serial. The current normal Claude/Codex session remains the controller.
5. Run the task's relevant lint, typecheck, tests, and diff checks. Then run `.coder-ai/scripts/update-ai-context.sh --symbols <dir> --diff` (or `--changed <dir>` to diff against git HEAD when no baseline exists): every +/-/~ symbol must map to the task's declared Files — anything outside is an unrelated change to revert or report. On task completion run `.coder-ai/scripts/update-ai-context.sh --refresh` — it regenerates exactly the atlas maps your changes touched, tracing which files are done. Record commands and observed results; never infer success.
6. Review by tier: trivial self-review; small one correctness pass; feature correctness plus relevant
   specialist lenses; high-risk all applicable lenses and opposite-provider review when available.
7. Before yielding for any reason, update `CURRENT.md` with intent, DoD, active task, decisions, files, next action, blockers, validation, and timestamp.
8. Before claiming completion, compare the full diff and validations with the original goal. Run the
   tier-appropriate review passes over changed code and direct callers;
   surface findings with file:line and resolve in-scope findings. Then synchronize `CURRENT.md`; installed
   hooks own Project Tasks lifecycle collection, so do not invoke its CLI during normal work. Report
   omissions explicitly; missing/failing evidence means partial.
