---
name: val-capture
description: Capture deterministic responsive UI screenshots with VAL when a task changes or explicitly requests visual output.
---
<!-- coder-ai-os:generated -->

# VAL capture

Use the repository's `.coder-ai/val/run` wrapper. Run `doctor` before the first capture on a machine, then `run --task <safe-id> --prompt "<current user UI request>"` so explicit criteria reach the checklist.

Do not install browser packages into the project or start/stop processes yourself; VAL owns those boundaries. Treat screenshots as artifacts and inspect only those needed to diagnose a reported finding.
