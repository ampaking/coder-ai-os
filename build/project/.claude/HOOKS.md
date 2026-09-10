<!-- coder-ai-os:generated -->
# Suggested agent hooks — fill in real commands; not auto-run.

- post_edit [PostToolUse]: run the project formatter and linter on the edited file if defined
- val_after_ui_change [PostToolUse]: deterministically mark UI validation pending when the edited path matches VAL watchGlobs; never invoke a model or run VAL per edit
- evidence_observation [PostToolUse]: record the command or edited paths and the observed outcome, so a claim can be checked against what actually ran; never invoke a model, never store output
- project_tasks_session [Stop]: record one content-free, project-local Project Tasks session observation
