# Changelog

Notable changes to `coder-ai-os`. Dates are absolute (YYYY-MM-DD).
## Unreleased

- `coder-ai-os setup`/`sync` now re-sync the snapshot and atlas INDEX as their LAST step. Setup
  writes agent files (`AGENTS.md`, `.claude/`, `.codex/`, …) after generating the first snapshot,
  and the structural fingerprint counts untracked files — so a brand-new setup reported
  "snapshot STALE" on its very first `--check`/`doctor` run, teaching agents to distrust a map
  that was actually correct.
- Default reply language is now `english` rather than `match-user`; set `user.language` in
  `config/local.yaml` to reply in the language of the latest message instead.
- Symbol indexes now mirror the repo's folder structure: `packages/user-api` →
  `.ai/symbols/packages/user-api.tsv` (was flat `packages--user-api.tsv`). Real paths are
  unique, so mirroring them keeps the collision-safety of the path-derived scheme while
  reading naturally for humans and agents. `--symbols-all` GC auto-migrates: old flat
  generated files are removed on the next sync, and a physical-path guard refuses writes
  that a symlinked intermediate dir would redirect outside `.ai/symbols/`.
- INDEX.md IS the root map: the repo root no longer gets a separate `root.md` — unclaimed
  root-level files (dot-dir tooling excluded) appear as a section inside `INDEX.md`, which
  in a clean monorepo simply doesn't exist. `--symbols .`/`--check` target INDEX; `--refresh`
  folds root-level changes into it. Every generated map header now leads with QUERY
  recipes (rg one-liner, sed section reads) so agents never load a whole map — a symbol
  lookup costs 1 line, not a 12 KB file.
- Code atlas: the symbol indexes are now a hierarchy of MARKDOWN maps — no more .tsv.
  `.ai/symbols/INDEX.md` (L1: packages + manifest dependency mermaid) → `<unit>.md`
  (L2: folder import graph with measured counts, folder table, root-file symbol tables)
  → `<unit>/<folder>.md` (L3: file import graph + per-file name·kind·line tables; a
  folder subtree with ≥8 owned files gets its own map, `MD_MIN_FILES` tunes it). Edges
  come from PARSED IMPORTS (python absolute/relative, JS/TS relative + workspace names)
  resolved against the repo file list — exact counts, verified against independently
  measured fixtures (aiila user-api: api→use_cases = 155 imports). L1 lookup is
  `rg -w '<name>' .ai/symbols/`. Old .tsv indexes and graph files GC automatically at
  the next sync; `--baseline`/`--diff`/`--changed`/`--check` semantics carry over.
- `--graph <dir>` now ENRICHES the unit's maps in place: used-by columns are word-match
  CONFIRMED BY IMPORT EDGES (kills same-name noise), and unreferenced symbols are
  classified test / entry (decorator above the def) / private before anything lands in
  the unit map's "needs verification" list — never a bare "(unused)" claim.
- Map regeneration now reports its DELTA: whenever a map is rewritten — by an AI
  (`--refresh`) or a human (`--symbols`, `--symbols-all`) — a `Δ` line shows old vs new
  (`symbols 54→55 (+1 −0: +name) · edges +1 −0`, weight changes count; up to 4 names
  shown). Silent when unchanged or metadata-only; printed on stderr so the GC's
  written-paths protocol on stdout stays clean.
- New `--refresh [<dir>]`: the task-completion trigger — diffs the working tree vs git
  HEAD, maps changed files to their owning units, regenerates exactly those maps +
  INDEX, and prints the trace. Wired into the task-lifecycle skill (step 5) and the
  always-loaded Orient guidance.
- Indexer fixes: dot-directories (`.claude/`, `.lefthook/`) are no longer treated as
  code units, and a nested manifest whose name can't resolve (stray `setup.py` deep in
  another package) no longer fabricates a phantom unit.
