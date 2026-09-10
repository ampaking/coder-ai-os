#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fail(){ echo "FAIL: $*" >&2; exit 1; }
has(){ grep -qF "$1" "$2" || fail "$2 missing: $1"; }

repo="$TMP/existing-repo"
mkdir -p "$repo/.ai/memory" "$repo/.codex" "$repo/scripts" "$repo/.ai"
printf '%s\n' 'user-owned root guidance' > "$repo/AGENTS.md"
printf '%s\n' 'Goal: keep this checkpoint exactly' > "$repo/.ai/memory/CURRENT.md"
printf '%s\n' 'user protocol note' > "$repo/AI_DEV_PROTOCOL.md"
printf '%s\n' '#!/usr/bin/env bash' 'echo user-script' > "$repo/scripts/update-ai-context.sh"
printf '%s\n' 'user MCP note' > "$repo/.ai/MCP.md"
printf '%s\n' 'user standards' > "$repo/.ai/standards.md"
cat > "$repo/.codex/config.toml" <<'TOML'
approval_policy = "on-request"
[features]
multi_agent = true
TOML
mkdir -p "$repo/.claude"
printf '%s\n' '{"permissions":{"allow":["Bash(make test:*)"]}}' > "$repo/.claude/settings.local.json"
before_claude_local="$(cksum "$repo/.claude/settings.local.json")"

"$ROOT/bin/coder-ai-os" sync "$repo" >/dev/null

python3 - "$repo/.coder-ai/tasks/settings.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    settings = json.load(handle)
assert settings["enabled"] is True
assert settings["automaticCollection"] is True
PY

python3 "$ROOT/src/coderai/project_tasks/cli.py" --project "$repo" disable >/dev/null
"$ROOT/bin/coder-ai-os" sync "$repo" >/dev/null
python3 - "$repo/.coder-ai/tasks/settings.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    settings = json.load(handle)
assert settings["enabled"] is True
assert settings["automaticCollection"] is True
PY

has 'user-owned root guidance' "$repo/AGENTS.md"
if grep -q 'coder-ai-os:managed' "$repo/AGENTS.md"; then fail 'sync modified project-owned AGENTS.md'; fi
[ ! -e "$repo/CLAUDE.md" ] || fail 'sync created a project-owned CLAUDE.md'
[ ! -e "$repo/GEMINI.md" ] || fail 'sync created a project-owned GEMINI.md'
has 'AI_DEV_PROTOCOL.md' "$repo/.coder-ai/local/INSTRUCTIONS.md"
has 'load only the matching skill' "$repo/.coder-ai/local/INSTRUCTIONS.md"
has 'AI_DEV_PROTOCOL.md' "$repo/.cursor/rules/coder-ai-os.mdc"
has 'AI_DEV_PROTOCOL.md' "$repo/.github/copilot-instructions.md"
for f in "$repo/.cursor/rules/coder-ai-os.mdc" "$repo/.github/copilot-instructions.md"; do
  has 'Load ladder' "$f"
  has 'Floor (every tier)' "$f"
  has 'PROTOCOL_PHASES.md' "$f"
  has 'intent first; phase updates' "$f"
