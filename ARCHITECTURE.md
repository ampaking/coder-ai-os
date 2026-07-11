# Architecture

`coder-ai-os` is not an AGENTS.md generator — it is a **harness compiler**. You define an
engineering harness once (structured config), and it compiles into the native
configuration format of each AI coding tool. This is the "harness engineering" model:
what changes an agent's behavior is the whole harness (instructions · tools · permissions ·
memory · context · workflow · automation), not one Markdown file.

## Pipeline

```
config/*.yaml   (kernel — one source of truth)
  + config/local.yaml   (per-machine overlay, gitignored, from the wizard / AI intake)
        │
        ▼  bin/compile   (PyYAML if present, else a vendored zero-dep parser)
        │   • deep-merges overlay over defaults   • strips any safety: override
        │   • guardrails-first render             • enforces the token budget
        ▼
build/AGENTS.md (canonical) · build/CLAUDE.md · build/GEMINI.md   (adapters)
        │
        ▼  install.sh   (marker-guarded inject; cleans legacy markers)
        ▼
~/.codex/AGENTS.md · ~/.claude/CLAUDE.md · ~/.gemini/GEMINI.md
```

Three intake paths converge on the **same** overlay, so output is deterministic:
`bin/coder-ai-os init --interactive` (wizard) · `--ai claude|codex` (AI interview,
draft → review → compile) · `--from profile.yaml`.

## The seven engines

| Engine | Owns | Config | Status |
|---|---|---|---|
| **Profile** | experience, languages, style (coding.*), reply format | `config/user.yaml` (+ `profiles/<lang>.yaml`) | done |
| **Safety** | non-negotiable guardrails (`.env`/git/sudo/deploy) | `config/safety.yaml` + `claude/permissions.json` | done |
| **Workflow** | intent → plan → atomic tasks → validate → review | `config/workflow.yaml` + `protocol/AI_DEV_PROTOCOL.md` | done (instructional, by design) |
| **Context** | orient L1–L4, snapshot, index, retrieval policy + budgets | `config/context.yaml` · `scripts/update-ai-context.sh` | done |
| **Skill** | lazy-load one skill per trigger | `config/skills.yaml` + `skills/<id>/SKILL.md` | done |
| **Adapter** | emit per-tool files (5 tools) | `bin/compile` render/footers | done |
| **Audit** | budget, drift, conflicts, skills, health | `coder-ai-os doctor` | done |

Automation (`commands`/`subagents`/`hooks`), `mcp`, and `memory` engines are compiled from
their config into repo-scoped artifacts by `--project`. See
[`docs/harness-coverage.md`](docs/harness-coverage.md) for the full 8-mechanism mapping (100%).

## Layering

```
committed defaults   config/*.yaml            (shared, source of truth)
   └─ overridden by  config/local.yaml        (per-machine, gitignored — wizard/AI writes it)
                     safety: is always dropped from the overlay
```

Repo-scoped adapters (Cursor `.cursor/rules/coder-ai-os.mdc`, Copilot
`.github/copilot-instructions.md`) are emitted into a target repo by `install.sh --project`
— Cursor as its own file, Copilot as a marker block that preserves any existing content.
Planned (Phase B): global → language → repository (`.ai-agent/`) → task resolution.

## Design principles

1. **Guardrails first.** Negative constraints (`safety.yaml` `never:`) compile to the top of
   every file — they improve reliability more than positive checklists, and the compiler
   forbids weakening them from an overlay.
2. **Small instructions, retrieve on demand.** The always-loaded block stays under the 3 KB
   global budget (Codex truncates concatenated AGENTS.md at 32 KiB); depth lives in
   `AI_DEV_PROTOCOL.md`, the snapshot, and the code index — loaded only when needed.
3. **Deterministic + reviewable.** One canonical schema; manual, AI, and file intake all
   produce the same overlay. The AI configures preferences only — never permissions,
   destructive ops, secret access, or architecture.
4. **Budget-enforced.** `coder-ai-os doctor` fails when output exceeds budget or configs
   conflict, so drift is caught before install.
5. **Coexists with your existing setup — never clobbers.** Instruction files use marker blocks
   (your content outside them is preserved). `settings.json` is a `jq` merge that keeps your
   `defaultMode` and only unions in guardrail deny-rules. Generated commands/skills carry a
   `coder-ai-os:generated` marker; install skips (and logs) any same-named file that isn't ours,
   and only updates its own on re-run. MCP registration writes only its own named keys.

## File map

```
config/            kernel — identity·safety·workflow·tokens·user·context·skills·
                   commands·subagents·hooks·mcp·memory (+ local.yaml overlay, profiles/<lang>.yaml)
skills/            skills/<id>/SKILL.md — full skill text (loaded on trigger)
bin/compile        the harness compiler (config → build/*.md + build/project/, --check/--validate/--doctor)
bin/setup          manual preference wizard
bin/coder-ai-os    unified CLI (init/compile/doctor/diff/profile)
build/             compiled per-tool blocks + build/project/ repo-scoped artifacts (committed)
install.sh         inject global blocks (+ --project drops repo-scoped harness artifacts)
intake/            AI interview instructions (draft → review → compile)
scripts/           update-ai-context.sh — zero-dep repo snapshot generator
protocol/          AI_DEV_PROTOCOL.md — the tiered development workflow
docs/              intake, harness coverage, deeper guides
.ai/coder-ai-os/      architecture roadmap (plan.md)
```

## References

The harness-compiler model is grounded in *"Harness Engineering for Agentic AI Coding Tools"*
([PDF](https://arxiv.org/pdf/2602.14690)) and related work on AGENTS.md efficiency and context
engineering. Full citations, PDF links, and acknowledgements:
[`docs/harness-coverage.md`](docs/harness-coverage.md#references--thanks).