- Superseded within this release (never shipped): `--graph <dir>`: derives the unit's usage graph — which symbol is used where —
  writing `<unit>.graph.tsv` (name → defined → uses, capped at 12 shown locations) and
  `<unit>.graph.md` (mermaid: nodes grouped by defining file, each usage attributed to
  its nearest enclosing definition, symbol→symbol edges prioritized under the 150-edge
  cap, plus a "possibly unused" list). Heuristic word-match by design (imports/comments
  count as usage, same names conflate, dunders skipped); on-demand only — never at sync
  (5,570 symbols graph in ~1.7s). Orphaned graphs are GC'd when their unit's index goes.
- Symbol ownership is now EXCLUSIVE: a unit's index covers only files no nested unit
  claims, so each file appears in exactly one .tsv and `root.tsv` holds just the unclaimed
  leftovers instead of re-indexing the entire repo (a real monorepo's root.tsv dropped from
  22,697 rows to 0). Repo-wide L1 lookup is `rg -w '<name>' .ai/symbols/`; the .tsv headers
  and the always-loaded Orient text teach the new read pattern.
- Codex: no sandbox settings are written anywhere anymore, by owner decision — no
  `sandbox_mode` in the defaults, profiles, or generated review-agent TOMLs; Codex picks
  its own sandbox. `approval_policy = "on-failure"` lets it ask to retry a command when
  its sandbox blocks one (Ubuntu 23.10+ blocks bubblewrap's user namespaces, so pinned
  sandbox modes died with `bwrap: ... RTM_NEWADDR` before running anything). The interim
  AppArmor helper script and the install/doctor sandbox warnings were removed with it.
- Claude deny rules: dropped the `Write(...)` variants from `claude/permissions.json` —
  Claude Code only matches `Edit(path)` rules for file-editing tools (Edit/Write/
  NotebookEdit), so the `Write(...)` entries were silent no-ops that produced startup
  warnings; the `Edit(...)` twins already enforce the block.
- Setup/sync now auto-generates ALL unit symbol indexes (`--symbols-all`): every unit the
  snapshot lists — manifest packages, bare dirs, source root, repo root — gets its
  `.ai/symbols/<unit>.tsv` at sync time (empty units are legal, header-only).
- `--symbols-all` garbage-collects orphaned indexes (renamed/removed units) so no stale map
  survives to mislead an agent; only files carrying the generated header are touched.
