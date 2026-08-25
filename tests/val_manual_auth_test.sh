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
export VAL_SOURCE_DIR VAL_PROJECT_DIR VAL_STATE_DIR VAL_CONFIG_FILE
mkdir -p "$VAL_STATE_DIR"
jq '.auth={loginUrl:"/login",steps:[{fill:"input[name=email]",value:"${VAL_USER}"}],storageState:".coder-ai/val/auth.json",successCheck:"text=Logout"}' "$ROOT/val/templates/val.config.json" > "$VAL_CONFIG_FILE"

# shellcheck source=val/lib/common.sh
source "$ROOT/val/lib/common.sh"
# shellcheck source=val/lib/config.sh
source "$ROOT/val/lib/config.sh"
# shellcheck source=val/lib/auth.sh
source "$ROOT/val/lib/auth.sh"
val_config_load
VAL_URL='http://fixture.invalid'
export VAL_URL

outside="$TMP/outside/auth.json"
VAL_CONFIG_JSON="$(printf '%s' "$VAL_CONFIG_JSON" | jq --arg path "$outside" '.auth.storageState=$path')"
if val_auth_state_path >/dev/null 2>&1; then fail 'out-of-state auth path was accepted'; fi
[ ! -e "$TMP/outside" ] || fail 'rejected auth path created a directory outside VAL state'
val_config_load

val_browser_run() {
  local request state
  request="$(jq -c '.')"
  printf '%s' "$request" | jq -e '.operation == "auth-manual" and .url == "http://fixture.invalid/login"' >/dev/null || fail 'manual auth browser request is wrong'
  state="$(printf '%s' "$request" | jq -r '.authState')"
  printf '%s\n' '{}' > "$state"
  chmod 600 "$state"
  jq -n --arg state "$state" '{protocolVersion:1,status:"authenticated",statePath:$state,steps:[{action:"manual",status:"pass",path:"manual-success.png"}]}'
}

run_dir="$VAL_STATE_DIR/runs/manual-test"
mkdir -p "$run_dir"
state="$(val_auth_manual "$run_dir")"
[ "$state" = "$(cd "$VAL_STATE_DIR" && pwd -P)/auth.json" ] || fail 'manual auth returned the wrong state path'
[ "$(stat -f '%Lp' "$state" 2>/dev/null || stat -c '%a' "$state")" = 600 ] || fail 'manual auth state mode is not 600'

printf '%s\n' 'PASS: VAL manual authentication orchestration'
