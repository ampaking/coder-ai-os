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

VAL_PROJECT_DIR="$TMP/project"
VAL_STATE_DIR="$VAL_PROJECT_DIR/.coder-ai/val"
VAL_CONFIG_FILE="$VAL_STATE_DIR/config.json"
VAL_SOURCE_DIR="$ROOT/val"
export VAL_PROJECT_DIR VAL_STATE_DIR VAL_CONFIG_FILE VAL_SOURCE_DIR
mkdir -p "$VAL_STATE_DIR"
cp "$ROOT/val/templates/val.config.json" "$VAL_CONFIG_FILE"

# shellcheck source=val/lib/common.sh
source "$ROOT/val/lib/common.sh"
# shellcheck source=val/lib/config.sh
source "$ROOT/val/lib/config.sh"
# shellcheck source=val/lib/reload.sh
source "$ROOT/val/lib/reload.sh"
val_config_load

jq '.watchGlobs=["frontend/**/*.{astro,pcss}"]' "$VAL_CONFIG_FILE" > "$TMP/config.next"
mv "$TMP/config.next" "$VAL_CONFIG_FILE"
val_config_load
[ "$(val_reload_decide node true true frontend/pages/home.astro)" = hmr ] || fail 'configured brace watch glob did not match'

[ "$(val_reload_decide node true true src/Card.tsx)" = hmr ] || fail 'node view change did not select HMR'
[ "$(val_reload_decide node true true package.json)" = restart ] || fail 'node dependency change did not restart'
[ "$(val_reload_decide compose true true src/Card.tsx)" = hmr ] || fail 'mounted Compose view change did not select HMR'
[ "$(val_reload_decide compose false true src/Card.tsx 2>/dev/null)" = rebuild ] || fail 'unmounted Compose view change did not rebuild'
[ "$(val_reload_decide docker true true Dockerfile)" = rebuild ] || fail 'Dockerfile change did not rebuild'
[ "$(val_reload_decide compose true true docker-compose.yml)" = recreate ] || fail 'Compose file change did not recreate'
[ "$(val_reload_decide compose true false src/Card.tsx)" = recreate ] || fail 'failed container health did not recreate'
[ "$(val_reload_decide node true false src/Card.tsx)" = restart ] || fail 'failed node health did not restart'
[ "$(val_reload_decide compose true true src/Card.tsx package.json)" = rebuild ] || fail 'highest-priority action was not retained'

VAL_MOUNT_WARNING_EMITTED='false'
warning_file="$TMP/warnings"
val_reload_decide compose false true a.css >/dev/null 2> "$warning_file"
val_reload_decide compose false true b.css >/dev/null 2>> "$warning_file"
[ "$(wc -l < "$warning_file" | awk '{print $1}')" -eq 1 ] || fail 'unmounted-source warning was not emitted exactly once'

MOUNT_SOURCE="$VAL_PROJECT_DIR"
docker() {
  jq -n --arg source "$MOUNT_SOURCE" '[{Mounts:[{Source:$source,Destination:"/app"}]}]'
}
val_reload_source_mounted fixture || fail 'project bind mount was not detected from docker inspect'
MOUNT_SOURCE="$TMP/different-source"
if val_reload_source_mounted fixture; then fail 'unrelated Docker mount was mistaken for the project source'; fi

CALLS="$TMP/reload.calls"
: > "$CALLS"
val_server_load_driver() { printf '%s\n' load >> "$CALLS"; }
val_server_stop() { printf '%s\n' stop >> "$CALLS"; }
val_server_start() { printf '%s\n' start >> "$CALLS"; }
VAL_DRIVER='node'
VAL_STARTED_BY_US='false'
export VAL_DRIVER VAL_STARTED_BY_US
val_reload_apply hmr
[ ! -s "$CALLS" ] || fail 'HMR mutated server lifecycle'
set +e
val_reload_apply restart
status=$?
set -e
[ "$status" -eq 2 ] && [ ! -s "$CALLS" ] || fail 'external server restart was not blocked'
VAL_STARTED_BY_US='true'
val_reload_apply restart
[ "$(awk 'END {print NR}' "$CALLS")" -eq 3 ] || fail 'owned Node restart did not load, stop, and start exactly once'

printf '%s\n' 'PASS: VAL reload decision matrix'
