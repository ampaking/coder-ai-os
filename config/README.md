# Config schema

The kernel. Every tool file is compiled from these — edit here, then run `bin/compile`
(or `./install.sh`, which compiles first). A per-machine `config/local.yaml` overlay
(gitignored, written by the wizard / AI intake) overrides these defaults; the compiler
**drops any `safety:` key** from the overlay so guardrails can never be weakened locally.

## Files

### `identity.yaml`
| key | type | meaning |
|---|---|---|
| `name` | str | profile name |
| `persona` | str | one-line persona, rendered into the Autonomy section |
| `managed_by` | str | tool that owns the block (`coder-ai-os`) |

### `safety.yaml` — guardrails (non-negotiable, rendered first)
| key | type | meaning |
|---|---|---|
| `forbid_env_read`, `forbid_git_write`, `forbid_sudo`, `forbid_deploy`, `forbid_unrelated_refactor`, `forbid_disable_checks`, `forbid_unneeded_deps` | bool | intent flags |
| `never` | list[str] | verbatim "Never" lines compiled to the top of every output |

### `workflow.yaml`
| key | type | values |
|---|---|---|
| `planning`, `atomic_tasks`, `self_review`, `architecture_review` | bool | toggle protocol sections |
| `protocol_file` | str | path to the tiered protocol (`AI_DEV_PROTOCOL.md`) |
| `clarify.mode` | str | `by-exception` |
| `clarify.ask_before_large_scan` | bool | — |
| `orient.strategy` | str | `query-first` |
| `orient.levels` / `orient.index` / `orient.snapshot` | str | orientation ladder text |

### `tokens.yaml`
| key | type | values |
|---|---|---|
| `reply_format` | str | `flow-graph` \| `prose` \| `minimal` — selects a skeleton below |
| `max_tokens` | int | mid-task reply cap shown in the block |
| `final_review` | bool | end-of-task goal review (in `reply_format`) is exempt from `max_tokens` |
| `graphs` | bool | — |
| `budget.global_file_max_kb` | int | per-file ceiling enforced by `--doctor` (default 3) |
| `budget.codex_total_max_kib` | int | Codex concatenation cap headroom (32) |
| `reply_skeletons.<format>` | list[str] | the reply template for each format |

### `user.yaml` — the engineer's profile (overlay target)
| key | type | values |
|---|---|---|
| `experience` | str | `senior` \| `junior` |
| `verbosity` | str | `low` \| `medium` (default) \| `high` — behavioral: `medium`/`high` render mid-task progress narration (one-line intent before the first action + a note per phase change); `low` renders the end-of-task report only. Unknown values fail `--check`. |
| `language` | str | `english` (default) \| `match-user`; another language requires an explicit request |
| `max_tokens` | int | — |
| `review_style`, `risk_tolerance` | str | — |
| `languages` | list[str] | e.g. `[Python, TypeScript]` — renders a Profile line only when set |
| `code_style` | str | short phrase — renders in the Profile line |

### Harness engines (compiled into repo-scoped artifacts by `--project`)
| File | Emits | Notes |
|---|---|---|
| `context.yaml` | Harness/Context section + doctor budgets | `initial_file_limit`, `budgets_tokens.*` (advisory) |
| `skills.yaml` | select portable skills for `.claude/skills/` + `.agents/skills/` | `enabled[]`, `lazy_load`; each `skills/<id>/SKILL.md` owns and validates its trigger metadata |
| `commands.yaml` | native Claude/Gemini/Cursor commands; Codex follows `AGENTS.md` + `AI_DEV_PROTOCOL.md` | `commands.<name>.{description,body}` |
| `subagents.yaml` | Claude `.claude/agents/<name>.md` + Codex `.codex/agents/<name>.toml` | read-only exploration/review roles; parent model inherited |
| `hooks.yaml` | `.claude/HOOKS.md` + safe prompt-only Stop hook | `hooks.<id>.{event,action}` — user hooks are preserved and merged idempotently; arbitrary commands are never auto-run |
| `mcp.yaml` | `.ai/MCP.md` + doctor listing | `servers.<id>.{command,args,targets,install}` |
| `memory.yaml` | Memory + shared project navigator convention | `location`, `index`, `resume`, `navigator`, `capture`, `prune`, `load` |
| `profiles/<lang>.yaml` | merged when `user.languages` includes `<lang>` | e.g. sets `user.coding.*` |

### `user.yaml` `coding` (rendered into the Profile line when set)
`function_style` · `typing` · `comments`. Language profiles fill these in automatically.

## Overlay (`config/local.yaml`, gitignored)
Top-level keys mirror the stems above (`user`, `tokens`, `workflow`). Deep-merged over the
defaults. A `safety:` block is accepted but ignored. Validate an overlay with
`bin/compile --validate` (keys + budget). Example:

```yaml
user:
  experience: senior
  languages: [Python, TypeScript]
tokens:
  reply_format: minimal
  max_tokens: 400
```