- Hardening from four-lens review: symbol index filenames are now path-derived and
  collision-safe (`packages/api` → `packages--api.tsv`; two packages both named "api" can no
  longer overwrite each other's index); `.ai`/`.ai/symbols`/`.baseline` symlink guards; doctor
  runs the tool's own script copy (never repo-authored code); `--changed` no longer dies under
  `set -e` when nothing changed; NUL-delimited xargs survives filenames with spaces;
  `--symbols-all` indexes in-process (was one full `git ls-files` per unit); portable awk
  lookup hint replaces `grep -P`.
- Memory scope awareness: the snapshot header now declares whether `.ai/` is SHARED
  (tracked — commit checkpoints with features), LOCAL-ONLY (git-ignored or `.git/info/exclude`
  — never git-add, teammates can't see it), or untracked-so-far (human decides). Protocol §4's
  "commit .ai/" rule is now conditional on this scope. Detected live per regeneration.
- The always-loaded Orient section now teaches the symbol workflow directly: L1 lookup =
  grep `.ai/symbols/<unit>.tsv` (units listed in the snapshot), track your own edits with
  `--changed <dir>`, refresh stale indexes with `--symbols <dir>` — within the 3 KB budget.
- `--changed` works from nested scopes inside a bigger git repo (`git diff --relative` +
  cwd-relative `git show`) — previously subdir runs misreported files as removed.
- New `--changed [<dir>]` mode: symbol-level diff of the working tree vs git HEAD — AI
  triggers it per file/dir to see added/removed/moved functions with no saved baseline
  (git is the baseline; body-only edits stay with `git diff`). Wired into task-lifecycle.
- Nested-project support + tree-style flow graph: the snapshot detects the source root
  (longest common source-file prefix), so wrapper layouts like `src/sat/backend/...` map
  their real modules from any sync point; units keep full repo-relative paths so `--symbols`
  commands stay copy-pasteable. The module flow now also renders as an indented tree with
  `<- cycle` marks — architecture loops become visible instead of hidden in flat edge lists.
- Per-unit symbol index (L1): `scripts/update-ai-context.sh --symbols <dir>` writes
  `.ai/symbols/<unit>.tsv` (file→kind→name→line; locations only, never code content) for ANY
  directory — manifest package, bare source dir (no pyproject/package.json needed), or repo
  root — generated on demand per unit, never the whole app. Content fingerprint + `--check`
  refuses stale line numbers; uses universal-ctags when installed, zero-dep regex fallback
  otherwise (definitions only — call edges stay in the queryable code index). `--baseline` +
  `--diff` give a task-boundary structural diff (+added/-removed/~moved) so out-of-scope or
  unintended changes are caught mechanically; wired into the task-lifecycle, debugging, and
  monorepo-change skills, with two-way links between snapshot rows and index headers.
- Module-flow graph: the snapshot now renders a layered top-level import graph
  (`[entry] api / [mid] services / [base] db` + counted edges) aggregated from import/use/
  require lines — a human-readable architecture map agents orient from instead of reading
  files. File-level by design; function/class callers/impact stay in the queryable code index.
- Workspace-aware orientation: `scripts/update-ai-context.sh` now emits a heuristic monorepo
  map (nested `package.json`/`Cargo.toml`/`pyproject.toml`/`go.mod`/… → package name + internal
  deps) so agents pick the owning package without scanning, plus a structural path-list
  fingerprint and a `--check` mode (fresh=0 / STALE=1) that agents and `coder-ai-os doctor` run
  before trusting the map. `.ai/` meta files are excluded from project shape; untracked
  non-ignored files now count. Still zero-dependency (bash + coreutils).
- Verbosity is now behavioral, defaulting to `medium`: the compiler renders mid-task progress
  narration (one-line intent before the first action + a note per phase change) at
  `medium`/`high` and omits it at `low`; unknown values fail `--check`/`--doctor`/build.
- Token/context visibility ships as harness UI, never model self-report: install merges a
  Claude Code `statusLine` (live context %, additive, user's own wins) and a Codex
  `[tui].status_line` (identifiers verified against codex-cli 0.144.4) via the existing
  user-preserving idempotent TOML merge.
- Understand-first floor (protocol §1a) — a gate no tier can skip, closing the misclassified
  "simple task" failure mode: one-line intent before the first edit, never edit an unread file,
  verify claimed behavior before fixing it, escalate the tier on contradicting evidence, and a
  minimum observed validation even for trivial changes. Compiled pointer ships in all five
  adapters; sync test asserts it renders.
- Provider-native Claude/Codex architecture verified against current official docs: portable skills
  compile to `.claude/skills` and `.agents/skills`; Codex read-only agents compile to
  `.codex/agents`; obsolete Codex-only repair/prompts are retired.
- Skills are now valid canonical Agent Skill bundles with strict name/description/token validation,
  complete resource copying, Codex UI metadata, and a cross-provider `task-lifecycle` resume skill.
- Setup and sync now seed a shared `.ai/PROJECT_NAVIGATOR.md` for unfamiliar projects. Claude,
  Codex, and the other generated adapters use it to keep the relevant flow, connected files, AI
  limits, task graph, and human decision points visible; refresh preserves maintained content.
  It aligns intended outcomes before action and records expected/observed evidence, uncertainty,
- Startup context remains under 3 KB; feature/epic investigation can use isolated read-only agents,
  while writes remain serial and portable `CURRENT.md` checkpoints survive provider/model switches.


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
  discovery) and installs into `~/.claude/skills` **and** `~/.agents/skills` — same progressive
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
