# Contributing

`coder-ai-os` is a harness compiler: structured config → per-tool files. Change the config,
not the generated output.

## Workflow
1. Edit `config/*.yaml` (see [`config/README.md`](config/README.md)). Never hand-edit
   `build/*.md` — it is generated.
2. Compile and check: `coder-ai compile && coder-ai doctor`.
3. `doctor` must pass (within budget, no conflicts, no drift) before you commit `build/`.
4. Follow [`protocol/AI_DEV_PROTOCOL.md`](protocol/AI_DEV_PROTOCOL.md): classify the tier,
   understand before editing, atomic tasks each with a reason, validate with an observed run.

## Guardrails (never relax)
Safety lives in `config/safety.yaml` + `claude/permissions.json`. The compiler drops any
`safety:` from an overlay. Do not add flows that read `.env`/secrets or perform git writes.

## Adding a tool adapter
1. Add an entry to `TOOLS` in `bin/compile` and a footer in `footer()`.
2. Repo-scoped tools (Cursor, Copilot) are emitted via the `--project` flow, not global install.
3. Keep every output within the token budget (`doctor` enforces it).

## Testing
The scripts are validated by running them against a sandbox `HOME` and a throwaway git repo
(never your real `~`). Run Python tests with
`PYTHONPATH=src python3 -m unittest discover -s tests -p '*_test.py'`. Syntax-check shell with
`bash -n` and the runtime with `PYTHONPATH=src python3 -m compileall -q src/coderai tests`.
Regenerate an installed project's snapshot with `.coder-ai/scripts/update-ai-context.sh` when structure changes.
