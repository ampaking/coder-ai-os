# Harness coverage

Where `coder-ai-os` stands against the configuration taxonomy in *"Harness Engineering for
Agentic AI Coding Tools"* (arXiv:2602.14690, 2,853 repos, 5 tools). The paper's thesis: the
harness — not the model or a single Markdown file — is what shapes agent behavior.

Legend: ● done · ◐ partial · ○ missing.

## The 8 configuration mechanisms — all covered
| Mechanism | Paper adoption | Coverage | Provided by |
|---|---|---|---|
| Context files | dominant, often sole | ● 5 of 5 tools | compiler → CLAUDE/AGENTS/GEMINI + Cursor/Copilot via `--project` |
| Tool permissions | common | ● Claude deny-list + Codex sandbox | `claude/permissions.json`, `codex/config.autonomous.toml`, `config/safety.yaml` |
| MCP servers | moderate | ● registry + auto-register | `config/mcp.yaml` → `--with-mcp` registers each per tool (claude mcp add · codex toml · gemini/cursor JSON); CodeGraph via `--with-codegraph` |
| Hooks | rare | ● git + real Stop hook | git: `install_repo_hooks`; agent: `config/hooks.yaml` → firing `.claude/settings.json` Stop hook + `HOOKS.md` |
| Skills | rare | ● lazy-loaded, Claude + Codex | `config/skills.yaml` + `skills/<id>/SKILL.md` (frontmatter) → `~/.claude/skills` + `~/.codex/skills` |
| Subagents | very rare | ● generated | `config/subagents.yaml` → `.claude/agents/` |
| Slash commands | rare | ● generated, all 4 CLIs | `config/commands.yaml` → Claude/Codex-prompts/Gemini-TOML/Cursor |
| Memory lifecycle | emerging | ● policy + convention | `config/memory.yaml` → `.ai/memory/` |

**Finding we act on:** advanced mechanisms are rare because they're *hard to configure*, not
low-value. `coder-ai-os` compiles all of them from one kernel — the differentiator. The
always-loaded block stays small (harness detail lives in generated artifacts, loaded on demand);
`coder-ai-os doctor` reports budget, conflicts, skills, and per-mechanism counts.

## The reconciliation (why instructions stay small)
Two efficiency papers appear to conflict: well-designed instructions can cut runtime/tokens
(arXiv:2510.21413), yet large requirement-heavy `AGENTS.md` files reduce task success and
raise cost ~20% (arXiv:2602.11988, 2601.20404). Resolution, and our core philosophy:

> **Don't optimize the instruction file. Optimize the harness.**

Keep the always-loaded block small (budget-enforced <3 KB); push depth into the protocol,
the snapshot, and the code index — retrieved on demand.

## Status
All 8 mechanisms and all 7 engines are covered. Language layering
(`config/profiles/<lang>.yaml` merged by `user.languages`) is in; full
global→repo→task layering and settings.json hook auto-merge are the next depth passes.

## References & thanks

This project's architecture is directly shaped by recent empirical research. Thanks to the
authors — read these to understand *why* `coder-ai-os` is a harness compiler and not an
AGENTS.md generator:

- **Harness Engineering for Agentic AI Coding Tools: An Exploratory Study** — the taxonomy of
  8 configuration mechanisms across 2,853 repos.
  [abs](https://arxiv.org/abs/2602.14690) · [PDF](https://arxiv.org/pdf/2602.14690)
- **Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents?**
  [abs](https://arxiv.org/abs/2602.11988) · [PDF](https://arxiv.org/pdf/2602.11988)
- **On the Impact of AGENTS.md Files on the Efficiency of AI Coding Agents**
  [abs](https://arxiv.org/abs/2601.20404) · [PDF](https://arxiv.org/pdf/2601.20404)
- **Context Engineering for AI Agents in Open-Source Software**
  [abs](https://arxiv.org/abs/2510.21413) · [PDF](https://arxiv.org/pdf/2510.21413)

Core lesson we adopt: **don't optimize the instruction file — optimize the harness.** Keep the
always-loaded block small and budget-enforced; push depth into skills, indexes, and workflow,
retrieved on demand.
