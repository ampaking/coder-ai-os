<!-- coder-ai-os:generated -->
# Suggested agent hooks — fill in real commands; not auto-run.

- post_edit [PostToolUse]: run the project formatter and linter on the edited file if defined
- before_finish [Stop]: remind to run the test suite before reporting done
- val_after_ui_change [PostToolUse]: after editing a file, inspect .coder-ai/val/config.json watchGlobs; when the edited path matches, run VAL as an isolated validation task with the current UI request as --prompt and return only manifest.summary plus the report path