done
[ -f "$repo/.coder-ai/PROTOCOL_PHASES.md" ] || fail 'feature/epic phases companion missing from .coder-ai/'
has 'user protocol note' "$repo/AI_DEV_PROTOCOL.md"
has 'echo user-script' "$repo/scripts/update-ai-context.sh"
[ -x "$repo/.coder-ai/scripts/update-ai-context.sh" ] || fail 'managed context helper missing from isolated directory'
[ -x "$repo/.coder-ai/scripts/discover-standards.sh" ] || fail 'managed standards helper missing from isolated directory'
[ -x "$repo/.coder-ai/scripts/val-post-edit.py" ] || fail 'deterministic VAL edit marker missing'
[ -x "$repo/.coder-ai/scripts/compact-current.py" ] || fail 'CURRENT compactor missing'
has '.coder-ai/scripts/update-ai-context.sh' "$repo/.coder-ai/local/INSTRUCTIONS.md"
has 'user MCP note' "$repo/.ai/MCP.md"
has 'user standards' "$repo/.ai/standards.md"
has 'Goal: keep this checkpoint exactly' "$repo/.ai/memory/CURRENT.md"
has 'approval_policy = "on-request"' "$repo/.codex/config.toml"
has 'name: architecture' "$repo/.claude/agents/architecture.md"
has 'complete isolated task lifecycle' "$repo/.claude/commands/task.md"
has 'name: debugging' "$repo/.agents/skills/debugging/SKILL.md"
has 'Prove the regression test is sensitive' "$repo/.agents/skills/debugging/SKILL.md"
has 'Tests are bug detectors, not implementation confirmation' "$repo/.claude/skills/feature-development/SKILL.md"
has 'default_prompt:' "$repo/.agents/skills/debugging/agents/openai.yaml"
has 'Resume or run a complex repository task' "$repo/.agents/skills/task-lifecycle/SKILL.md"
has 'name: native-orchestration' "$repo/.agents/skills/native-orchestration/SKILL.md"
has 'never require `coder-ai run`, a daemon, or an MCP bridge' "$repo/.agents/skills/native-orchestration/SKILL.md"
has 'codex exec --cd <project> --sandbox workspace-write' "$repo/.agents/skills/native-orchestration/SKILL.md"
has 'claude -p --permission-mode auto --output-format json' "$repo/.claude/skills/native-orchestration/SKILL.md"
has 'retry the same handoff once' "$repo/.agents/skills/native-orchestration/SKILL.md"
has 'user opted out of it' "$repo/.claude/skills/native-orchestration/SKILL.md"
has 'cannot replace its own parent process' "$repo/.agents/skills/native-orchestration/SKILL.md"
has 'never guess model names' "$repo/.claude/skills/native-orchestration/SKILL.md"
has '`--fallback-model <configured-model>`' "$repo/.claude/skills/native-orchestration/SKILL.md"
has 'explicit delegated-model preference' "$repo/.agents/skills/native-orchestration/SKILL.md"
has 'name: project-tasks' "$repo/.agents/skills/project-tasks/SKILL.md"
has 'UI is strictly on demand' "$repo/.agents/skills/project-tasks/SKILL.md"
has 'name: project-tasks' "$repo/.claude/skills/project-tasks/SKILL.md"
has 'name: release-handoff' "$repo/.agents/skills/release-handoff/SKILL.md"
has 'normally 2–3 changed source/test files' "$repo/.claude/skills/release-handoff/SKILL.md"
has '.ai/knowledge/release-workflow.md' "$repo/.agents/skills/release-handoff/SKILL.md"
has 'Never mention AI, Claude, Codex' "$repo/.claude/skills/release-handoff/SKILL.md"
has 'Never run `git add`' "$repo/.agents/skills/release-handoff/SKILL.md"
has 'tasks/' "$repo/.coder-ai/.gitignore"
has 'runs/' "$repo/.coder-ai/.gitignore"
[ "$(cksum "$repo/.claude/settings.local.json")" = "$before_claude_local" ] || fail 'sync overwrote native Claude standing approvals'
if grep -q '^sandbox_mode[[:space:]]*=' "$repo/.codex/agents/explorer.toml"; then
  fail 'Codex explorer pins a sandbox instead of inheriting the host-compatible policy'
fi
has 'developer_instructions' "$repo/.codex/agents/reviewer.toml"
has 'whether tests could actually catch the defect' "$repo/.codex/agents/reviewer.toml"
has 'prefix_rule(pattern=["claude", "-p", "--permission-mode", "plan"], decision="allow")' "$repo/.codex/rules/coder-ai-os.rules"
has 'complete isolated task lifecycle' "$repo/.cursor/commands/task.md"
has 'Never require coder-ai run or Project Tasks commands' "$repo/.cursor/commands/task.md"
has '# Project navigator — shared human/AI map' "$repo/.ai/PROJECT_NAVIGATOR.md"
has '## Capability boundary' "$repo/.ai/PROJECT_NAVIGATOR.md"
has '## Task graph' "$repo/.ai/PROJECT_NAVIGATOR.md"
has '## Intent alignment — before changing anything' "$repo/.ai/PROJECT_NAVIGATOR.md"
has '## Mistake detection and recovery' "$repo/.ai/PROJECT_NAVIGATOR.md"
(cd "$repo" && HOME="$TMP/home" "$ROOT/bin/coder-ai-os" doctor >/dev/null)
broken="$TMP/broken-context"; mkdir -p "$broken"
"$ROOT/bin/coder-ai-os" setup "$broken" >/dev/null
rm -f "$broken/.ai/standards.md"
if (cd "$broken" && HOME="$TMP/home" "$ROOT/bin/coder-ai-os" doctor >/dev/null 2>&1); then
  fail 'doctor passed with a required project context file missing'
