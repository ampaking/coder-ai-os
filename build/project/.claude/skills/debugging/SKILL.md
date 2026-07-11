---
name: debugging
description: Use when — failing test · runtime error · unexpected behavior
---
<!-- coder-ai-os:generated -->

# Skill: debugging

Trigger: a failing test, a runtime error, or unexpected behavior.

1. Reproduce first — capture the exact failing command and its output. Never guess.
2. Localize with the index/snapshot (callers of the failing symbol, related tests) before reading whole files.
3. Form one hypothesis, add the smallest probe (log/assert/test) that confirms or refutes it.
4. Fix the root cause, not the symptom. Keep the change minimal and unrelated code untouched.
5. Re-run the exact failing command + the surrounding tests. Report the observed result.
