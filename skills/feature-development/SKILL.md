---
name: feature-development
description: Build a multi-file feature or new endpoint/component through scoped investigation, atomic implementation, validation, and final goal review.
---

# Feature development

1. Restate intent + a Definition of Done (observable). Classify the tier (AI_DEV_PROTOCOL).
2. Investigate via the atlas, not by reading the repo: `.ai/symbols/INDEX.md` picks the owning unit; the unit map's folder graph shows the wiring; `rg -w '<name>' .ai/symbols/` locates any symbol in one line. Read ONLY the source locations the task touches — never whole maps, never unaffected code. Name every file the change touches.
3. Break into atomic mini-PR tasks, each with a reason. One task in flight at a time. Load
   `native-orchestration`: the user's normal Claude/Codex session controls the plan, uses native
   same-provider subagents, and may invoke the other installed CLI directly for a bounded worker or review.
4. Implement the smallest complete change per task; validate (lint/typecheck/tests) after each. Keep
   delegated writes serial. Use opposite-provider review only for high-risk/cross-contract work or when
   requested; otherwise use correctness plus only relevant specialist lenses. Fix findings and re-review before
   advancing to the next task. Tests are bug detectors, not implementation confirmation: derive them from
   acceptance criteria and failure modes, cover boundaries/negative paths, and observe red-before-green for
   regressions when possible. For high-risk tests written after implementation, perform a safe temporary
   fault/mutation check, restore it, and rerun green.
5. Before claiming completion, run `.coder-ai/scripts/update-ai-context.sh --refresh`; inspect its Δ lines
   and the full diff vs the goal for dropped requirements, unrelated files, and contract changes. A `-name`
   you didn't intend is a dropped function; a function whose only caller disappeared is dead code to flag
   (`--graph <dir>` lists it under "needs verification"). Always review correctness; add security, performance,
   or architecture only when that risk is present. High-risk work uses all applicable lenses. Resolve
   in-scope findings, validate, then synchronize `CURRENT.md`. Installed hooks own Project Tasks collection;
   do not invoke its CLI during normal work.
6. For the human Git/release handoff, load `release-handoff`; write `.ai/commit.md` and repository-evidenced
   `.ai/knowledge/release-workflow.md`. Suggest only—never execute Git writes, pushes, or releases.