fi
has 'complete isolated task lifecycle' "$repo/.gemini/commands/task.toml"
has 'coder-ai-os:managed' "$repo/.github/copilot-instructions.md"

init_repo="$TMP/init-repo"; mkdir -p "$init_repo"; git -C "$init_repo" init -q
printf '%s\n' '# user local ignore' > "$init_repo/.git/info/exclude"
(cd "$init_repo" && "$ROOT/bin/coder-ai-os" init --ai true >/dev/null)
python3 - "$init_repo/.coder-ai/tasks/settings.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    settings = json.load(handle)
assert settings["enabled"] is True
assert settings["automaticCollection"] is True
PY
exclude="$init_repo/.git/info/exclude"
has '# user local ignore' "$exclude"
has '# >>> coder-ai-os:local >>>' "$exclude"
has '/.coder-ai/' "$exclude"
has '/.agents/skills/' "$exclude"
has '/.claude/settings.local.json' "$exclude"
has '/.ai/commit.md' "$exclude"
has '/.ai/knowledge/release-workflow.md' "$exclude"
if grep -qFx '/AGENTS.md' "$exclude"; then fail 'local excludes hide project-owned AGENTS.md'; fi
[ "$(grep -cFx '# >>> coder-ai-os:local >>>' "$exclude")" = 1 ] || fail 'local exclude block duplicated'
(cd "$init_repo" && "$ROOT/bin/coder-ai-os" sync . >/dev/null)
[ "$(grep -cFx '# >>> coder-ai-os:local >>>' "$exclude")" = 1 ] || fail 'second sync duplicated local exclude block'
if git -C "$init_repo" status --short | grep -Eq '^\?\? ((\.coder-ai|\.ai|\.claude|\.agents|\.codex|\.cursor|\.gemini|\.github)/|AGENTS\.md|CLAUDE\.md|GEMINI\.md|AI_DEV_PROTOCOL\.md)'; then
  fail 'fresh setup exposes untracked coder-ai-os artifacts despite local excludes'
fi

before_goal="$(cksum "$repo/.ai/memory/CURRENT.md")"
before_agents="$(cksum "$repo/AGENTS.md")"
before_script="$(cksum "$repo/scripts/update-ai-context.sh")"
before_managed_script="$(cksum "$repo/.coder-ai/scripts/update-ai-context.sh")"
before_mcp="$(cksum "$repo/.ai/MCP.md")"
before_standards="$(cksum "$repo/.ai/standards.md")"
printf '%s\n' 'Human-maintained navigator decision' >> "$repo/.ai/PROJECT_NAVIGATOR.md"
before_navigator="$(cksum "$repo/.ai/PROJECT_NAVIGATOR.md")"
printf '%s\n' 'Cursor rule (repo-scoped). Generated by coder-ai-os — do not hand-edit; edit config/*.yaml and recompile.' 'stale generated rule' > "$repo/.cursor/rules/coder-ai-os.mdc"
"$ROOT/bin/coder-ai-os" sync "$repo" >/dev/null
[ "$(cksum "$repo/.ai/memory/CURRENT.md")" = "$before_goal" ] || fail 'sync overwrote current task state'
[ "$(cksum "$repo/AGENTS.md")" = "$before_agents" ] || fail 'sync changed project-owned guidance'
[ "$(cksum "$repo/scripts/update-ai-context.sh")" = "$before_script" ] || fail 'sync overwrote user script'
[ "$(cksum "$repo/.coder-ai/scripts/update-ai-context.sh")" = "$before_managed_script" ] || fail 'sync produced a non-deterministic managed script'
[ "$(cksum "$repo/.ai/MCP.md")" = "$before_mcp" ] || fail 'sync overwrote user MCP notes'
[ "$(cksum "$repo/.ai/standards.md")" = "$before_standards" ] || fail 'sync overwrote user standards'
[ "$(cksum "$repo/.ai/PROJECT_NAVIGATOR.md")" = "$before_navigator" ] || fail 'sync overwrote maintained navigator'
has 'Human-maintained navigator decision' "$repo/.ai/PROJECT_NAVIGATOR.md"
has '.coder-ai/scripts/update-ai-context.sh' "$repo/.cursor/rules/coder-ai-os.mdc"
has 'load only the matching skill' "$repo/.cursor/rules/coder-ai-os.mdc"

