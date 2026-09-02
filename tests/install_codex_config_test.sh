#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail(){ echo "FAIL: $*" >&2; exit 1; }
sync_repo(){ "$ROOT/bin/coder-ai-os" sync "$1" >/dev/null; }
sync_repo_log(){ "$ROOT/bin/coder-ai-os" sync "$1" 2>&1; }
assert_count(){
  local expected="$1" pattern="$2" file="$3" actual
  actual="$(grep -cE "$pattern" "$file" || true)"
  [ "$actual" = "$expected" ] || fail "$file: expected $expected match(es) for $pattern, got $actual"
}

repo="$TMP/existing-repo"
mkdir -p "$repo/.codex"
cat > "$repo/.codex/config.toml" <<'EOF'
model = "user-model"
approval_policy = "on-request"

[features]
multi_agent = true
EOF

sync_repo "$repo"
config="$repo/.codex/config.toml"

grep -q '^model = "user-model"$' "$config" || fail "user model was lost"
grep -q '^approval_policy = "on-request"$' "$config" || fail "user approval policy was replaced"
assert_count 0 '^sandbox_mode[[:space:]]*=' "$config"
assert_count 1 '^default_permissions[[:space:]]*=[[:space:]]*":workspace"$' "$config"
grep -q '^\[features\]$' "$config" || fail "user table was lost"
assert_count 1 '^approval_policy[[:space:]]*=' "$config"
assert_count 0 '^sandbox_mode[[:space:]]*=' "$config"

first="$(cksum "$config")"
sync_repo "$repo"
[ "$(cksum "$config")" = "$first" ] || fail "second sync was not idempotent"

quoted="$TMP/quoted-repo"
mkdir -p "$quoted/.codex"
cat > "$quoted/.codex/config.toml" <<'EOF'
"approval_policy" = "on-request"
'sandbox_mode' = "read-only"
EOF
sync_repo "$quoted"
assert_count 1 '^[[:space:]]*"?approval_policy"?[[:space:]]*=' "$quoted/.codex/config.toml"
assert_count 1 "^[[:space:]]*'?sandbox_mode'?[[:space:]]*=" "$quoted/.codex/config.toml"
assert_count 0 '^default_permissions[[:space:]]*=' "$quoted/.codex/config.toml"

permission_repo="$TMP/permission-repo"
mkdir -p "$permission_repo/.codex"
printf '%s\n' 'default_permissions = ":read-only"' > "$permission_repo/.codex/config.toml"
sync_repo "$permission_repo"
assert_count 1 '^default_permissions[[:space:]]*=[[:space:]]*":read-only"$' "$permission_repo/.codex/config.toml"
assert_count 0 '^default_permissions[[:space:]]*=[[:space:]]*":workspace"$' "$permission_repo/.codex/config.toml"

mode_repo="$TMP/mode-repo"
mkdir -p "$mode_repo/.codex"
printf '%s\n' 'model = "private"' > "$mode_repo/.codex/config.toml"
chmod 600 "$mode_repo/.codex/config.toml"
sync_repo "$mode_repo"
mode="$(stat -c '%a' "$mode_repo/.codex/config.toml" 2>/dev/null || stat -f '%Lp' "$mode_repo/.codex/config.toml")"
[ "$mode" = 600 ] || fail "Codex config mode changed to $mode"

# codex-cli rewrites config.toml and reflows our managed status_line into a multi-line
# array. That is our own content, so a re-sync must not reject the file as ambiguous.
reflowed="$TMP/reflowed-repo"
mkdir -p "$reflowed/.codex"
cat > "$reflowed/.codex/config.toml" <<'EOF'
# >>> coder-ai-os:managed >>>
approval_policy = "on-failure"
# <<< coder-ai-os:managed <<<
# >>> coder-ai-os:managed >>>
[tui]
status_line = [
  "model-with-reasoning",
  "git-branch",
]
# <<< coder-ai-os:managed <<<
EOF
reflowed_log="$(sync_repo_log "$reflowed")"
case "$reflowed_log" in
  *"ambiguous multiline TOML"*) fail 'reflowed managed status_line array tripped the multiline guard' ;;
esac
assert_count 1 '^\[tui\]$' "$reflowed/.codex/config.toml"
assert_count 1 '^status_line[[:space:]]*=' "$reflowed/.codex/config.toml"
assert_count 1 '^approval_policy[[:space:]]*=' "$reflowed/.codex/config.toml"

multiline="$TMP/multiline-repo"
mkdir -p "$multiline/.codex"
printf '%s\n' 'value = [' '  "item",' ']' > "$multiline/.codex/config.toml"
multiline_before="$(cksum "$multiline/.codex/config.toml")"
if sync_repo "$multiline" 2>/dev/null; then fail 'ambiguous multiline TOML was accepted'; fi
[ "$(cksum "$multiline/.codex/config.toml")" = "$multiline_before" ] || fail 'ambiguous TOML was modified'

