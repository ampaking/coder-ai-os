#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP="$(mktemp -d)"
FIXTURE="$ROOT/tests/fixtures/val-docker-app"
PROJECT="$TMP/project"
COMPOSE_PROJECT_NAME="val-real-compose-$$"
export COMPOSE_PROJECT_NAME

cleanup() {
  (cd "$PROJECT" && docker compose down --remove-orphans >/dev/null 2>&1) || true
  rm -rf "$TMP"
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

mkdir -p "$PROJECT/.coder-ai/val"
cp "$FIXTURE/Dockerfile" "$FIXTURE/server.mjs" "$FIXTURE/compose.yaml" "$PROJECT/"
jq '.env="compose" | .url="http://127.0.0.1:${PORT}" | .container.service="web" | .container.port=8000 | .readyTimeoutMs=5000' \
  "$ROOT/val/templates/val.config.json" > "$PROJECT/.coder-ai/val/config.json"

VAL_PROJECT_DIR="$PROJECT"
VAL_STATE_DIR="$PROJECT/.coder-ai/val"
VAL_CONFIG_FILE="$VAL_STATE_DIR/config.json"
VAL_SOURCE_DIR="$ROOT/val"
VAL_SERVER_LOG="$VAL_STATE_DIR/server.log"
VAL_RUN_ID="compose-real-$$"
export VAL_PROJECT_DIR VAL_STATE_DIR VAL_CONFIG_FILE VAL_SOURCE_DIR VAL_SERVER_LOG VAL_RUN_ID

# shellcheck source=val/lib/common.sh
source "$ROOT/val/lib/common.sh"
# shellcheck source=val/lib/config.sh
source "$ROOT/val/lib/config.sh"
# shellcheck source=val/lib/detect.sh
source "$ROOT/val/lib/detect.sh"
# shellcheck source=val/lib/server.sh
source "$ROOT/val/lib/server.sh"

val_config_load
VAL_DRIVER='compose'
val_server_start
[ "$VAL_STARTED_BY_US" = true ] || fail 'VAL did not claim the Compose service it started'
owned_id="$VAL_CONTAINER_ID"
val_server_stop
[ "$(docker inspect -f '{{.State.Running}}' "$owned_id" 2>/dev/null || true)" = false ] || fail 'owned Compose service remained running'

(cd "$PROJECT" && docker compose up --detach --no-deps web >/dev/null)
VAL_RUN_ID="compose-reuse-$$"
VAL_LOCK_FILE="$VAL_LOCK_DIR/$VAL_RUN_ID.json"
VAL_DRIVER_SOURCED='false'
VAL_STARTED_BY_US='false'
export VAL_RUN_ID VAL_LOCK_FILE
val_server_start
[ "$VAL_STARTED_BY_US" = false ] || fail 'pre-existing Compose service was claimed as owned'
reused_id="$VAL_CONTAINER_ID"
val_server_stop
[ "$(docker inspect -f '{{.State.Running}}' "$reused_id")" = true ] || fail 'pre-existing Compose service was stopped'

printf '%s\n' 'PASS: real Compose ownership and reuse'