legacy_scripts="$TMP/legacy-scripts"; mkdir -p "$legacy_scripts/.coder-ai-os-script/val-fixtures"
cp "$ROOT/scripts/update-ai-context.sh" "$legacy_scripts/.coder-ai-os-script/update-ai-context.sh"
printf '%s\n' 'user fixture' > "$legacy_scripts/.coder-ai-os-script/val-fixtures/custom.mjs"
"$ROOT/bin/coder-ai-os" sync "$legacy_scripts" >/dev/null
[ -x "$legacy_scripts/.coder-ai/scripts/update-ai-context.sh" ] || fail 'legacy helper was not migrated'
has 'user fixture' "$legacy_scripts/.coder-ai/scripts/val-fixtures/custom.mjs"
[ ! -e "$legacy_scripts/.coder-ai-os-script" ] || fail 'empty legacy helper directory remains'

legacy="$TMP/legacy-repo"; mkdir -p "$legacy"; cp "$ROOT/protocol/AI_DEV_PROTOCOL.md" "$legacy/AI_DEV_PROTOCOL.md"
"$ROOT/bin/coder-ai-os" sync "$legacy" >/dev/null
[ "$(grep -c '^# AI_DEV_PROTOCOL.md$' "$legacy/AI_DEV_PROTOCOL.md")" = 1 ] || fail 'legacy protocol was duplicated'
[ -f "$legacy/.ai/legacy/AI_DEV_PROTOCOL.pre-managed.md" ] || fail 'legacy protocol backup missing'
has '### 1a. Understand-first floor' "$legacy/AI_DEV_PROTOCOL.md"

custom_legacy="$TMP/custom-legacy-repo"; mkdir -p "$custom_legacy"
cp "$ROOT/protocol/AI_DEV_PROTOCOL.md" "$custom_legacy/AI_DEV_PROTOCOL.md"
printf '%s\n' 'user-customized protocol rule' >> "$custom_legacy/AI_DEV_PROTOCOL.md"
"$ROOT/bin/coder-ai-os" sync "$custom_legacy" >/dev/null
has 'user-customized protocol rule' "$custom_legacy/AI_DEV_PROTOCOL.md"
[ ! -e "$custom_legacy/.ai/legacy/AI_DEV_PROTOCOL.pre-managed.md" ] || fail 'custom protocol was misclassified as generated'

managed_docs="$TMP/managed-docs"; mkdir -p "$managed_docs/packages/api"
cat > "$managed_docs/AGENTS.md" <<'EOF'
project-owned root rule
<!-- >>> coder-ai-os:managed >>> -->
stale generated rule
<!-- <<< coder-ai-os:managed <<< -->
project-owned trailing rule
EOF
cat > "$managed_docs/packages/api/AGENTS.md" <<'EOF'
package-owned rule
<!-- >>> coder-ai-os:managed >>> -->
stale package rule
<!-- <<< coder-ai-os:managed <<< -->
EOF
"$ROOT/bin/coder-ai-os" sync "$managed_docs" >/dev/null
has 'project-owned root rule' "$managed_docs/AGENTS.md"
has 'project-owned trailing rule' "$managed_docs/AGENTS.md"
has 'package-owned rule' "$managed_docs/packages/api/AGENTS.md"
if grep -Rq 'stale generated rule\|stale package rule\|coder-ai-os:managed' "$managed_docs/AGENTS.md" "$managed_docs/packages/api/AGENTS.md"; then
  fail 'legacy managed instruction blocks were not removed cleanly'
