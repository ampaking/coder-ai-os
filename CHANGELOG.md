# Changelog

Notable changes to `coder-ai-os`. Dates are absolute (YYYY-MM-DD).

## [0.1.0] — 2026-07-11 — first public release
### Changed (hybrid: global behavior + per-repo config)
- Split into two clean layers. `coder-ai-os install` writes only your **reusable behavior** to
  `~` (guardrails · reply format · workflow · generic skills) with **zero project data**, so it
  never conflicts between apps. `coder-ai-os setup` writes everything project-specific **into the
  repo** and touches `~` nothing. (Fixes the cross-app conflict + the "setup bled into root" bug.)
- Per-repo workspace `<repo>/.coder-ai/`: `project.yaml` (this repo's languages/config, auto-
  detected), `generated/` (the repo-merged compiled block), `identity.json` (isolation
  fingerprint). The compiler gained `--project <repo>` + a repo-overlay layer
  (precedence: defaults < language preset < machine < **repo**; safety never weakened).
- Isolation fingerprint (`identity.json` = project_id · git_root_hash · remote_hash): copy a
  `.coder-ai/` from another repo and the mismatch is detected and the cache rebuilt — App A's
  context can't leak into App B. `generated/` + `identity.json` are gitignored (machine-local).

### Added
- Honest "Does it actually work?" section in the README: the product is treated as a hypothesis
  to measure, not assume. The one deterministic, checkable claim is size — the compiled
  always-loaded block is ~2.9 KB (`wc -c build/*.md`); task-level gains are left to the user to
  measure on their own repos and models. No unbacked benchmark numbers are claimed.
- One-line install: `bootstrap.sh` clones (or updates) the repo and runs `install.sh` in a single
  `curl … | sh` command — no manual clone/cd. README leads with it; the git-clone flow remains.
- Anti-re-ask rule: agents no longer drip or repeat clarification questions. The always-loaded
  block now says "batch questions in ONE message, don't drip or re-ask"; `AI_DEV_PROTOCOL.md`
  adds "Ask once, not again and again" — one round, then proceed on stated assumptions, no
  circling back for reconfirmation.
- Efficiency protocol tightened: `AI_DEV_PROTOCOL.md` gains explicit task-sizing rules
  (one reviewable behavior per task · < 5 changed files · separate refactor/behavior/schema/tests ·
  implement one → validate → next, never all-at-once). The always-loaded block now states
  "atomic tasks — one at a time, validate each" so agents make small, cheap, validated changes.
- Beginner-friendly, OS-style setup: `./install.sh` on a first interactive run offers a guided
  setup ("Run the guided setup? [Y/n]") instead of silent defaults (TTY-gated, so scripts/CI are
  unaffected). The wizard now has a welcome banner, **detects which AI CLIs are installed**,
  numbered steps, and a review-then-confirm ("Apply this to your agents now?").
- Smarter AI intake: `intake/PROFILE_INTERVIEW.md` now tells the agent to *understand intent* —
  infer languages/tools from the repo and machine, respect existing agent config, pre-fill and
  adapt questions — rather than mechanically fill a template. Still draft → your approval → compile.
- End-of-task goal review, uncapped: `config/tokens.yaml` `final_review` makes the task-completion
  review render in your `reply_format` (flow-graph/prose/minimal) and be **exempt from
  `max_tokens`** — mid-task replies stay within the cap. Reply format + budget are user-flexible
  (wizard or `config/local.yaml`); README now has a "Make it yours" guide and a simple credit to
  the harness-engineering paper (arXiv:2602.14690).

