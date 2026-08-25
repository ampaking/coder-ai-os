#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

ROOT="$1"
PROJECT="$2"
VAL_RUN_ID="$3"
INFO_FILE="$4"
RELEASE_FILE="$5"
VAL_PROJECT_DIR="$PROJECT"
VAL_CONFIG_FILE="$PROJECT/.coder-ai/val/config.json"
VAL_SOURCE_DIR="$ROOT/val"
unset VAL_LOCK_DIR VAL_LOCK_FILE VAL_SERVER_LOG VAL_STATE_DIR
export VAL_RUN_ID VAL_PROJECT_DIR VAL_CONFIG_FILE VAL_SOURCE_DIR

# shellcheck source=val/lib/common.sh
source "$ROOT/val/lib/common.sh"
# shellcheck source=val/lib/config.sh
source "$ROOT/val/lib/config.sh"
# shellcheck source=val/lib/detect.sh
source "$ROOT/val/lib/detect.sh"
# shellcheck source=val/lib/server.sh
source "$ROOT/val/lib/server.sh"

cleanup() {
  val_server_stop
}
on_signal() {
  val_server_stop
  trap - EXIT
  exit 130
}
trap cleanup EXIT
trap on_signal INT TERM

val_server_start
temporary="$(mktemp "${INFO_FILE}.XXXXXX")"
jq -n --argjson pid "$VAL_SERVER_PID" --argjson port "$VAL_PORT" --arg url "$VAL_URL" '{pid:$pid,port:$port,url:$url}' > "$temporary"
mv "$temporary" "$INFO_FILE"
while [ ! -e "$RELEASE_FILE" ]; do
  sleep 0.1
done
