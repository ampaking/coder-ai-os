# coder-ai-os

### Every AI coding agent — working the way *you* do.

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Bash](https://img.shields.io/badge/shell-bash-4EAA25?logo=gnubash&logoColor=white)
![Python 3](https://img.shields.io/badge/python-3.8%2B-3776AB?logo=python&logoColor=white)
![Zero dependencies](https://img.shields.io/badge/deps-zero-success)
![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)

**Works with:** Claude Code · OpenAI Codex · Cursor · GitHub Copilot · Google Gemini —
one config compiled to `CLAUDE.md`, `AGENTS.md`, `.cursor/rules`, `copilot-instructions.md`, and `GEMINI.md`.

Your standards, your guardrails, how you like changes explained: coder-ai-os captures how you
engineer **once** and compiles it into every AI coding tool. Claude, Codex, Cursor, Copilot,
and Gemini all behave like you — instead of being set up five different ways.

**One profile in → every agent out.** Any language, any framework. A **harness compiler** for
AI coding agents — not another AGENTS.md generator.

### Set it up once — every AI tool obeys

You describe how you work **one time**. coder-ai-os compiles that into the native config file each
AI coding tool already reads — so you never configure Claude, Codex, Cursor, Copilot, and Gemini
five separate ways (and never keep them in sync by hand).

```
                    ┌──────────────────────────────┐
   how you work  →  │   config/*.yaml  (edit once)  │
   guardrails       └──────────────┬───────────────┘
   your style                      │  bin/compile
   reply format                    ▼
        ┌───────────────┬──────────┴─────┬───────────────┬───────────────┐
        ▼               ▼                ▼               ▼               ▼
   Claude Code     OpenAI Codex       Cursor      GitHub Copilot     Gemini
   CLAUDE.md        AGENTS.md       .cursor/rules   copilot-…md      GEMINI.md
```

Change one setting → re-run once → **all five agents update together.** Guardrails (no `.env`
reads, no `git push`, no sudo/deploys) are compiled to the top of every file and can't be
weakened.

> Grounded in harness-engineering research: the *harness* — instructions, tools, guardrails,
> memory — not the model, is what makes an agent reliable ([arXiv:2602.14690](https://arxiv.org/pdf/2602.14690)).

## Core capabilities

- **Compile Once** — turn one profile into each tool's native config (`CLAUDE.md`, `AGENTS.md`, `.cursor/rules`, Copilot instructions, `GEMINI.md`)
- **Enforce Guardrails** — hard, always-on limits (no `.env`/secrets, no `git push`, no sudo/deploy, no unrelated refactors) compiled to the top of every file
- **Match Your Style** — learn the repo's own Prettier/ESLint/tsconfig, Black/Ruff/mypy config into standards agents follow before writing code
- **Remember Across Agents** — file-based memory, so switching Claude ↔ Codex ↔ Cursor loses nothing; commit `.ai/` to share resume state with your team, or gitignore it to keep it machine-local — agents detect which and behave accordingly
- **Navigate Together** — a shared map aligns intent before action, connects relevant files/tasks, exposes AI limits and human decisions, and records mistake recovery
- **Trigger Everywhere** — commands (`/plan`, `/review`, `/task`, `/snapshot`) and lazy-loaded skills in every CLI's native format
- **Navigate Monorepos** — fix the owning package first, trace dependents, fix each in isolation, then integrate at the root
- **Orient Without Scanning** — sync generates a layered map of any repo (nested layouts and no-manifest folders included): a module-flow tree with cycle marks, per-package symbol indexes (`function → file:line`, grepped not loaded), staleness fingerprints, and `--changed` to see added/removed functions vs git HEAD
- **Fail-Safe Understanding** — an understand-first floor no task can skip: state intent, never edit an unread file, verify claimed behavior, escalate when evidence contradicts the plan; a symbol diff at task end flags out-of-scope edits
- **See Token Use Live** — install ships Claude Code and Codex status lines (context % / token counters, harness-measured); your own statusline config always wins
- **Reports Your Way** — every task ends with a goal review in your reply format (flow-graph, prose, or minimal); the final review isn't clipped by the token budget
- **Verify & Budget** — a small, budget-enforced instruction block; prove what every CLI actually loads with one command
- **Close the Visual Loop** — VAL automatically captures deterministic responsive screenshots after UI diffs, runs accessibility/layout/pixel checks, applies allowlisted fixes, and emits one evidence-linked report without installing anything in the application repo
- **Remember Project Tasks Privately** — setup/sync enables content-free task lifecycle and validation evidence in that project's ignored local SQLite database; prompts, source, paths, and terminal output stay excluded

## What's new

Highlights of the current release. Full detail in the [changelog](CHANGELOG.md).

- **Code atlas instead of file scanning** — `.ai/symbols/` is now a hierarchy of Markdown maps:
  `INDEX.md` (packages + dependency diagram) → `<unit>.md` (folder import graph, counted edges)
  → `<unit>/<folder>.md` (per-file symbol tables). Edges come from *parsed imports*, not guesses.
  A symbol lookup costs one line — `rg -w '<name>' .ai/symbols/` — instead of loading a whole file.
- **Know what you changed** — `--changed` shows added/removed/moved functions vs git HEAD with no
  saved baseline, `--refresh` regenerates exactly the maps your edits touched and prints the trace,
  and `--check` tells you whether a map is still fresh before an agent trusts it.
- **Resume any task, any agent** — the new `/task` command and `task-lifecycle` skill carry a task
  across sessions, context compaction, and tool switches using checked-in state, so Claude → Codex
  mid-task loses nothing.
- **A shared human–AI map** — every repo gets `.ai/PROJECT_NAVIGATOR.md`: intended outcome, current
  flow, connected files, AI limits, open decisions, and mistake recovery. Your edits survive `sync`.
- **Codex the way Codex works** — read-only review agents compile to `.codex/agents`, skills to
  `.agents/skills`, and setup uses Codex's portable `:workspace` permission profile rather than
  pinning a platform-specific sandbox mode. Existing user permission choices always win.
- **Live context in the status line** — install adds a Claude Code and Codex status line showing
  real context use, measured by the harness rather than guessed by the model. Your own config wins.
- **An understand-first floor** — no tier can skip it: state intent before the first edit, never
  edit an unread file, verify claimed behavior before fixing it, and escalate when evidence
  contradicts the plan.
- **Three more review roles** — `architecture`, `explorer`, and `performance` join `reviewer` and
  `security` in both Claude and Codex formats.
- **Quieter, more useful defaults** — verbosity is behavioral and defaults to `medium` (a one-line
  intent up front plus a note per phase change), and Claude's deny rules dropped the `Write(...)`
  entries that were silent no-ops causing startup warnings.

Already using coder-ai-os? Run `./install.sh` once, then `coder-ai-os sync` in each repo — managed
regions update while your guidance, memory, and navigator content stay untouched.

## Documentation & installation

**Easiest — one line.** Installs and configures every agent in one command:

```sh
curl -fsSL https://raw.githubusercontent.com/ampaking/coder-ai-os/main/bootstrap.sh | sh
```

Then personalize interactively whenever you like: `coder-ai-os init`.

**Or clone and run** (identical result; the first run offers the guided setup):

```sh
git clone https://github.com/ampaking/coder-ai-os.git ~/coder-ai-os
cd ~/coder-ai-os
./install.sh            # first run offers the guided, one-minute setup
./install.sh --init     # jump straight to the wizard
```

### How it works — two steps

coder-ai-os is split into a **global** layer (how *you* work) and a **per-repo** layer
(a project's languages, standards, commands). You run each once:

```sh
# 1. GLOBAL — run once per machine, from the clone. Installs your behavior into
#    ~/.claude, ~/.codex, ~/.gemini AND creates the `coder-ai-os` command
#    (symlinked into ~/.local/bin).
cd ~/coder-ai-os && ./install.sh

# 2. PER-REPO — run inside any project. Keeps generated runtime state clone-local.
cd ~/your-app && coder-ai-os setup
```

To safely refresh the complete harness in an existing repository, use one command:

```sh
cd ~/your-app && coder-ai-os sync
```

After sync, use the normal native CLI. The model selected by that CLI remains the controller:

```sh
cd ~/your-app
claude
# or
codex
```

Then provide the complete task normally. For feature/epic work, the generated instructions make that
session define acceptance criteria, create ordered atomic tasks, keep writes serial, validate each task,
and request an independent review before moving on.

Same-provider work uses native subagents. Cross-provider work invokes the other installed native CLI
directly: Claude can run `codex exec`, and Codex can run `claude -p`. No `coder-ai-os run`, daemon,
router, or MCP bridge is required. The opposite provider reviews in read-only/plan mode; findings return
to the implementing context for repair, validation, and re-review.

The parent does not invent unavailable model names: an explicitly configured model may be used, otherwise
the delegated CLI uses its own configured default. If the other CLI is unavailable or unauthenticated, the
session reports that cross-provider review was not performed and uses a fresh same-provider reviewer.
Only material security/data/API/schema/production decisions pause for the human; safe reversible ambiguity
is recorded as an assumption and work continues. The final response remains a human-reviewable evidence
report, not an automatic commit or deployment.

Native fallback is bounded: retry one transient timeout/429, optionally try a configured known model,
then return to a fresh same-provider subagent/context. A missing, unauthenticated, quota-exhausted, or
user-disabled second provider does not block ordinary work. Every handoff is preceded by a compact
`CURRENT.md` checkpoint, so a fresh normal Claude or Codex session can resume. A native CLI cannot replace
its own already-terminated parent process; automatic failover at that exact boundary would require an
external controller, which coder-ai-os deliberately does not pretend the normal CLI provides.

`sync` never injects into repository-owned `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, including package-level
files. It puts its compiled guidance in `.coder-ai/local/INSTRUCTIONS.md`; the global native instructions
load that sidecar after project guidance, and `.git/info/exclude` keeps it clone-local without changing
the shared `.gitignore`. Sync also removes only legacy coder-ai-os marker blocks from project instruction
files, so upstream changes remain visible and pulls do not conflict with local generated guidance.

`sync` keeps user guidance, Codex overrides,
`.ai/memory/` task state, and the maintained `.ai/PROJECT_NAVIGATOR.md`. Missing navigators are
seeded for projects configured by older versions. It also additively creates or updates `.codex/config.toml`, preserving
existing top-level Codex values and unrelated TOML content. Every supported agent follows the same tiered `AI_DEV_PROTOCOL.md`: plan with intent and Definition
of Done, CURRENT→NEW graph, one isolated task at a time, validation checkpoints, review-only passes,
and a final diff-to-goal audit. English remains the default unless explicitly
requested otherwise.

Codex keeps its host-selected sandbox so the same generated harness works across Linux, macOS,
Windows, containers, and restricted runners; coder-ai-os adds `default_permissions=":workspace"`
and the current interactive `approval_policy="on-request"` only when the user chose neither a
permission profile nor sandbox mode. Routine project-local work proceeds inside the workspace;
attempts outside it still require the host's approval. Claude uses auto mode with hard deny rules. Existing user or
project safety values always win, and coder-ai-os never enables unrestricted execution automatically.

Standing approvals remain native and private. In Claude CLI, choosing **Yes, and don't ask again** writes
the narrow allow rule to `.claude/settings.local.json`; sync excludes and never modifies that file. In Codex,
accepting an allow-list proposal writes the prefix to `~/.codex/rules/default.rules`; coder-ai-os never
overwrites it. Sync pre-allows only coder-ai-os's exact atlas helper and cross-provider review prefixes.
Project test/lint commands may ask once, then the native CLI owns the reviewed persistent rule. Broad shell,
package-manager, Git-write, destructive, secret, and production permissions are never learned automatically.

> **`coder-ai-os: command not found`?** The `coder-ai-os` command only exists *after* step 1
> (global `./install.sh`) — that step creates the symlink. If it's still not found, `~/.local/bin`
> isn't on your `PATH` (add `export PATH="$HOME/.local/bin:$PATH"` to your shell rc), or just call
> it by path: `~/coder-ai-os/install.sh --project "$PWD"` (equivalent to `coder-ai-os setup`).

Configure any way you like — all produce the same profile:

```sh
coder-ai-os init --interactive        # profile wizard + initialize the current Git project
coder-ai-os init --ai claude|codex    # AI profile interview + initialize the current Git project
coder-ai-os init --from profile.yaml  # apply a saved profile + initialize the current Git project
./install.sh --project ~/path/to/repo # set up a repo (rules, commands, skills, standards, memory)
```

Docs, architecture, config schema, and the research behind it 👉
[Architecture](ARCHITECTURE.md) · [Config schema](config/README.md) ·
[Setup flows](docs/intake.md) · [Harness coverage](docs/harness-coverage.md) ·
[Development protocol](protocol/AI_DEV_PROTOCOL.md)

Confirm everything is wired: `./install.sh --status` and `coder-ai-os verify`.

## Project Tasks

Project Tasks is a local record of work performed with coding agents. `coder-ai-os setup`, `sync`,
and direct project installation enable it together with content-free automatic observations.
It stores structured summaries and AI-session metadata in `.coder-ai/tasks/tasks.sqlite3`, which
setup adds to the project's `.coder-ai/.gitignore`. Raw prompts, source contents, secrets, commit
messages, file paths, authors, and employee scores are not collected.

```sh
coder-ai-os tasks enable                  # enable tasks + safe automatic collection
coder-ai-os tasks status --json
coder-ai-os tasks brief --depth quick     # 30-second onboarding; working/deep are also available
coder-ai-os tasks review                  # evidence for the last seven days
coder-ai-os tasks open                    # launch the black localhost UI only when requested
coder-ai-os tasks open --demo             # disposable rich preview; real database unchanged
coder-ai-os tasks close                   # stop this project's dashboard from another terminal
coder-ai-os tasks git-links               # review optional task/commit candidates
coder-ai-os tasks doctor                  # read-only SQLite integrity check
coder-ai-os tasks repair --yes            # preserve corruption, then create clean local state
coder-ai-os tasks export                  # safe structured export; session IDs excluded
coder-ai-os tasks collect disable         # stop automatic hooks until the next sync
coder-ai-os tasks disable                 # disable tasks until the next setup/sync; preserve history
coder-ai-os tasks delete --yes            # delete this project's task state
```

The compiled instructions give every supported agent the same local-only boundary; Claude Code and
Codex additionally receive the native `project-tasks` skill with the full lifecycle contract. Claude's
global Stop hook records only that a response ended; it never blocks another response or infers task
completion. Explicit task and validation events own lifecycle state. `tasks open` starts a loopback-only
server for that request and stops on exit. Notifications use an explicitly installed, short-lived local scheduler:
the operating system wakes one bounded analysis/delivery command at 18:00 local time, then it exits.
Optional Git collection is off by default and contains only hashes, parents, timestamps, file counts,
and aggregate additions/deletions; candidates require confirmation or rejection in the UI or CLI.
It is supporting evidence, never a measure of engineering value. The default UI theme is black,
keyboard-accessible, and responsive; theme emulation never changes the stored project preference.
The Now, Map, and Review period navigator selects Day, Week, Month, or Year, shows the exact date
range, and moves backward through project history.
The Personal Project Guide explains structured evidence without storing questions. Optional
Codex and Claude buttons run only after one visible confirmation and open the selected agent in an
external terminal using read-only/plan-only modes; the dashboard does not store model responses.

## Visual Autonomy Loop (VAL)

`coder-ai-os setup` and `sync` inspect the target repository for UI signals and add a small
`.coder-ai/val/` runtime configuration, setup guide, and wrapper. Existing VAL configuration is
never overwritten. The global installer links the `val` command; Playwright, axe-core, and
pixelmatch stay in VAL's pinned Docker image or isolated user cache—nothing is added to the
application's package manifest, lockfile, Python environment, or vendor directory.

```sh
cd ~/your-app
coder-ai-os setup
# Later, refresh generated integration files with: coder-ai-os sync
./.coder-ai/val/run doctor
./.coder-ai/val/run run --task pricing-card --prompt "Make pricing cards stack below 768px"
```

Agents are always instructed to use the project-local `./.coder-ai/val/run` wrapper. For a human
at a terminal, `coder-ai-os val ...` remains an optional convenience and delegates to that local
wrapper when the current project has one.

In a monorepo, setup writes `.coder-ai/val/apps.json` plus one isolated config and runtime state
directory per detected web package. Select the application explicitly when more than one exists:

```sh
./.coder-ai/val/run --app apps-admin doctor
./.coder-ai/val/run --app apps-admin run --task workflow-modal
```

Detection supports package scripts regardless of Node/TypeScript age, Django, Rails, Laravel,
static HTML, Docker, and Compose. The generic `node` driver name means “run the configured serve
command”; it is not a Node dependency. For an already hosted application, set `env` to `remote`,
set its absolute `url`, and leave the remote process read-only. Edit `.coder-ai/val/config.json`
when automatic detection cannot know a service, port, route, or command.

For Claude Code, the installed project `PostToolUse` prompt checks edited paths against
`watchGlobs` and instructs the agent to run the loop as an isolated validation task. Other supported
agents receive the same behavior through the installed VAL skills when their skill dispatcher
recognizes a UI change. The current UI request is passed through `--prompt` for checklist extraction.
A run produces `.coder-ai/val/runs/<task>/manifest.json`, round evidence,
`final/shots/`, and `final/report.md`. Exit codes are `0` green, `1` remaining findings/manual
criteria, `2` capped/escalated with `BLOCKED.md`, and `3` infrastructure failure. Baselines are
human-controlled and are promoted only with `val baseline promote --run <task>` after review.

For detected package-based web apps, setup creates a test-only fixture scaffold at
`.coder-ai/scripts/val-fixtures/<app-id>.mjs`. It is intentionally fail-closed until the project
defines its local browser storage state and exact deterministic API responses. Package metadata can
identify a framework, but cannot safely infer an application's roles, session format, or authorization
rules. The adapter may write only into its isolated `.coder-ai/val/` app state; VAL rejects symlinks,
paths outside that state, malformed routes, and any silent fallback to production credentials.

Legacy scripted authentication remains available when fixture mode is disabled. Configured fill values must be exact environment placeholders
such as `${VAL_USER}` and `${VAL_PASS}`; literal credentials are rejected. VAL masks password
fields in login evidence, saves state under `.coder-ai/val/` with mode `0600`, and attempts login
at most twice. For MFA/captcha, run `val auth --manual`; VAL opens a local headed browser, waits
for `successCheck`, and reuses the saved state afterward. Unattended login failures return exit `3`
instead of risking an account lockout.
For OAuth or external identity-provider redirects, list the exact trusted origins in
`auth.allowedOrigins`; all other third-party requests remain blocked for deterministic captures.

VAL requires Bash 4+, `jq`, `curl`, `awk`, and `git`; Docker is preferred for browser isolation,
with local `npm`/`npx` cache fallback. Windows is supported through WSL or Git Bash, not native
Command Prompt or PowerShell process management.

## Make it yours

Everything is a setting. Change your reply format, verbosity, token budget, or languages — just
re-run the interview:

```sh
coder-ai-os init --interactive     # re-apply globally + initialize the current Git project
```

Or set them directly in a per-machine overlay (`config/local.yaml`), then `./install.sh`:

```yaml
user:
  verbosity: low               # low · medium (default) · high — medium adds mid-task progress notes
  languages: [Python, TypeScript]
tokens:
  reply_format: minimal        # flow-graph · prose · minimal  (behavioral — minimal is really shorter)
  max_tokens: 300              # caps mid-task replies; the end-of-task goal review is exempt
```

Guardrails are the one thing you *can't* weaken here — they're always on. Full list of settings
in the [config schema](config/README.md).


## Created by Nobin Khandaker

Built to make AI-assisted development consistent across every tool — one engineering identity,
every agent. Contributions welcome: [Contributing](CONTRIBUTING.md) ·
[Code of Conduct](CODE_OF_CONDUCT.md) · [MIT License](LICENSE).
