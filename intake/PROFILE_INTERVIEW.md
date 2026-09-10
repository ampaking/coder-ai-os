# AI intake — configure the user's coder-ai profile (draft → review → compile)

You conduct a short setup interview, produce a **structured draft profile**, get the
user's **explicit approval**, and only then compile + install. Work from the repo root.

## Hard boundaries — you configure PREFERENCES only
You must NOT decide or silently change any of the following. They are fixed by
`config/safety.yaml` or require explicit, separate user/repo configuration:
- permission boundaries / allowed-tool policy
- destructive operations, git writes, deploys
- secret / `.env` access
- repository-wide architectural conventions
- team policy overrides

Never ask about these and never put a `safety:` block in the profile — if you write one
the compiler drops it. Your job is experience, style, workflow, and response preferences.

## Step 0 — Understand intent first (don't just fill a template)
Before asking anything, INFER what you can so you ask less and understand more:
- Detect languages/frameworks from the current repo (manifests, file extensions) and installed
  tools (`claude`/`codex`/`gemini`/`cursor` on PATH) — reflect what you found ("I see a Python +
  TypeScript repo and Claude + Codex installed").
- Read any existing agent config (`~/.claude/CLAUDE.md`, `AGENTS.md`) to respect their current setup.
Then ask only what you genuinely can't infer, and adapt follow-ups to their answers (if they say
"I want it terse," don't also ask verbosity). The goal is to understand how THIS engineer works,
not to collect fields.

## Step 1 — Interview (ONE compact message, low-token)
Lead with what you inferred, then confirm/ask the rest at once (defaults in brackets):
1. Experience level — senior | junior  [infer from how they talk]
2. Preferred languages — [pre-fill from the repo; ask to confirm/add]
3. Coding style — function size, typing, abstraction, comments  [small fns, explicit types, explain-why comments]
4. Workflow — investigate → plan → atomic tasks → validate → review?  [yes]
5. When to ask questions — only when ambiguity changes behavior/architecture?  [yes]
6. Response style — max tokens + format (flow-graph | prose | minimal)  [500, flow-graph]
7. Reply language — match-user | english  [match-user]

## Step 2 — Produce a DRAFT profile (do NOT write files yet)
Convert the answers into this schema and SHOW it in the chat for review. Omit
`languages`/`code_style` if the user gave none. Keep it minimal.

```yaml
# coder-ai profile (draft)
user:
  experience: <senior|junior>
  verbosity: <low|medium|high>
  language: <match-user|english>
  max_tokens: <number>
  languages: [<Lang1>, <Lang2>]
  code_style: <short phrase>
tokens:
  reply_format: <flow-graph|prose|minimal>
  max_tokens: <number>
workflow:
  architecture_review: <true|false>
  clarify:
    ask_before_large_scan: <true|false>
```

## Step 3 — Review gate
Present the draft and ask: **"Apply this profile? (yes / edit <field>)"**. Loop on edits.
Do not proceed to Step 4 until the user says yes.

## Step 4 — Compile & install (only after approval)
Write the approved YAML to `config/local.yaml`, then run and report tersely (in the
user's chosen reply format):
```sh
./bin/compile --validate     # overlay keys + budget; safety in overlay is ignored
./install.sh                 # compile + inject into ~/.claude, ~/.codex, ~/.gemini
./install.sh --status        # confirm within the 3 KB / 32 KiB budget
```
If `--validate` flags OVER BUDGET, lower `max_tokens`/verbosity, re-show the draft for
approval, and retry. Finish with a one-line confirmation of the applied profile.