fi
has 'packages/api/AGENTS.md' "$managed_docs/.coder-ai/local/MONOREPO.md"
has 'Repository-owned `AGENTS.md`' "$managed_docs/.coder-ai/local/INSTRUCTIONS.md"

hook_repo="$TMP/hook-repo"; mkdir -p "$hook_repo/.git/hooks"
cat > "$hook_repo/.git/hooks/post-merge" <<'HOOK'
#!/usr/bin/env bash
echo user-before
# >>> coder-ai-os:snapshot >>>
scripts/update-ai-context.sh
# <<< coder-ai-os:snapshot <<<
echo user-after
HOOK
chmod 751 "$hook_repo/.git/hooks/post-merge"
"$ROOT/bin/coder-ai-os" sync "$hook_repo" >/dev/null
has 'echo user-before' "$hook_repo/.git/hooks/post-merge"
has 'echo user-after' "$hook_repo/.git/hooks/post-merge"
if grep -qF 'coder-ai-os:snapshot' "$hook_repo/.git/hooks/post-merge"; then
  fail 'retired managed hook block remains'
fi
[ "$(stat -c '%a' "$hook_repo/.git/hooks/post-merge" 2>/dev/null || stat -f '%Lp' "$hook_repo/.git/hooks/post-merge")" = 751 ] || fail 'hook mode changed during cleanup'

linked_hooks="$TMP/common-git/hooks"; mkdir -p "$linked_hooks" "$TMP/worktree" "$TMP/fake-bin"
real_git="$(command -v git)"
cp "$hook_repo/.git/hooks/post-merge" "$linked_hooks/post-merge"
# Restore a managed block so this fixture verifies Git-resolved worktree hook paths.
awk '$0 == "echo user-after" { print "# >>> coder-ai-os:snapshot >>>"; print "scripts/update-ai-context.sh"; print "# <<< coder-ai-os:snapshot <<<" } { print }' \
  "$linked_hooks/post-merge" > "$linked_hooks/post-merge.tmp"
mv "$linked_hooks/post-merge.tmp" "$linked_hooks/post-merge"
cat > "$TMP/fake-bin/git" <<'FAKEGIT'
#!/usr/bin/env bash
if [ "${3:-}" = rev-parse ] && [ "${4:-}" = --git-path ] && [ "${5:-}" = hooks ]; then
  printf '%s\n' "$FAKE_HOOKS"
  exit 0
fi
exec "$REAL_GIT" "$@"
FAKEGIT
chmod +x "$TMP/fake-bin/git"
PATH="$TMP/fake-bin:$PATH" FAKE_HOOKS="$linked_hooks" REAL_GIT="$real_git" "$ROOT/bin/coder-ai-os" sync "$TMP/worktree" >/dev/null
if grep -qF 'coder-ai-os:snapshot' "$linked_hooks/post-merge"; then fail 'worktree managed hook block remains'; fi
has 'echo user-before' "$linked_hooks/post-merge"
has 'echo user-after' "$linked_hooks/post-merge"

outside="$TMP/outside.md"; printf '%s\n' 'outside-safe' > "$outside"
linked="$TMP/linked-repo"; mkdir -p "$linked"; ln -s "$outside" "$linked/AGENTS.md"
if "$ROOT/bin/coder-ai-os" sync "$linked" >/dev/null 2>&1; then fail 'sync accepted symlink target'; fi
has 'outside-safe' "$outside"

legacy_mcp="$TMP/legacy-mcp-repo"; mkdir -p "$legacy_mcp/.ai"
tail -n +2 "$ROOT/build/project/MCP.md" > "$legacy_mcp/.ai/MCP.md"
"$ROOT/bin/coder-ai-os" sync "$legacy_mcp" >/dev/null
has 'coder-ai-os:generated' "$legacy_mcp/.ai/MCP.md"

parent_out="$TMP/parent-out"; mkdir -p "$parent_out"
parent_repo="$TMP/parent-repo"; mkdir -p "$parent_repo"; ln -s "$parent_out" "$parent_repo/.codex"
if "$ROOT/bin/coder-ai-os" sync "$parent_repo" >/dev/null 2>&1; then fail 'sync accepted symlink parent'; fi
[ -z "$(find "$parent_out" -mindepth 1 -print -quit)" ] || fail 'sync wrote through symlink parent'