broken="$TMP/broken-repo"
mkdir -p "$broken/.codex"
printf '%s\n' '# >>> coder-ai-os:managed >>>' 'model = "keep-me"' > "$broken/.codex/config.toml"
broken_before="$(cksum "$broken/.codex/config.toml")"
if sync_repo "$broken" 2>/dev/null; then fail 'unbalanced markers were accepted'; fi
[ "$(cksum "$broken/.codex/config.toml")" = "$broken_before" ] || fail 'unbalanced marker config was modified'

if "$ROOT/install.sh" --codex-only >/dev/null 2>&1; then fail 'retired --codex-only flag was accepted'; fi

# [tui].status_line: added when absent; user status_line or explicit [tui] always wins.
assert_count 1 '^\[tui\]$' "$config"
assert_count 1 '^status_line[[:space:]]*=' "$config"
python3 -c 'import sys,tomllib; tomllib.load(open(sys.argv[1],"rb"))' "$config" || fail 'merged config with [tui] is not valid TOML'

own_sl="$TMP/own-statusline-repo"
mkdir -p "$own_sl/.codex"
printf '%s\n' '[tui]' 'status_line = ["current-dir"]' > "$own_sl/.codex/config.toml"
sync_repo "$own_sl"
assert_count 1 '^\[tui\]$' "$own_sl/.codex/config.toml"
assert_count 1 '^status_line[[:space:]]*=' "$own_sl/.codex/config.toml"
grep -q 'status_line = \["current-dir"\]' "$own_sl/.codex/config.toml" || fail 'user status_line was replaced'
python3 -c 'import sys,tomllib; tomllib.load(open(sys.argv[1],"rb"))' "$own_sl/.codex/config.toml" || fail 'user-statusline merge is not valid TOML'

own_tui="$TMP/own-tui-repo"
mkdir -p "$own_tui/.codex"
printf '%s\n' '[tui]' 'notifications = true' > "$own_tui/.codex/config.toml"
sync_repo "$own_tui"
assert_count 1 '^\[tui\]$' "$own_tui/.codex/config.toml"
assert_count 0 '^status_line[[:space:]]*=' "$own_tui/.codex/config.toml"
python3 -c 'import sys,tomllib; tomllib.load(open(sys.argv[1],"rb"))' "$own_tui/.codex/config.toml" || fail 'user-tui merge is not valid TOML'

fresh="$TMP/fresh-repo"
mkdir -p "$fresh"
"$ROOT/bin/coder-ai-os" setup "$fresh" >/dev/null
assert_count 1 '^approval_policy[[:space:]]*=[[:space:]]*"on-request"$' "$fresh/.codex/config.toml"
assert_count 0 '^sandbox_mode[[:space:]]*=' "$fresh/.codex/config.toml"
assert_count 1 '^default_permissions[[:space:]]*=[[:space:]]*":workspace"$' "$fresh/.codex/config.toml"

grep -q '^# Project navigator — shared human/AI map$' "$fresh/.ai/PROJECT_NAVIGATOR.md" || fail 'fresh sync lacks project navigator'
grep -q '^## Human decisions$' "$fresh/.ai/PROJECT_NAVIGATOR.md" || fail 'project navigator lacks human decision boundary'
grep -q '^## Intent alignment — before changing anything$' "$fresh/.ai/PROJECT_NAVIGATOR.md" || fail 'project navigator lacks intent gate'
grep -q '^## Mistake detection and recovery$' "$fresh/.ai/PROJECT_NAVIGATOR.md" || fail 'project navigator lacks recovery loop'
grep -q '^## Definition of done$' "$fresh/.ai/memory/CURRENT.md" || fail 'checkpoint lacks definition of done'
[ -f "$fresh/.ai/symbols/INDEX.md" ] || fail 'setup did not generate root atlas index'
grep -q '^## Validation evidence (command + observed)$' "$fresh/.ai/memory/CURRENT.md" || fail 'checkpoint lacks validation evidence'
[ -x "$fresh/.coder-ai/val/run" ] || fail 'setup did not install the project-local VAL wrapper'
[ -f "$fresh/.agents/skills/val-fix/SKILL.md" ] || fail 'setup did not install the automatic VAL skill'
[ -f "$fresh/.agents/skills/native-orchestration/SKILL.md" ] || fail 'setup did not install native orchestration for Codex'
[ -f "$fresh/.agents/skills/debugging/SKILL.md" ] || fail 'setup did not install debugging guidance for Codex'
grep -qF 'red-before/green-after' "$fresh/.agents/skills/debugging/SKILL.md" \
  || fail 'installed debugging skill lacks test-sensitivity evidence'
