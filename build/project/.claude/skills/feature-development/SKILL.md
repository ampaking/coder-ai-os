---
name: feature-development
description: Use when — new endpoint or component · multi-file feature
---
<!-- coder-ai-os:generated -->

# Skill: feature-development

Trigger: a new endpoint/component or a multi-file feature.

1. Restate intent + a Definition of Done (observable). Classify the tier (AI_DEV_PROTOCOL).
2. Investigate: entry points, callers, similar implementations, tests, config. Name every file the change touches.
3. Break into atomic mini-PR tasks, each with a reason. One task in flight at a time.
4. Implement the smallest complete change per task; validate (lint/typecheck/tests) after each.
5. Review the full diff vs the goal — dropped requirements? unrelated files? contract changes?
