#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

project="$TMP/project with spaces"
mkdir -p "$project/.coder-ai/val"

output="$(cd "$project" && "$ROOT/val/val" doctor)" || fail 'doctor failed with available dependencies'
printf '%s\n' "$output" | grep -q '^bash>=4' || fail 'doctor omitted Bash readiness'
printf '%s\n' "$output" | grep -q '^config.*optional' || fail 'doctor did not report optional config'

"$ROOT/val/val" --help | grep -q -- '--prompt <ui-request>' || fail 'CLI help omitted prompt criteria input'

cp "$ROOT/val/templates/val.config.json" "$project/.coder-ai/val/config.json"
output="$(cd "$project" && "$ROOT/val/val" doctor)" || fail 'doctor rejected the template config'
printf '%s\n' "$output" | grep -q '^config.*ready' || fail 'doctor did not validate config'

printf '%s\n' '{"env":"invalid"}' > "$project/.coder-ai/val/config.json"
set +e
output="$(cd "$project" && "$ROOT/val/val" doctor 2>&1)"
status=$?
set -e
[ "$status" -eq 3 ] || fail "invalid config returned $status instead of 3"
printf '%s\n' "$output" | grep -q '^config.*invalid' || fail 'invalid config was not identified'

fake_bin="$TMP/fake-bin"
mkdir -p "$fake_bin"
for command in bash curl awk ps npx dirname; do
  path="$(command -v "$command")"
  ln -s "$path" "$fake_bin/$command"
done
set +e
output="$(cd "$project" && PATH="$fake_bin" /bin/bash "$ROOT/val/val" doctor 2>&1)"
status=$?
set -e
[ "$status" -eq 3 ] || fail "missing jq returned $status instead of 3"
printf '%s\n' "$output" | grep -q '^jq.*missing' || fail 'doctor did not list missing jq'
printf '%s\n' "$output" | grep -q '^git.*missing' || fail 'doctor did not list missing git'

symlink_project="$TMP/symlink-project"
outside="$TMP/outside"
mkdir -p "$symlink_project/.coder-ai" "$outside"
ln -s "$outside" "$symlink_project/.coder-ai/val"
set +e
output="$(cd "$symlink_project" && "$ROOT/val/val" doctor 2>&1)"
status=$?
set -e
[ "$status" -eq 3 ] || fail "symlinked VAL state returned $status instead of 3"
printf '%s\n' "$output" | grep -q 'refusing symlinked VAL path' || fail 'symlinked VAL state was not rejected'

runtime_project="$TMP/runtime-project"
mkdir -p "$runtime_project/.coder-ai/val/runs" "$TMP/runtime-outside"
ln -s "$TMP/runtime-outside" "$runtime_project/.coder-ai/val/runs/task"
VAL_PROJECT_DIR="$runtime_project" VAL_STATE_DIR="$runtime_project/.coder-ai/val" \
  bash -c 'source "$1/val/lib/common.sh"; ! val_require_safe_output "$VAL_STATE_DIR/runs/task/val.log"' _ "$ROOT" \
  || fail 'task runtime symlink was not rejected'

printf '%s\n' 'PASS: VAL CLI foundation'
