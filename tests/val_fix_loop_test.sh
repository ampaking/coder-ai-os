#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

VAL_SOURCE_DIR="$ROOT/val"
VAL_PROJECT_DIR="$TMP/project"
VAL_STATE_DIR="$VAL_PROJECT_DIR/.coder-ai/val"
VAL_CONFIG_FILE="$VAL_STATE_DIR/config.json"
VAL_LOCK_DIR="$VAL_STATE_DIR/locks"
VAL_RUN_ID='loop-test'
VAL_LOCK_FILE="$VAL_LOCK_DIR/$VAL_RUN_ID.json"
export VAL_SOURCE_DIR VAL_PROJECT_DIR VAL_STATE_DIR VAL_CONFIG_FILE VAL_LOCK_DIR VAL_RUN_ID VAL_LOCK_FILE
mkdir -p "$VAL_STATE_DIR"

# shellcheck source=val/lib/common.sh
source "$ROOT/val/lib/common.sh"
# shellcheck source=val/lib/fix.sh
source "$ROOT/val/lib/fix.sh"
# shellcheck source=val/lib/report.sh
source "$ROOT/val/lib/report.sh"
# shellcheck source=val/lib/run.sh
source "$ROOT/val/lib/run.sh"

val_require_core() { :; }
val_config_load() { VAL_CONFIG_JSON='{}'; export VAL_CONFIG_JSON; }
val_detect_driver() { VAL_DRIVER='remote'; export VAL_DRIVER; printf '%s\n' remote; }
val_server_start() { VAL_URL='http://fixture.invalid'; VAL_STARTED_BY_US='false'; export VAL_URL VAL_STARTED_BY_US; }
val_server_stop() { :; }
val_detect_browser_mode() { printf '%s\n' local; }
val_auth_prepare() { printf '%s\n' ''; }
val_checklist_update() { printf '%s\n' '[]' > "$1"; }
val_fix_collect_allowed_files() { printf '%s\n' src/ui.css; }
val_run_capture_round() { mkdir -p "$1/shots"; printf '%s\n' '{"shots":[]}' > "$3"; }
val_verify() {
  local output="$3" shot
  shot="$(dirname "$output")/shots/fail.png"
  jq -n --arg shot "$shot" '{protocolVersion:1,findings:[{criterionId:"c-1",checkId:"overflow",status:"fail",severity:"P0",shot:$shot,route:"/",viewport:[375,812],theme:"light",detail:"overflow"}],criteria:[{id:"c-1",status:"fail"}],summary:{pass:0,fail:1,manual:0,blocked:0}}' > "$output"
}
val_judge() {
  jq -n '[{criterionId:"c-1",finding:"overflow",file:"src/ui.css",line:1,expected:"fits",actual:"overflows",suggestedFix:"invalid patch",actionable:true}]' > "$2"
}
val_fix_apply_judgments() {
  val_fix_record_attempt "$3" 'c-1'
  return 1
}

set +e
val_run 'loop-test' > "$TMP/output.log" 2>&1
status=$?
set -e
[ "$status" -eq 2 ] || fail "unfixable loop returned $status instead of 2"
run_dir="$VAL_STATE_DIR/runs/loop-test"
[ -f "$run_dir/BLOCKED.md" ] || fail 'BLOCKED.md was not created'
grep -q 'three fix attempts exhausted' "$run_dir/BLOCKED.md" || fail 'blocked reason missing'
[ "$(jq -r '.["c-1"]' "$run_dir/attempts.json")" -eq 3 ] || fail 'loop did not stop at three attempts'
[ "$(find "$run_dir" -maxdepth 1 -type d -name 'round-*' | awk 'END {print NR}')" -eq 3 ] || fail 'unfixable loop did not stop at round 3'
[ "$(jq -r '.exitCode' "$run_dir/manifest.json")" -eq 2 ] || fail 'blocked manifest exit code is wrong'

printf '%s\n' 'PASS: VAL unfixable finding escalates after three attempts'
