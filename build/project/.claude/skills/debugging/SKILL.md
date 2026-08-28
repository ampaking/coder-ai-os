---
name: debugging
description: Diagnose and fix failing tests, runtime errors, or unexpected behavior. Use when a failure must be reproduced, localized, corrected, and verified.
---
<!-- coder-ai-os:generated -->

# Debugging

1. Reproduce first — capture the exact failing command and its output. Never guess.
2. Localize before reading whole files: find the owning unit in `.ai/PROJECT_SNAPSHOT.md`, QUERY its atlas maps — `rg -w '<name>' .ai/symbols/` returns the one table row you need; never read a whole map (each header lists sed recipes for single sections; regenerate on demand: `.coder-ai-os-script/update-ai-context.sh --symbols <dir>`). Then read only that source location. Callers/impact: the map's import graphs and used-by columns, dep-scoped `rg -w`, or the code index.
3. Form one hypothesis, add the smallest probe (log/assert/test) that confirms or refutes it.
4. Fix the root cause, not the symptom. Keep the change minimal and unrelated code untouched.
5. Re-run the exact failing command + the surrounding tests. Report the observed result.
