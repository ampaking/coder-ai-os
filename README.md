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

> Grounded in harness-engineering research: the *harness* — instructions, tools, guardrails,
> memory — not the model, is what makes an agent reliable ([arXiv:2602.14690](https://arxiv.org/pdf/2602.14690)).

## Core capabilities

- **Compile Once** — turn one profile into each tool's native config (`CLAUDE.md`, `AGENTS.md`, `.cursor/rules`, Copilot instructions, `GEMINI.md`)
- **Enforce Guardrails** — hard, always-on limits (no `.env`/secrets, no `git push`, no sudo/deploy, no unrelated refactors) compiled to the top of every file
- **Match Your Style** — learn the repo's own Prettier/ESLint/tsconfig, Black/Ruff/mypy config into standards agents follow before writing code
- **Remember Across Agents** — file-based, git-committed memory, so switching Claude ↔ Codex ↔ Cursor loses nothing and you resume where you left off
- **Trigger Everywhere** — commands (`/plan`, `/review`, `/snapshot`) and lazy-loaded skills in every CLI's native format
- **Navigate Monorepos** — fix the owning package first, trace dependents, fix each in isolation, then integrate at the root
- **Reports Your Way** — every task ends with a goal review in your reply format (flow-graph, prose, or minimal); the final review isn't clipped by the token budget
- **Verify & Budget** — a small, budget-enforced instruction block; prove what every CLI actually loads with one command

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

# 2. PER-REPO — run inside any project. Writes that repo's rules, commands,
#    skills, standards, and memory into the repo itself (nothing global).
cd ~/your-app && coder-ai-os setup
```

> **`coder-ai-os: command not found`?** The `coder-ai-os` command only exists *after* step 1
> (global `./install.sh`) — that step creates the symlink. If it's still not found, `~/.local/bin`
> isn't on your `PATH` (add `export PATH="$HOME/.local/bin:$PATH"` to your shell rc), or just call
> it by path: `~/coder-ai-os/install.sh --project "$PWD"` (equivalent to `coder-ai-os setup`).

Configure any way you like — all produce the same profile:

```sh
coder-ai-os init --interactive        # manual wizard
coder-ai-os init --ai claude|codex    # let an AI interview you (draft → you approve → compile)
coder-ai-os init --from profile.yaml  # apply a saved profile
./install.sh --project ~/path/to/repo # set up a repo (rules, commands, skills, standards, memory)
```

Docs, architecture, config schema, and the research behind it 👉
[Architecture](ARCHITECTURE.md) · [Config schema](config/README.md) ·
[Setup flows](docs/intake.md) · [Harness coverage](docs/harness-coverage.md) ·
[Development protocol](protocol/AI_DEV_PROTOCOL.md)

Confirm everything is wired: `./install.sh --status` and `coder-ai-os verify`.

## Make it yours

Everything is a setting. Change your reply format, verbosity, token budget, or languages — just
re-run the interview:

```sh
coder-ai-os init --interactive     # re-run any time; re-applies to every agent
```

Or set them directly in a per-machine overlay (`config/local.yaml`), then `./install.sh`:

```yaml
user:
  verbosity: high              # low · medium · high
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
