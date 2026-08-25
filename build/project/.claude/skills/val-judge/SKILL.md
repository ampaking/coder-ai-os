---
name: val-judge
description: Localize VAL visual failures using failure-only evidence when deterministic checks or soft UI criteria remain non-green.
---
<!-- coder-ai-os:generated -->

# VAL judge

Use the judgments emitted by `val run`; do not send passing screenshots or arbitrary repository files to a model. Source context must be limited to files already in the task's initial git diff.

Treat a missing or malformed judgment as infrastructure/non-actionable evidence, never permission to invent a file or location.