invalid_nav="$TMP/invalid-nav"; mkdir -p "$invalid_nav/.ai/PROJECT_NAVIGATOR.md"
if "$ROOT/bin/coder-ai-os" sync "$invalid_nav" >/dev/null 2>&1; then
  fail 'sync accepted a non-file project navigator target'
fi

# Workspace-aware snapshot: monorepo map + internal deps + fingerprint staleness detection.
mono="$TMP/mono-repo"
mkdir -p "$mono/packages/shared" "$mono/packages/api" "$mono/crates/core"
printf '%s\n' '{ "name": "@t/shared", "version": "1.0.0" }' > "$mono/packages/shared/package.json"
printf '%s\n' '{ "name": "@t/api", "dependencies": { "@t/shared": "workspace:*" } }' > "$mono/packages/api/package.json"
printf '%s\n' '[package]' 'name = "t-core"' > "$mono/crates/core/Cargo.toml"
(cd "$mono" && bash "$ROOT/scripts/update-ai-context.sh" >/dev/null)
snap="$mono/.ai/PROJECT_SNAPSHOT.md"
has 'packages/api  [package.json]  @t/api  deps-> @t/shared' "$snap"
has 'crates/core  [Cargo.toml]  t-core' "$snap"
has '> Fingerprint: ' "$snap"
# Module flow: layered top-level import graph (entry imports others; base is imported).
mkdir -p "$mono/api" "$mono/db"
printf 'from db import models\n' > "$mono/api/server.py"
printf 'import sqlite3\n' > "$mono/db/models.py"
(cd "$mono" && bash "$ROOT/scripts/update-ai-context.sh" >/dev/null)
grep -E '^\[entry\] .*api' "$snap" >/dev/null || fail 'module flow missing [entry] api layer'
grep -E '^\[base\] .*db' "$snap" >/dev/null || fail 'module flow missing [base] db layer'
has '1 api->db' "$snap"

(cd "$mono" && bash "$ROOT/scripts/update-ai-context.sh" --check >/dev/null) || fail 'fresh snapshot reported stale'
touch "$mono/packages/api/new-file.ts"
if (cd "$mono" && bash "$ROOT/scripts/update-ai-context.sh" --check >/dev/null 2>&1); then
  fail 'stale snapshot passed --check after a file was added'
fi

# L1 symbol index: manifest unit + bare-dir unit + root; queryable by name and by file.
printf '%s\n' 'export function handleLogin(u) { return u }' 'export class SessionStore {}' \
  > "$mono/packages/api/auth.ts"
printf 'def get_user(uid):\n    pass\n' > "$mono/api/users.py"
(cd "$mono" && bash "$ROOT/scripts/update-ai-context.sh" --symbols packages/api >/dev/null)
(cd "$mono" && bash "$ROOT/scripts/update-ai-context.sh" --symbols api >/dev/null)
(cd "$mono" && bash "$ROOT/scripts/update-ai-context.sh" --symbols . >/dev/null)
sym="$mono/.ai/symbols/packages/api.md"
[ -f "$sym" ] || fail 'manifest-unit symbol index missing (name from package.json)'
grep -Eq '^\| handleLogin \| function \| 1 \|$' "$sym" || fail 'function definition not indexed with line'
grep -Eq '^\| SessionStore \| class \| 2 \|$' "$sym" || fail 'by-file/class row missing'
grep -q 'get_user' "$mono/.ai/symbols/api.md" || fail 'bare-dir (no manifest) unit not indexed'
[ -f "$mono/.ai/symbols/INDEX.md" ] || fail 'root atlas index missing'
has 'map: .ai/symbols/packages/api.md' "$snap"
grep -q 'api  (no manifest)  map: .ai/symbols/api.md' "$snap" || fail 'bare unit not listed in snapshot'
has '.  (repo root)  map: .ai/symbols/INDEX.md' "$snap"
(cd "$mono" && bash "$ROOT/scripts/update-ai-context.sh" --symbols packages/api --check >/dev/null) \
  || fail 'fresh symbol index reported stale'
