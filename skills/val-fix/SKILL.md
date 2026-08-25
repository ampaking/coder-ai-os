---
name: val-fix
description: Run VAL's guarded visual repair loop automatically after UI changes match configured watch globs.
---
# VAL fix

Run the complete VAL loop as an isolated validation task after UI changes, passing the current request with `--prompt`. VAL may patch only files present in the task's initial git diff and owns its reload/rebuild decisions.

Stop on VAL's terminal exit: `0` green, `1` findings/manual work remain, `2` escalated with `BLOCKED.md`, `3` infrastructure failure. Return only `manifest.summary`, report path, and exit meaning to the parent context; do not load all screenshots or the browser transcript.
