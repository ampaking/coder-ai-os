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

"$ROOT/bin/coder-ai-os" sync "$repo" >/dev/null

has 'user-owned root guidance' "$repo/AGENTS.md"
has 'AI_DEV_PROTOCOL.md' "$repo/AGENTS.md"
has 'English unless explicitly requested otherwise.' "$repo/AGENTS.md"
has 'AI_DEV_PROTOCOL.md' "$repo/CLAUDE.md"
has 'AI_DEV_PROTOCOL.md' "$repo/GEMINI.md"
has 'AI_DEV_PROTOCOL.md' "$repo/.cursor/rules/coder-ai-os.mdc"
has 'AI_DEV_PROTOCOL.md' "$repo/.github/copilot-instructions.md"
for f in "$repo/AGENTS.md" "$repo/CLAUDE.md" "$repo/GEMINI.md" \
         "$repo/.cursor/rules/coder-ai-os.mdc" "$repo/.github/copilot-instructions.md"; do
  has 'floor, all tiers: read before edit; verify claims; escalate on contradiction' "$f"
  has 'intent first; phase updates' "$f"
done
has 'user protocol note' "$repo/AI_DEV_PROTOCOL.md"
has 'echo user-script' "$repo/scripts/update-ai-context.sh"
[ -x "$repo/.coder-ai-os-script/update-ai-context.sh" ] || fail 'managed context helper missing from isolated directory'
[ -x "$repo/.coder-ai-os-script/discover-standards.sh" ] || fail 'managed standards helper missing from isolated directory'
has '.coder-ai-os-script/update-ai-context.sh' "$repo/AGENTS.md"
has 'user MCP note' "$repo/.ai/MCP.md"
has 'user standards' "$repo/.ai/standards.md"
has 'Goal: keep this checkpoint exactly' "$repo/.ai/memory/CURRENT.md"
has 'approval_policy = "on-request"' "$repo/.codex/config.toml"
has 'name: architecture' "$repo/.claude/agents/architecture.md"
has 'complete isolated task lifecycle' "$repo/.claude/commands/task.md"
has 'name: debugging' "$repo/.agents/skills/debugging/SKILL.md"
has 'default_prompt:' "$repo/.agents/skills/debugging/agents/openai.yaml"
has 'Resume or run a complex repository task' "$repo/.agents/skills/task-lifecycle/SKILL.md"
if grep -q '^sandbox_mode[[:space:]]*=' "$repo/.codex/agents/explorer.toml"; then
  fail 'Codex explorer pins a sandbox instead of inheriting the host-compatible policy'
fi
has 'developer_instructions' "$repo/.codex/agents/reviewer.toml"
has 'complete isolated task lifecycle' "$repo/.cursor/commands/task.md"
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

before_goal="$(cksum "$repo/.ai/memory/CURRENT.md")"
before_agents="$(grep -c 'user-owned root guidance' "$repo/AGENTS.md")"
before_script="$(cksum "$repo/scripts/update-ai-context.sh")"
before_managed_script="$(cksum "$repo/.coder-ai-os-script/update-ai-context.sh")"
before_mcp="$(cksum "$repo/.ai/MCP.md")"
before_standards="$(cksum "$repo/.ai/standards.md")"
printf '%s\n' 'Human-maintained navigator decision' >> "$repo/.ai/PROJECT_NAVIGATOR.md"
before_navigator="$(cksum "$repo/.ai/PROJECT_NAVIGATOR.md")"
"$ROOT/bin/coder-ai-os" sync "$repo" >/dev/null
[ "$(cksum "$repo/.ai/memory/CURRENT.md")" = "$before_goal" ] || fail 'sync overwrote current task state'
[ "$(grep -c 'user-owned root guidance' "$repo/AGENTS.md")" = "$before_agents" ] || fail 'sync duplicated user guidance'
[ "$(cksum "$repo/scripts/update-ai-context.sh")" = "$before_script" ] || fail 'sync overwrote user script'
[ "$(cksum "$repo/.coder-ai-os-script/update-ai-context.sh")" = "$before_managed_script" ] || fail 'sync produced a non-deterministic managed script'
[ "$(cksum "$repo/.ai/MCP.md")" = "$before_mcp" ] || fail 'sync overwrote user MCP notes'
[ "$(cksum "$repo/.ai/standards.md")" = "$before_standards" ] || fail 'sync overwrote user standards'
[ "$(cksum "$repo/.ai/PROJECT_NAVIGATOR.md")" = "$before_navigator" ] || fail 'sync overwrote maintained navigator'
has 'Human-maintained navigator decision' "$repo/.ai/PROJECT_NAVIGATOR.md"

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
printf 'user notes\n' > "$mono/.ai/symbols/user-notes.tsv"
(cd "$mono" && bash "$ROOT/scripts/update-ai-context.sh" --symbols-all >/dev/null)
[ ! -e "$mono/.ai/symbols/ghost.tsv" ] || fail 'orphaned generated index not removed'
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
  cp -r "$ROOT/bin" "$ROOT/config" "$ROOT/templates" "$ROOT/skills" "$ROOT/shared" \
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
