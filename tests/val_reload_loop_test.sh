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
VAL_SERVER_LOG="$VAL_STATE_DIR/server.log"
export VAL_SOURCE_DIR VAL_PROJECT_DIR VAL_STATE_DIR VAL_CONFIG_FILE VAL_LOCK_DIR VAL_SERVER_LOG
mkdir -p "$VAL_STATE_DIR"

# shellcheck source=val/lib/common.sh
source "$ROOT/val/lib/common.sh"
# shellcheck source=val/lib/fix.sh
source "$ROOT/val/lib/fix.sh"
# shellcheck source=val/lib/report.sh
source "$ROOT/val/lib/report.sh"
# shellcheck source=val/lib/run.sh
source "$ROOT/val/lib/run.sh"

STARTS="$TMP/starts"
RELOADS="$TMP/reloads"
MODE='css'
PASS_ROUND=4
export STARTS RELOADS MODE PASS_ROUND

val_require_core() { :; }
val_config_load() { VAL_CONFIG_JSON='{}'; export VAL_CONFIG_JSON; }
val_config_get() { [ "$1" != '.routes[]' ] || return 0; return 1; }
val_detect_driver() { VAL_DRIVER='node'; export VAL_DRIVER; printf '%s\n' node; }
val_server_start() { printf '%s\n' start >> "$STARTS"; VAL_URL='http://fixture.invalid'; VAL_STARTED_BY_US='true'; export VAL_URL VAL_STARTED_BY_US; }
val_server_stop() { :; }
val_detect_browser_mode() { printf '%s\n' local; }
val_run_has_dark_theme() { return 1; }
val_auth_prepare() { printf '%s\n' ''; }
val_checklist_update() { printf '%s\n' '[]' > "$1"; }
val_fix_collect_allowed_files() { if [ "$MODE" = css ]; then printf '%s\n' src/ui.css; else printf '%s\n' package.json; fi; }
val_run_capture_round() { mkdir -p "$1/shots"; printf '%s\n' '{"shots":[]}' > "$3"; }
val_verify() {
  local output="$3" round status='fail' fail_count=1
  round="${output#*round-}"; round="${round%%/*}"
  if [ "$round" -ge "$PASS_ROUND" ]; then status='pass'; fail_count=0; fi
  jq -n --arg status "$status" --argjson fail "$fail_count" '{protocolVersion:1,findings:(if $fail == 0 then [] else [{criterionId:"c-1",checkId:"overflow",status:$status,severity:"P0",shot:"shot.png",route:"/",viewport:[375,812],theme:"light",detail:"fixture"}] end),criteria:[{id:"c-1",source:"default",assert:"fixture",status:$status,active:true}],summary:{pass:(if $fail == 0 then 1 else 0 end),fail:$fail,manual:0,blocked:0}}' > "$output"
}
val_judge() { jq -n '[{criterionId:"c-1",finding:"fixture",file:"src/ui.css",line:1,expected:"fixed",actual:"broken",suggestedFix:"fixture",actionable:true}]' > "$2"; }
val_fix_apply_judgments() {
  val_fix_record_attempt "$3" 'c-1'
  if [ "$MODE" = css ]; then printf '%s\n' src/ui.css; else printf '%s\n' package.json; fi
}
val_run_reload_for_files() {
  if [ "$1" = package.json ]; then printf '%s\n' restart >> "$RELOADS"; else printf '%s\n' hmr >> "$RELOADS"; fi
}

: > "$STARTS"; : > "$RELOADS"
val_run css-loop >/dev/null || fail 'CSS loop did not finish green'
[ "$(awk 'END {print NR}' "$STARTS")" -eq 1 ] || fail 'CSS rounds started the server more than once'
[ "$(grep -c '^hmr$' "$RELOADS")" -eq 3 ] || fail 'CSS loop did not perform three HMR-only rounds'

: > "$STARTS"; : > "$RELOADS"
MODE='package'; PASS_ROUND=2; export MODE PASS_ROUND
val_run package-loop >/dev/null || fail 'dependency loop did not finish green'
[ "$(awk 'END {print NR}' "$STARTS")" -eq 1 ] || fail 'dependency loop initial server count is wrong'
[ "$(grep -c '^restart$' "$RELOADS")" -eq 1 ] || fail 'dependency change did not trigger exactly one restart'

val_server_start() { printf '%s\n' 'fixture container failed to boot' > "$VAL_SERVER_LOG"; return 3; }
set +e
val_run infra-loop >/dev/null 2>&1
status=$?
set -e
[ "$status" -eq 3 ] || fail 'infrastructure failure did not return 3'
infra_dir="$VAL_STATE_DIR/runs/infra-loop"
jq -e '.exitCode == 3 and .rounds == 0' "$infra_dir/manifest.json" >/dev/null || fail 'infrastructure manifest missing'
grep -q 'fixture container failed to boot' "$infra_dir/final/report.md" || fail 'infrastructure report omitted server log tail'

printf '%s\n' 'PASS: VAL CSS HMR and dependency restart loop counts'
