---
name: debugging
description: Diagnose and fix failing tests, runtime errors, or unexpected behavior. Use when a failure must be reproduced, localized, corrected, and verified.
---
<!-- coder-ai-os:generated -->

# Debugging

1. Reproduce first — capture the exact failing command and its output. For a bug fix, preserve or add a
   regression test that fails for the reported behavior before changing production code whenever the
   environment permits. A test observed only after the fix is weaker evidence and must be labeled as such.
2. Localize before reading whole files: find the owning unit in `.ai/PROJECT_SNAPSHOT.md`, QUERY its atlas maps — `rg -w '<name>' .ai/symbols/` returns the one table row you need; never read a whole map (each header lists sed recipes for single sections; regenerate on demand: `.coder-ai/scripts/update-ai-context.sh --symbols <dir>`). Then read only that source location. Callers/impact: the map's import graphs and used-by columns, dep-scoped `rg -w`, or the code index.
3. Form one hypothesis, add the smallest probe (log/assert/test) that confirms or refutes it. Derive the
   test from the contract, user-visible failure, boundary, or invariant—not from the implementation you
   intend to write. Assert externally meaningful outputs/state/errors; do not mock the unit under test,
   repeat its algorithm in the assertion, or accept a snapshot as the only proof of behavior.
4. Fix the root cause, not the symptom. Keep the change minimal and unrelated code untouched.
5. Re-run the exact failing command + the surrounding tests. Include the failure path, adjacent boundary
   cases, and one negative/error case when relevant. Report the observed result.
6. Prove the regression test is sensitive: prefer an observed red-before/green-after result. If the test
   was necessarily written after the fix and the changed behavior is high-risk, make one safe temporary
   mutation that recreates the defect, observe the test fail for the expected reason, restore immediately,
   then rerun green. Never leave the mutation in the tree or weaken assertions to make it pass.
