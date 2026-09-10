# coder-ai

### Every AI coding agent — working the way *you* do.

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Bash](https://img.shields.io/badge/shell-bash-4EAA25?logo=gnubash&logoColor=white)
![Python 3](https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white)
![Zero dependencies](https://img.shields.io/badge/deps-zero-success)
![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)

Describe how you work **once**. It compiles into the config file every AI coding tool already
reads — so Claude, Codex, Cursor, Copilot and Gemini all behave like you.

```
                    ┌──────────────────────────────┐
   how you work  →  │  config/*.yaml  (edit once)  │
   guardrails       └──────────────┬───────────────┘
   your style                      │  bin/compile
   reply format                    ▼
        ┌───────────────┬──────────┴─────┬───────────────┬───────────────┐
        ▼               ▼                ▼               ▼               ▼
   Claude Code     OpenAI Codex       Cursor      GitHub Copilot     Gemini
   CLAUDE.md        AGENTS.md       .cursor/rules   copilot-…md      GEMINI.md
```

---

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/ampaking/coder-ai-os/main/bootstrap.sh | sh
cd ~/your-app && coder-ai setup
```

Then use `claude` / `codex` / `cursor` as normal — they follow your rules now.
Refresh a project any time with `coder-ai sync`; it never overwrites your own notes.

**Needs** macOS or Linux (WSL on Windows) · Python 3.9+ · git · `gh` for PR automation

<sub>`command not found`? `~/.local/bin` isn't on your `PATH`. Older installs have `coder-ai-os`;
it still works, but everything is documented as `coder-ai`.</sub>

---

## Day to day

```sh
coder-ai status                 # where am I? project, agents, current task
coder-ai find handleRetry       # locate code without reading files
coder-ai prove                  # what is actually verified — evidence, not claims
coder-ai ship --dry-run         # deliver by this project's own steps
```

---

## Everything it does

**Set your standards** — the core

| | | |
|---|---|---|
| 🧩 | One profile compiled into every tool's native config | `coder-ai init` |
| 🛡️ | Guardrails that settings can't weaken | `coder-ai verify` |
| ⌨️ | `/plan` `/review` `/task` in every CLI, plus lazy-loaded skills | `/task` |

**See the code** — [guide](docs/setup.md)

| | | |
|---|---|---|
| 🗺️ | Symbol atlas — find anything without scanning | `coder-ai find <symbol>` |
| 🧠 | Memory that survives switching Claude ↔ Codex | `coder-ai status` |

**Check the work** — [evidence](docs/evidence.md) · [VAL](docs/val.md)

| | | |
|---|---|---|
| 🔎 | A report generated from what actually ran, not what was claimed | `coder-ai prove` |
| 👀 | Screenshots, a11y and pixel checks after UI edits | `coder-ai val doctor` |

**Finish the work** — [delivery](docs/delivery.md) · [PR automation](docs/pr-automation.md)

| | | |
|---|---|---|
| 🚢 | Run this project's own commit → verify → push → PR steps | `coder-ai ship` |
| 🤖 | An agent that watches a PR and fixes it while you work elsewhere | `coder-ai pr 1420 -- claude` |
| 📓 | A private, content-free record of what agents did — [guide](docs/project-tasks.md) | `coder-ai tasks brief` |

---

## Commands

| | |
|---|---|
| `coder-ai init` · `coder-ai install` | your profile · this machine |
| `coder-ai setup` · `coder-ai sync` | set up or refresh a project |
| `coder-ai status` | where am I |
| `coder-ai verify` · `coder-ai doctor` | what each CLI loads · deep health |
| `coder-ai find <symbol>` | locate code in the atlas |
| `coder-ai prove` | what is verified |
| `coder-ai ship` | run this project's delivery workflow |
| `coder-ai pr <n> -- <provider>` | supervise a pull request |
| `coder-ai val` · `coder-ai tasks` | visual loop · project tasks |
| `coder-ai run "<task>"` | one bounded execute → verify → review |
| `coder-ai compile` · `diff` · `profile show` | rebuild and inspect generated output |

Reporting commands take `--json`. Acting commands take `--dry-run`.

<sub>`coder-ai-os` still works for older installs. New documentation uses `coder-ai` only.</sub>

---

## Where things live

| | | Commit it? |
|---|---|---|
| `config/*.yaml` | your profile — the single source | yes |
| `.ai/` | atlas, memory, navigator, plans | your call — agents detect which |
| `.coder-ai/` | runtime: instructions, scripts, evidence, VAL, tasks | no — kept clone-local, except `delivery.yaml` |
| `~/.coder-ai/` | machine state: PR sessions and worktrees | n/a |

Atlas refresh: `.coder-ai/scripts/update-ai-context.sh --changed .`

---

## Make it yours

Rerun `coder-ai init --interactive`, or edit `config/local.yaml`:

```yaml
user:      {verbosity: low, languages: [Python, TypeScript]}
tokens:    {reply_format: minimal, max_tokens: 300}
```

Guardrails are the one thing you can't weaken. Full list: [config schema](config/README.md).

---

## Docs

[Setup](docs/setup.md) · [Evidence](docs/evidence.md) · [Delivery](docs/delivery.md) ·
[PR automation](docs/pr-automation.md) · [VAL](docs/val.md) · [Project tasks](docs/project-tasks.md) ·
[Architecture](ARCHITECTURE.md) · [Protocol](protocol/AI_DEV_PROTOCOL.md) · [Changelog](CHANGELOG.md)

> The *harness* — instructions, tools, guardrails, memory — not the model, is what makes an agent
> reliable ([arXiv:2602.14690](https://arxiv.org/pdf/2602.14690)).

## Created by Nobin Khandaker

[Contributing](CONTRIBUTING.md) · [Code of Conduct](CODE_OF_CONDUCT.md) · [MIT](LICENSE)
