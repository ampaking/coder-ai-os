# Setup, sync, and how sessions actually run

The short version lives in the [README](../README.md); this is the detail.

## Two layers

coder-ai is split into a **global** layer (how *you* work) and a **per-repo** layer
(a project's languages, standards, commands). You run each once:

```sh
# 1. GLOBAL — run once per machine, from the clone. Installs your behavior into
#    ~/.claude, ~/.codex, ~/.gemini AND creates the `coder-ai` command
#    (symlinked into ~/.local/bin).
cd ~/coder-ai-os && ./install.sh

# 2. PER-REPO — run inside any project. Keeps generated runtime state clone-local.
cd ~/your-app && coder-ai setup
```

To safely refresh the complete harness in an existing repository, use one command:

```sh
cd ~/your-app && coder-ai sync
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
directly: Claude can run `codex exec`, and Codex can run `claude -p`. No `coder-ai run`, daemon,
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

> **`coder-ai-os: command not found`?** The `coder-ai` command only exists *after* step 1
> (global `./install.sh`) — that step creates the symlink. If it's still not found, `~/.local/bin`
> isn't on your `PATH` (add `export PATH="$HOME/.local/bin:$PATH"` to your shell rc), or just call
> it by path: `~/coder-ai-os/install.sh --project "$PWD"` (equivalent to `coder-ai setup`).

Configure any way you like — all produce the same profile:

```sh
coder-ai init --interactive        # profile wizard + initialize the current Git project
coder-ai init --ai claude|codex    # AI profile interview + initialize the current Git project
coder-ai init --from profile.yaml  # apply a saved profile + initialize the current Git project
./install.sh --project ~/path/to/repo # set up a repo (rules, commands, skills, standards, memory)
```
