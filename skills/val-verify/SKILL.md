---
name: val-verify
description: Verify UI changes through VAL deterministic overflow, accessibility, focus, tap-target, viewport, and pixel checks.
---
# VAL verify

After a UI-file change matches `.coder-ai/val/config.json` `watchGlobs`, run `.coder-ai/val/run run --task <safe-id> --prompt "<current user UI request>"`. Use `manifest.json` and `report.md` as the primary result; never infer green from the presence of screenshots.

Manual criteria remain non-green. Never promote baselines automatically; only a human runs `val baseline promote` after merge.