printf '%s\n' 'export function extra() {}' >> "$mono/packages/api/auth.ts"
if (cd "$mono" && bash "$ROOT/scripts/update-ai-context.sh" --symbols packages/api --check >/dev/null 2>&1); then
  fail 'stale symbol index passed --check after content change'
fi

# D5 task-boundary diff: catches added/removed/moved symbols, incl. out-of-declared-scope.
(cd "$mono" && bash "$ROOT/scripts/update-ai-context.sh" --symbols packages/api --baseline >/dev/null)
awk 'BEGIN { print "export function otpCheck() {}" } { print }' "$mono/packages/api/auth.ts" > "$mono/packages/api/auth.ts.tmp"
mv "$mono/packages/api/auth.ts.tmp" "$mono/packages/api/auth.ts"
printf 'def sneaky():\n    pass\n' >> "$mono/api/users.py"   # unrelated edit in ANOTHER unit
diff_out="$( (cd "$mono" && bash "$ROOT/scripts/update-ai-context.sh" --symbols packages/api --diff) || true)"
printf '%s\n' "$diff_out" | grep -q '^+ function otpCheck' || fail 'symbol diff missed added function'
printf '%s\n' "$diff_out" | grep -q '^~ function handleLogin' || fail 'symbol diff missed moved function'
if (cd "$mono" && bash "$ROOT/scripts/update-ai-context.sh" --symbols api --check >/dev/null 2>&1); then
  fail 'unrelated edit in api/ unit not detectable (stale check should flag it)'
fi

# --symbols-all indexes every unit the snapshot lists (empty units legal, header-only).
(cd "$mono" && bash "$ROOT/scripts/update-ai-context.sh" --symbols-all >/dev/null) \
  || fail 'symbols-all failed'
for u in packages/api.md packages/shared.md crates/core.md api.md INDEX.md; do
  [ -f "$mono/.ai/symbols/$u" ] || fail "symbols-all missed unit $u"
done

# --symbols-all GC: orphaned generated indexes removed; user-authored files kept.
printf '%s\n' '# unit: ghost  dir: gone/away' '# parent: .ai/PROJECT_SNAPSHOT.md  regenerate: x' \
  > "$mono/.ai/symbols/ghost.tsv"
mkdir -p "$mono/.ai/symbols/gone"
printf '%s\n' '# task-scoped baseline — Generated by .coder-ai/scripts/update-ai-context.sh' \
  'gone/away/file.py	function	ghost	1' > "$mono/.ai/symbols/gone/away.baseline"
printf 'user notes\n' > "$mono/.ai/symbols/user-notes.tsv"
(cd "$mono" && bash "$ROOT/scripts/update-ai-context.sh" --symbols-all >/dev/null)
[ ! -e "$mono/.ai/symbols/ghost.tsv" ] || fail 'orphaned generated index not removed'
[ ! -e "$mono/.ai/symbols/gone/away.baseline" ] || fail 'renamed-unit baseline not removed'
[ -f "$mono/.ai/symbols/packages/api.baseline" ] || fail 'active-unit baseline was removed'
[ -f "$mono/.ai/symbols/user-notes.tsv" ] || fail 'user-authored symbols file was removed'

# --changed: symbol-level diff vs git HEAD (added function + new file detected).
cg="$TMP/changed-repo"; mkdir -p "$cg/app"
( cd "$cg" && git init -q \
  && printf 'def alpha():\n    pass\n' > app/core.py \
  && git add -A && git -c user.name=t -c user.email=t@t commit -qm init \
  && printf '\ndef beta():\n    pass\n' >> app/core.py \
  && printf 'def gamma():\n    pass\n' > app/extra.py )
cout="$( (cd "$cg" && bash "$ROOT/scripts/update-ai-context.sh" --changed app) || true )"
printf '%s\n' "$cout" | grep -q '^+ function beta app/core.py:4' || fail '--changed missed added function'
printf '%s\n' "$cout" | grep -q '^+ function gamma app/extra.py:1' || fail '--changed missed new file symbol'

# Memory scope: the snapshot tells the AI whether .ai/ is shared, local-ignored, or undecided.
(cd "$cg" && bash "$ROOT/scripts/update-ai-context.sh" >/dev/null)
grep -q '^> Memory scope: LOCAL-ONLY so far (.ai untracked)' "$cg/.ai/PROJECT_SNAPSHOT.md" \
  || fail 'untracked memory scope not reported'