### Fixed (onboarding)
- The `coder-ai-os` CLI is now runnable after install: `install.sh` symlinks it into
  `~/.local/bin` (with a PATH hint if that dir isn't on PATH), so the `coder-ai-os …` commands
  in the README work. Previously a fresh user got "command not found".
- Clarified the installer's closing note: Gemini CLI reads `~/.gemini/GEMINI.md` automatically
  (no manual step); the UI-toggle note now clearly applies to Google Antigravity (the IDE), not Gemini.

### Fixed (coexistence with your existing agent config)
- Never clobber your files: generated commands/skills/agents carry a `coder-ai-os:generated`
  marker; install skips (and logs) any same-named file that isn't ours, installs the rest, and
  updates only its own on re-run. Previously a same-named `plan.md`/`debugging` skill was overwritten.
- Preserve your Claude `defaultMode`: `settings.json` merge now keeps your chosen permission
  mode (sets `auto` only when you have none) and `skipAutoPermissionPrompt`; guardrail deny-rules
  are still always unioned in. Marker blocks and per-server MCP keys were already non-destructive.

### Changed
- Renamed the project `ai-agent-os` → **`coder-ai-os`**: CLI is now `bin/coder-ai-os`, markers
  are `coder-ai-os:managed`/`:snapshot`, roadmap dir is `.ai/coder-ai-os/`. The installer
  cleans legacy `ai-agent-os` blocks on install, so re-running replaces rather than duplicates.

### Added
- Generic MCP registration: `install.sh --with-mcp` registers every server in `config/mcp.yaml`
  with its target tools using each one's native mechanism — `claude mcp add --scope user`,
  Codex `~/.codex/config.toml [mcp_servers.*]` (marker-guarded), and Gemini/Cursor `mcpServers`
  JSON (jq merge, preserving existing). Servers with an `installer:` field (CodeGraph) keep
  their dedicated path.
- Code-standards auto-discovery: `scripts/discover-standards.sh` reads the repo's own tooling
  config (Prettier/ESLint/tsconfig; Black/Ruff/mypy; editorconfig…) plus light inference into
  `.ai/standards.md`. `--project` runs it and a git hook keeps it fresh; the compiled block
  tells every agent to match it before writing code.
- Cross-agent memory: file-based markdown (git-committed), **not** a DB — so switching
  Claude/Codex/Cursor loses nothing. `--project` seeds `.ai/memory/INDEX.md` (durable facts)
  and `.ai/memory/CURRENT.md` (resume point); the block tells agents to read INDEX first and
  resume from CURRENT.
- Monorepo support: a `monorepo-change` skill (triggers on cross-package / API-contract changes)
  that instructs the agent to fix the owning package first, in isolation, trace dependents,
  fix each in its own package, then return to root for the full harness check. `--project` also
  reinforces any *existing* per-package `AGENTS.md`/`CLAUDE.md` with isolation guidance
  (augment-only — never creates surprise files).
- Cross-tool skills: `skills/<id>/SKILL.md` now gets `name`+`description` frontmatter (for
  discovery) and installs into `~/.claude/skills` **and** `~/.codex/skills` — same progressive
  disclosure in both CLIs (was Claude-only).
- Executable hooks: `config/hooks.yaml` Stop hooks compile to a real, firing
  `.claude/settings.json` (safe `prompt` type — "validate before done"); installed into a repo
  only when it has no `settings.json` (else written as a side file to merge). Was advisory-only.
- `coder-ai-os verify`: shows which CLIs actually load the block, command/skill counts, and
  runs `codex --print-instructions` to confirm Codex sees it.
- Cross-CLI commands: `config/commands.yaml` now compiles into every tool's native format —
  Claude (`.claude/commands/*.md`), Codex prompts (`~/.codex/prompts/*.md`), Gemini
  (`~/.gemini/commands/*.toml`), Cursor (`.cursor/commands/*.md`). `install.sh` places the
  global ones (Claude/Codex/Gemini) and `--project` drops Cursor's — so `/plan`, `/review`,
  `/snapshot` trigger in whichever CLI you run. (Note: Codex is moving prompts → skills;
  cross-tool skills are the next step.)
- Harness engines to 100% coverage (P2–P7):
  - **Context** (`config/context.yaml`): retrieval policy + advisory token budgets; a compact
    Harness section in the block; `coding.*` on the profile; language layering
    (`config/profiles/<lang>.yaml` merged by `user.languages`).
  - **Skills** (`config/skills.yaml` + `skills/<id>/SKILL.md`): lazy triggers compiled as
    summaries; full skill dropped into `.claude/skills/` by `--project`.
  - **Automation**: `config/commands.yaml` → `.claude/commands/`, `config/subagents.yaml` →
    `.claude/agents/`, `config/hooks.yaml` → advisory `.claude/HOOKS.md`.
  - **MCP** (`config/mcp.yaml`) registry + `.ai/MCP.md`; **Memory** (`config/memory.yaml`) →
    `.ai/memory/` convention.
  - `bin/compile` emits `build/project/`; `coder-ai-os doctor` adds context health, skills
    (enabled/unused/missing), and per-mechanism counts.
- Cursor + Copilot adapters (P1): `bin/compile` emits `build/cursor.mdc` (with `.mdc`
  frontmatter) and `build/copilot-instructions.md`; `install.sh --project` writes them into
  a repo (Cursor as its own file, Copilot as a marker block preserving existing content).
  Context files now cover 5 of 5 tools.
- Documentation set: `ARCHITECTURE.md`, `config/README.md` (schema), `docs/intake.md`,
  `docs/harness-coverage.md`, `CONTRIBUTING.md`, `LICENSE` (MIT), this changelog.
- `coder-ai-os doctor` / `bin/compile --doctor` — budget, drift, conflict, and duplicate
  checks with a health summary.
- `.gitignore` entries for `__pycache__/`, `*.pyc`, `.mypy_cache/`.

## 2026-07-11 — harness compiler + OS CLI
### Added
- Agent Compiler: `config/*.yaml` (kernel) → `bin/compile` → `build/{AGENTS,CLAUDE,GEMINI}.md`,
  guardrails-first, budget-enforced (3 KB global / 32 KiB Codex). Vendored zero-dep YAML
  parser with PyYAML fallback; per-format reply skeletons.
- `bin/coder-ai-os` CLI: `init --interactive | --ai <tool> | --from <file>`, `compile`,
  `doctor`, `diff`, `profile show`.
- Manual wizard (`bin/setup`) + AI intake (`intake/PROFILE_INTERVIEW.md`, draft→review→compile)
  writing a gitignored `config/local.yaml` overlay; `safety:` overrides are stripped.
- Per-repo bootstrap (`install.sh --project`): protocol + snapshot generator + auto-refresh
  git hooks + optional CodeGraph index. Token-saver snapshot (`scripts/update-ai-context.sh`).
### Changed
- Renamed project `ai-agent-setup` → `coder-ai-os`; installer cleans legacy marker blocks.
- Instruction layers split: small always-loaded rules + deferred `AI_DEV_PROTOCOL.md`;
  clarify-by-exception + orient L1–L4 + INTENT reply field.