[ -f "$fresh/.claude/skills/native-orchestration/SKILL.md" ] || fail 'setup did not install native orchestration for Claude'
grep -qF 'direct Claude-to-Codex or Codex-to-Claude CLI handoffs' "$fresh/.agents/skills/native-orchestration/SKILL.md" \
  || fail 'native orchestration skill lacks direct provider handoff'
grep -qF 'prefix_rule(pattern=["claude", "-p", "--permission-mode", "plan"], decision="allow")' "$fresh/.codex/rules/coder-ai-os.rules" \
  || fail 'Codex native Claude handoff permission rule is missing'
[ "$(jq -r '.permissions.allow | index("Bash(codex exec --cd * --sandbox read-only:*)")' "$fresh/.claude/settings.json")" != null ] \
  || fail 'Claude native Codex handoff permission is missing'
jq -e '.permissions.deny | index("Bash(codex exec *--dangerously-bypass-approvals-and-sandbox*:*)") != null' \
  "$fresh/.claude/settings.json" >/dev/null || fail 'Claude does not deny unsafe Codex bypass handoff'
[ "$(jq -r '.permissions.defaultMode' "$fresh/.claude/settings.json")" = auto ] || fail 'fresh project did not receive Claude auto mode'
jq -e '.permissions.deny | index("Read(**/.env.*)") != null and index("Bash(git push:*)") != null' \
  "$fresh/.claude/settings.json" >/dev/null || fail 'fresh project lacks Claude secret/git deny rules'

claude_repo="$TMP/claude-repo"
mkdir -p "$claude_repo/.claude"
printf '%s\n' '{"model":"user-model","env":{"USER_SETTING":"keep"},"permissions":{"defaultMode":"plan","allow":["Bash(user-safe:*)"],"ask":["Bash(user-review:*)"],"deny":["Read(user-private/**)"]},"hooks":{"UserPromptSubmit":[{"hooks":[{"type":"command","command":"user-hook"}]}]},"custom":{"nested":{"value":42}}}' > "$claude_repo/.claude/settings.json"
sync_repo "$claude_repo"
[ "$(jq -r '.permissions.defaultMode' "$claude_repo/.claude/settings.json")" = plan ] || fail 'user Claude permission mode was replaced'
jq -e '.permissions.allow | index("Bash(user-safe:*)") != null' "$claude_repo/.claude/settings.json" >/dev/null \
  || fail 'user Claude allow rule was lost'
jq -e '
  .model == "user-model"
  and .env.USER_SETTING == "keep"
  and (.permissions.ask | index("Bash(user-review:*)") != null)
  and (.permissions.deny | index("Read(user-private/**)") != null)
  and .hooks.UserPromptSubmit[0].hooks[0].command == "user-hook"
  and .custom.nested.value == 42
' "$claude_repo/.claude/settings.json" >/dev/null || fail 'sync removed unrelated Claude settings data'
claude_first="$(cksum "$claude_repo/.claude/settings.json")"
sync_repo "$claude_repo"
[ "$(cksum "$claude_repo/.claude/settings.json")" = "$claude_first" ] || fail 'Claude settings merge is not idempotent'
home="$TMP/home"
mkdir -p "$home"
HOME="$home" "$ROOT/install.sh" --yes --codex-safety-defaults >/dev/null
assert_count 1 '^approval_policy[[:space:]]*=[[:space:]]*"on-request"$' "$home/.codex/config.toml"
assert_count 0 '^sandbox_mode[[:space:]]*=' "$home/.codex/config.toml"
assert_count 1 '^default_permissions[[:space:]]*=[[:space:]]*":workspace"$' "$home/.codex/config.toml"
[ -L "$home/.local/bin/coder-ai-os" ] || fail 'global coder-ai-os command link missing'
[ -L "$home/.local/bin/val" ] || fail 'global VAL command link missing'

owned_home="$TMP/owned-cli-home"
mkdir -p "$owned_home/.local/bin"
printf '%s\n' '#!/bin/sh' 'echo user-val' > "$owned_home/.local/bin/val"
chmod 755 "$owned_home/.local/bin/val"
owned_val_before="$(cksum "$owned_home/.local/bin/val")"
HOME="$owned_home" "$ROOT/install.sh" --yes >/dev/null
[ ! -L "$owned_home/.local/bin/val" ] || fail 'user-owned val command was replaced with a symlink'
[ "$(cksum "$owned_home/.local/bin/val")" = "$owned_val_before" ] || fail 'user-owned val command contents changed'

