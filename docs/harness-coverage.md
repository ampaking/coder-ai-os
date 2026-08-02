# Harness coverage

Where `coder-ai-os` stands against the configuration taxonomy in *"Harness Engineering for
Agentic AI Coding Tools"* (arXiv:2602.14690, 2,853 repos, 5 tools). The paper's thesis: the
harness — not the model or a single Markdown file — is what shapes agent behavior.

Legend: ● done · ◐ partial · ○ missing.

## The 8 configuration mechanisms — all covered
| Mechanism | Paper adoption | Coverage | Provided by |
|---|---|---|---|
| Context files | dominant, often sole | ● 5 of 5 tools | compiler → CLAUDE/AGENTS/GEMINI + Cursor/Copilot via `--project` |
| Tool permissions | common | ● Claude deny-list + Codex sandbox | `claude/permissions.json`, `codex/config.defaults.toml`, `config/safety.yaml` |
| MCP servers | moderate | ● registry + auto-register | `config/mcp.yaml` → `--with-mcp` registers each per tool (claude mcp add · codex toml · gemini/cursor JSON); CodeGraph via `--with-codegraph` |
| Hooks | rare | ● agent Stop hook | `config/hooks.yaml` → `.claude/settings.json` Stop hook + `HOOKS.md`; no Git-hook mutation |
| Skills | rare | ● lazy-loaded, Claude + Codex | `config/skills.yaml` + `skills/<id>/SKILL.md` (frontmatter) → `~/.claude/skills` + `~/.agents/skills` |
| Subagents | very rare | ● generated | `config/subagents.yaml` → Claude `.claude/agents/` + Codex `.codex/agents/` |
| Task commands | rare | ● native where repo-scoped | `config/commands.yaml` → Claude/Gemini/Cursor; Codex uses `AGENTS.md` + protocol |
| Memory lifecycle | emerging | ● policy + convention | `config/memory.yaml` → maintained `.ai/PROJECT_NAVIGATOR.md` + resumable `.ai/memory/` |
| Status display | not in paper | ● harness UI, not model text | install ships Claude `statusLine` (live context %) + Codex `[tui].status_line` (context/tokens); user values win. Models cannot introspect token counts — visibility is a display concern, paired with `verbosity: medium` progress narration |

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
the snapshot, shared project navigator, and code index — retrieved on demand. The navigator joins
intent, relevant file flow, AI limits, atomic tasks, human decisions, and evidence-based mistake recovery without mapping unrelated code.

## Status
All 8 paper mechanisms and all 7 engines are covered, plus one harness addition beyond the
paper: status display (live context/token visibility as harness UI). Language layering
(`config/profiles/<lang>.yaml` merged by `user.languages`) and idempotent settings hook merging are
in; full global→repo→task configuration layering and comparable task telemetry remain future depth.

## OpenAI Engineering transfer audit

The Engineering newsroom contains both reusable agent-harness practices and OpenAI service
infrastructure. coder-ai-os adopts the practices that belong in a portable local harness:

- **Implemented:** repository knowledge as the system of record; small always-loaded guidance;
  progressive-disclosure skills; query-first code maps; durable task checkpoints; explicit agent
  loops; independent read-heavy subagents; atomic validation/review; provider adapters; generated
  atlas garbage collection; additive prompt-only validation hooks.
- **Intentionally bounded:** memory improves only through reviewed Markdown updates, never
  unreviewed self-modification; architecture maps and reviewers expose boundary violations while
  project-native linters/tests remain the enforcement authority; browser, logs, metrics, and live
  external data are optional MCP/app integrations rather than assumed global access.
- **Not a harness concern:** MRC networking, WebRTC voice delivery, Responses API WebSockets and
  containers, PostgreSQL scaling, and product credit/rate-limit systems. Codex App Server becomes
  relevant only if coder-ai-os later ships a rich client or remote agent service.

This boundary prevents copying product-specific infrastructure into a configuration compiler while
preserving the transferable reliability lessons. Claims about token, time, or task-success gains
remain unquantified until comparable task telemetry exists.

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
