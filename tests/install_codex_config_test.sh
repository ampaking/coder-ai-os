#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail(){ echo "FAIL: $*" >&2; exit 1; }
sync_repo(){ "$ROOT/bin/coder-ai-os" sync "$1" >/dev/null; }
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

mode_repo="$TMP/mode-repo"
mkdir -p "$mode_repo/.codex"
printf '%s\n' 'model = "private"' > "$mode_repo/.codex/config.toml"
chmod 600 "$mode_repo/.codex/config.toml"
sync_repo "$mode_repo"
mode="$(stat -c '%a' "$mode_repo/.codex/config.toml" 2>/dev/null || stat -f '%Lp' "$mode_repo/.codex/config.toml")"
[ "$mode" = 600 ] || fail "Codex config mode changed to $mode"

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
assert_count 1 '^approval_policy[[:space:]]*=[[:space:]]*"on-failure"$' "$fresh/.codex/config.toml"
assert_count 0 '^sandbox_mode[[:space:]]*=' "$fresh/.codex/config.toml"

grep -q '^# Project navigator — shared human/AI map$' "$fresh/.ai/PROJECT_NAVIGATOR.md" || fail 'fresh sync lacks project navigator'
grep -q '^## Human decisions$' "$fresh/.ai/PROJECT_NAVIGATOR.md" || fail 'project navigator lacks human decision boundary'
grep -q '^## Intent alignment — before changing anything$' "$fresh/.ai/PROJECT_NAVIGATOR.md" || fail 'project navigator lacks intent gate'
grep -q '^## Mistake detection and recovery$' "$fresh/.ai/PROJECT_NAVIGATOR.md" || fail 'project navigator lacks recovery loop'
grep -q '^## Definition of done$' "$fresh/.ai/memory/CURRENT.md" || fail 'checkpoint lacks definition of done'
[ -f "$fresh/.ai/symbols/INDEX.md" ] || fail 'setup did not generate root atlas index'
grep -q '^## Validation evidence (command + observed)$' "$fresh/.ai/memory/CURRENT.md" || fail 'checkpoint lacks validation evidence'
[ -x "$fresh/.coder-ai/val/run" ] || fail 'setup did not install the project-local VAL wrapper'
[ -f "$fresh/.agents/skills/val-fix/SKILL.md" ] || fail 'setup did not install the automatic VAL skill'
home="$TMP/home"
mkdir -p "$home"
HOME="$home" "$ROOT/install.sh" --yes --codex-safety-defaults >/dev/null
assert_count 1 '^approval_policy[[:space:]]*=[[:space:]]*"on-failure"$' "$home/.codex/config.toml"
assert_count 0 '^sandbox_mode[[:space:]]*=' "$home/.codex/config.toml"
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
  printf '%s\n' '{"statusLine":{"type":"command","command":"mine.sh"}}' > "$own_home/.claude/settings.json"
  printf '%s\n' 'my custom script' > "$own_home/.claude/statusline.sh"
  HOME="$own_home" "$ROOT/install.sh" --yes >/dev/null 2>&1
  [ "$(jq -r '.statusLine.command' "$own_home/.claude/settings.json")" = 'mine.sh' ] \
    || fail 'user statusLine was replaced'
  grep -qF 'my custom script' "$own_home/.claude/statusline.sh" || fail 'user statusline.sh was overwritten'

  hook_repo="$TMP/existing-hook-repo"; mkdir -p "$hook_repo/.claude"
  printf '%s\n' '{"hooks":{"Stop":[{"hooks":[{"type":"prompt","prompt":"user stop hook"}]}]}}' \
    > "$hook_repo/.claude/settings.json"
  sync_repo "$hook_repo"
  jq -e '.hooks.Stop | length == 2' "$hook_repo/.claude/settings.json" >/dev/null \
    || fail 'generated Stop hook was not merged with the user Stop hook'
  jq -e '.hooks.PostToolUse | any(.[]; any(.hooks[]; .prompt | contains("watchGlobs")))' \
    "$hook_repo/.claude/settings.json" >/dev/null || fail 'VAL UI-change hook was not installed'
  jq -e '.hooks.Stop | any(.[]; any(.hooks[]; .prompt == "user stop hook"))' \
    "$hook_repo/.claude/settings.json" >/dev/null || fail 'user Stop hook was replaced'
  hook_first="$(cksum "$hook_repo/.claude/settings.json")"
  sync_repo "$hook_repo"
  [ "$(cksum "$hook_repo/.claude/settings.json")" = "$hook_first" ] \
    || fail 'Stop hook merge is not idempotent'
fi

echo "PASS: Codex config generation preserves user data and is idempotent"