# Claude statusLine: merged when absent + default script installed executable; user's own wins.
if command -v jq >/dev/null 2>&1; then
  sl_home="$TMP/sl-home"; mkdir -p "$sl_home"
  HOME="$sl_home" "$ROOT/install.sh" --yes >/dev/null 2>&1
  [ "$(jq -r '.statusLine.command' "$sl_home/.claude/settings.json")" = '~/.claude/statusline.sh' ] \
    || fail 'statusLine was not merged into fresh settings.json'
  [ -x "$sl_home/.claude/statusline.sh" ] || fail 'default statusline.sh missing or not executable'
  printf '%s' '{"model":{"display_name":"M"},"workspace":{"current_dir":"/a/b"},"context_window":{"used_percentage":41.9}}' \
    | bash "$sl_home/.claude/statusline.sh" | grep -qF '[M] b | ctx 41%' || fail 'statusline.sh output wrong'

  own_home="$TMP/own-sl-home"; mkdir -p "$own_home/.claude"
  printf '%s\n' '{"model":"home-model","env":{"HOME_SETTING":"keep"},"permissions":{"defaultMode":"acceptEdits","ask":["Bash(home-review:*)"]},"statusLine":{"type":"command","command":"mine.sh"},"custom":{"keep":true}}' > "$own_home/.claude/settings.json"
  printf '%s\n' 'my custom script' > "$own_home/.claude/statusline.sh"
  HOME="$own_home" "$ROOT/install.sh" --yes >/dev/null 2>&1
  [ "$(jq -r '.statusLine.command' "$own_home/.claude/settings.json")" = 'mine.sh' ] \
    || fail 'user statusLine was replaced'
  jq -e '.model == "home-model" and .env.HOME_SETTING == "keep" and .permissions.defaultMode == "acceptEdits" and (.permissions.ask | index("Bash(home-review:*)") != null) and .custom.keep == true' \
    "$own_home/.claude/settings.json" >/dev/null || fail 'global install removed existing Claude settings data'
  grep -qF 'my custom script' "$own_home/.claude/statusline.sh" || fail 'user statusline.sh was overwritten'

  hook_repo="$TMP/existing-hook-repo"; mkdir -p "$hook_repo/.claude"
  printf '%s\n' '{"hooks":{"Stop":[{"hooks":[{"type":"prompt","prompt":"user stop hook"}]},{"hooks":[{"type":"prompt","prompt":"remind to run the test suite before reporting done — old generated hook"}]},{"hooks":[{"type":"prompt","prompt":"first determine whether the session executed a repository task that changed code or configuration; keep working and do not approve the stop"}]}],"PostToolUse":[{"matcher":"Edit|Write|MultiEdit|Bash","hooks":[{"type":"command","command":"coder-ai-os tasks hook claude"}]}]}}' \
    > "$hook_repo/.claude/settings.json"
  sync_repo "$hook_repo"
  jq -e '.hooks.Stop | length == 2' "$hook_repo/.claude/settings.json" >/dev/null \
    || fail 'generated Stop hooks were not merged with the user Stop hook'
  jq -e '.hooks.PostToolUse | any(.[]; .matcher == "Edit|Write|MultiEdit" and any(.hooks[]; .type == "command" and .command == ".coder-ai/scripts/val-post-edit.py"))' \
    "$hook_repo/.claude/settings.json" >/dev/null || fail 'deterministic VAL edit marker hook was not installed'
  if jq -e '.hooks.PostToolUse | any(.[]; any(.hooks[]; .type == "prompt"))' \
    "$hook_repo/.claude/settings.json" >/dev/null; then fail 'VAL edit hook still invokes a model'; fi
  jq -e '.hooks.Stop | any(.[]; any(.hooks[]; .prompt == "user stop hook"))' \
    "$hook_repo/.claude/settings.json" >/dev/null || fail 'user Stop hook was replaced'
  jq -e '
    .hooks.Stop | any(.[]; any(.hooks[]; .command == "coder-ai-os tasks hook claude"))
  ' "$hook_repo/.claude/settings.json" >/dev/null || fail 'Project Tasks Stop collector was not installed'
  if jq -e '
    .hooks.Stop | any(.[]; any(.hooks[];
      ((.prompt // "") | contains("first determine whether the session executed a repository task"))
      or ((.prompt // "") | contains("keep working and do not approve the stop"))))
  ' "$hook_repo/.claude/settings.json" >/dev/null; then
    fail 'stale blocking completion prompt survived sync'
  fi
  if jq -e '
    .hooks.PostToolUse // [] | any(.[];
      any(.hooks[]; .command == "coder-ai-os tasks hook claude"))
  ' "$hook_repo/.claude/settings.json" >/dev/null; then
    fail 'Project Tasks collector still runs after every tool use'
  fi
  hook_first="$(cksum "$hook_repo/.claude/settings.json")"
  sync_repo "$hook_repo"
  [ "$(cksum "$hook_repo/.claude/settings.json")" = "$hook_first" ] \
    || fail 'Stop hook merge is not idempotent'
fi

echo "PASS: Codex config generation preserves user data and is idempotent"
