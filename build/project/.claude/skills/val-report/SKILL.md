---
name: val-report
description: Summarize a completed VAL run from its manifest and report without loading screenshot binaries or browser transcripts.
---
<!-- coder-ai-os:generated -->

# VAL report

Read `.coder-ai/val/runs/<task>/manifest.json` first. Report criterion totals, failures with evidence paths, manual items, blocked routes, rounds, driver/browser modes, and the exit meaning.

Do not claim success unless `exitCode` is `0`. Link evidence paths rather than embedding screenshots unless the user asks to inspect one.
