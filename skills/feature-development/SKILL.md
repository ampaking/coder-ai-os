---
name: feature-development
description: Build a multi-file feature or new endpoint/component through scoped investigation, atomic implementation, validation, and final goal review.
---

# Feature development

1. Restate intent + a Definition of Done (observable). Classify the tier (AI_DEV_PROTOCOL).
2. Investigate via the atlas, not by reading the repo: `.ai/symbols/INDEX.md` picks the owning unit; the unit map's folder graph shows the wiring; `rg -w '<name>' .ai/symbols/` locates any symbol in one line. Read ONLY the source locations the task touches — never whole maps, never unaffected code. Name every file the change touches.
3. Break into atomic mini-PR tasks, each with a reason. One task in flight at a time.
4. Implement the smallest complete change per task; validate (lint/typecheck/tests) after each.
5. Review the full diff vs the goal — dropped requirements? unrelated files? contract changes? Then `scripts/update-ai-context.sh --refresh`: its Δ lines show OLD vs NEW symbols per map — a `-name` you didn't intend is a dropped function, a function whose only caller you removed is dead-code to flag (`--graph <dir>` lists it under "needs verification"). Review only the places the Δ names.