printf '%s\n' '.ai/' >> "$cg/.git/info/exclude"
(cd "$cg" && bash "$ROOT/scripts/update-ai-context.sh" >/dev/null)
grep -q '^> Memory scope: LOCAL-ONLY (.ai is git-ignored)' "$cg/.ai/PROJECT_SNAPSHOT.md" \
  || fail 'locally-excluded memory scope not detected (.git/info/exclude)'
grep -q '^> Memory scope: local-only (no git repo)' "$snap" || fail 'no-git memory scope not reported'

# Nested project (E1): source root detected below wrapper dirs; units get full paths;
# flow tree (E2) renders indented imports with cycle marks.
nest="$TMP/nested-repo"
mkdir -p "$nest/src/sat/backend/alpha" "$nest/src/sat/backend/beta"
printf 'from beta import x\n\ndef run_pipeline():\n    pass\n' > "$nest/src/sat/backend/alpha/main.py"
printf 'from alpha import q\n' > "$nest/src/sat/backend/beta/base.py"
(cd "$nest" && bash "$ROOT/scripts/update-ai-context.sh" >/dev/null)
nsnap="$nest/.ai/PROJECT_SNAPSHOT.md"
has 'source root: src/sat/backend' "$nsnap"
has 'src/sat/backend/alpha  (no manifest)  map: .ai/symbols/src/sat/backend/alpha.md' "$nsnap"
has 'src/sat/backend  (source root)  map: .ai/symbols/src/sat/backend.md' "$nsnap"
grep -qF -- '--- flow tree (imports point down; cycles marked) ---' "$nsnap" || fail 'flow tree section missing'
grep -qF -- '`-> beta' "$nsnap" || fail 'tree does not show indented child edge'
grep -qF -- '<- cycle' "$nsnap" || fail 'cycle not marked in flow tree'
(cd "$nest" && bash "$ROOT/scripts/update-ai-context.sh" --symbols src/sat/backend/alpha >/dev/null)
grep -Eq '^\| run_pipeline \| function \| 3 \|$' "$nest/.ai/symbols/src/sat/backend/alpha.md" \
  || fail 'nested unit symbols not generated'

# Verbosity is behavioral: low omits the narration clause; an unknown value fails --check.
if command -v python3 >/dev/null 2>&1; then
  vb="$TMP/verbosity-sandbox"; mkdir -p "$vb"
  cp -r "$ROOT/bin" "$ROOT/config" "$ROOT/templates" "$ROOT/skills" \
        "$ROOT/protocol" "$ROOT/claude" "$ROOT/codex" "$vb/" 2>/dev/null
  rm -f "$vb/config/local.yaml"
  sed -i.bak 's/^verbosity: medium$/verbosity: low/' "$vb/config/user.yaml" && rm -f "$vb/config/user.yaml.bak"
  (cd "$vb" && python3 bin/compile >/dev/null)
  (cd "$vb" && python3 bin/compile --check >/dev/null) || fail 'clean full generated tree reported drift'
  if grep -q 'phase updates' "$vb/build/CLAUDE.md"; then fail 'verbosity low still renders narration'; fi
  printf '%s\n' 'stale generated artifact' > "$vb/build/project/stale.md"
  if (cd "$vb" && python3 bin/compile --check >/dev/null 2>&1); then
    fail 'stale nested generated artifact passed --check'
  fi
  rm -f "$vb/build/project/stale.md"
  ln -s /path-that-must-not-be-read "$vb/build/project/stale-link"
  if (cd "$vb" && python3 bin/compile --check >/dev/null 2>&1); then
    fail 'generated-tree symlink passed --check'
  fi
  rm -f "$vb/build/project/stale-link"
  sed -i.bak 's/^verbosity: low$/verbosity: banana/' "$vb/config/user.yaml" && rm -f "$vb/config/user.yaml.bak"
  if (cd "$vb" && python3 bin/compile --check >/dev/null 2>&1); then fail 'invalid verbosity passed --check'; fi
fi

echo 'PASS: one-step sync installs every agent lifecycle and preserves existing state'
