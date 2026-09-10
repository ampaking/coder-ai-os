# Visual Autonomy Loop (VAL)

`coder-ai setup` and `sync` inspect the target repository for UI signals and add a small
`.coder-ai/val/` runtime configuration, setup guide, and wrapper. Existing VAL configuration is
never overwritten. The visual loop is reached through `coder-ai val` or the project wrapper; Playwright, axe-core, and
pixelmatch stay in VAL's pinned Docker image or isolated user cache—nothing is added to the
application's package manifest, lockfile, Python environment, or vendor directory.

```sh
cd ~/your-app
coder-ai setup
# Later, refresh generated integration files with: coder-ai sync
./.coder-ai/val/run doctor
./.coder-ai/val/run run --task pricing-card --prompt "Make pricing cards stack below 768px"
```

Agents are always instructed to use the project-local `./.coder-ai/val/run` wrapper. For a human at a terminal, `coder-ai val ...` delegates to that local wrapper when the
current project has one.

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

