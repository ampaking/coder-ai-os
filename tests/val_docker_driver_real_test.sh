#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP="$(mktemp -d)"
VAL_CONTAINER_ID=''

cleanup() {
  if [ -n "$VAL_CONTAINER_ID" ]; then docker stop --time 1 "$VAL_CONTAINER_ID" >/dev/null 2>&1 || true; fi
  rm -rf "$TMP"
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

project="$TMP/docker app"
mkdir -p "$project/.coder-ai/val"
cp "$ROOT/tests/fixtures/val-docker-app/Dockerfile" "$ROOT/tests/fixtures/val-docker-app/server.mjs" "$project/"
jq '.env="docker" | .browserMode="docker" | .serve="" | .url="http://127.0.0.1:${PORT}" | .healthPath="/" | .readyTimeoutMs=120000 | .routes=["/"] | .container={service:null,port:null,image:null}' "$ROOT/val/templates/val.config.json" > "$project/.coder-ai/val/config.json"

VAL_PROJECT_DIR="$project"
VAL_STATE_DIR="$project/.coder-ai/val"
VAL_CONFIG_FILE="$VAL_STATE_DIR/config.json"
VAL_SOURCE_DIR="$ROOT/val"
VAL_SERVER_LOG="$TMP/server.log"
VAL_RUN_ID='real-docker-driver'
export VAL_PROJECT_DIR VAL_STATE_DIR VAL_CONFIG_FILE VAL_SOURCE_DIR VAL_SERVER_LOG VAL_RUN_ID

# shellcheck source=val/lib/common.sh
source "$ROOT/val/lib/common.sh"
# shellcheck source=val/lib/config.sh
source "$ROOT/val/lib/config.sh"
# shellcheck source=val/lib/detect.sh
source "$ROOT/val/lib/detect.sh"
# shellcheck source=val/lib/server.sh
source "$ROOT/val/lib/server.sh"

VAL_DRIVER='docker'
val_server_start
[ "$VAL_OWNER_TYPE" = container ] && [ "$VAL_STARTED_BY_US" = true ] || fail 'real Docker container ownership missing'
curl --fail --silent "$VAL_URL" >/dev/null || fail 'real Docker application is not healthy'
owned="$VAL_CONTAINER_ID"
val_server_stop
VAL_CONTAINER_ID=''
if docker inspect "$owned" >/dev/null 2>&1; then fail 'owned --rm Docker container remains after stop'; fi
[ ! -f "$VAL_LOCK_FILE" ] || fail 'real Docker lock remains after stop'

printf '%s\n' 'PASS: VAL real Docker application driver'
