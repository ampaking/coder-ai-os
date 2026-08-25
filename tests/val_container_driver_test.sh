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

FAKE_EXISTING=''
FAKE_SERVICES='web'
FAKE_COMPOSE_STATE="$TMP/compose-started"
DOCKER_LOG="$TMP/docker.log"

docker() {
  printf '%s ' "$@" >> "$DOCKER_LOG"
  printf '\n' >> "$DOCKER_LOG"
  case "${1:-}" in
    image)
      printf '%s\n' '[{"Config":{"ExposedPorts":{"8000/tcp":{}}}}]'
      ;;
    build) ;;
    run) printf '%s\n' container-owned ;;
    port) printf '%s\n' '127.0.0.1:49123' ;;
    stop) ;;
    logs) printf '%s\n' 'fixture logs' ;;
    inspect)
      if [ "${2:-}" = -f ]; then
        case "${3:-}" in
          '{{.Id}}') printf '%s\n' "${4:-}" ;;
          '{{.State.Running}}') printf '%s\n' true ;;
          '{{.Name}}') printf '/%s\n' "${4:-}" ;;
          *coder-ai-os.val.run-id*) printf '%s\n' "$VAL_RUN_ID" ;;
          *com.docker.compose.service*) printf '%s\n' web ;;
          *Health*) printf '%s\n' '' ;;
        esac
      else
        printf '%s\n' '[{"Config":{"ExposedPorts":{"8000/tcp":{}}}}]'
      fi
      ;;
    compose)
      shift
      case "${1:-}" in
        config) printf '%s\n' "$FAKE_SERVICES" ;;
        ps)
          if [ -f "$FAKE_COMPOSE_STATE" ]; then printf '%s\n' compose-owned
          elif [ -n "$FAKE_EXISTING" ]; then printf '%s\n' "$FAKE_EXISTING"; fi
          ;;
        up) touch "$FAKE_COMPOSE_STATE" ;;
        port) printf '%s\n' '127.0.0.1:49124' ;;
        stop) ;;
        logs) printf '%s\n' 'compose fixture logs' ;;
      esac
      ;;
  esac
}

VAL_PROJECT_DIR="$TMP/project"
VAL_STATE_DIR="$VAL_PROJECT_DIR/.coder-ai/val"
VAL_CONFIG_FILE="$VAL_STATE_DIR/config.json"
VAL_SOURCE_DIR="$ROOT/val"
VAL_SERVER_LOG="$TMP/server.log"
VAL_RUN_ID='container-test'
export VAL_PROJECT_DIR VAL_STATE_DIR VAL_CONFIG_FILE VAL_SOURCE_DIR VAL_SERVER_LOG VAL_RUN_ID
mkdir -p "$VAL_STATE_DIR"
jq '.env="docker" | .container.image="fixture:latest" | .container.port=8000' "$ROOT/val/templates/val.config.json" > "$VAL_CONFIG_FILE"

# shellcheck source=val/lib/common.sh
source "$ROOT/val/lib/common.sh"
# shellcheck source=val/lib/config.sh
source "$ROOT/val/lib/config.sh"
# shellcheck source=val/lib/detect.sh
source "$ROOT/val/lib/detect.sh"
# shellcheck source=val/lib/server.sh
source "$ROOT/val/lib/server.sh"
val_config_load

VAL_DRIVER='docker'
val_server_load_driver
driver_start
[ "$VAL_OWNER_TYPE" = container ] && [ "$VAL_STARTED_BY_US" = true ] || fail 'Docker ownership was not recorded'
[ "$VAL_CONTAINER_NAME" != "coder-ai-os-val-$VAL_RUN_ID" ] || fail 'Docker container name lacks project/run uniqueness'
[ "$VAL_PORT" = 49123 ] || fail 'Docker published port was not discovered'
val_server_write_lock
jq -e '.ownerType == "container" and .containerId == "container-owned"' "$VAL_LOCK_FILE" >/dev/null || fail 'container lock fields missing'
driver_stop
grep -q 'stop.*container-owned' "$DOCKER_LOG" || fail 'owned Docker container was not stopped'

: > "$DOCKER_LOG"
VAL_DRIVER='compose'
VAL_DRIVER_SOURCED='false'
VAL_CONFIG_JSON=''
FAKE_EXISTING='compose-existing'
jq '.env="compose" | .container.service="web" | .container.port=8000' "$ROOT/val/templates/val.config.json" > "$VAL_CONFIG_FILE"
val_config_load
val_server_load_driver
driver_start
[ "$VAL_STARTED_BY_US" = false ] || fail 'existing Compose service was claimed as owned'
driver_stop
if grep -q '^compose.*stop' "$DOCKER_LOG"; then fail 'existing Compose service was stopped'; fi

: > "$DOCKER_LOG"
FAKE_EXISTING=''
rm -f "$FAKE_COMPOSE_STATE"
driver_start
[ "$VAL_STARTED_BY_US" = true ] || fail 'new Compose service was not marked owned'
[ "$VAL_CONTAINER_ID" = compose-owned ] || fail 'new Compose container ID missing'
driver_stop
grep -q 'compose stop.*web' "$DOCKER_LOG" || fail 'owned Compose service was not stopped'

VAL_CONFIG_JSON=''
FAKE_SERVICES="web
worker"
jq '.env="compose" | .container.service=null | .container.port=8000' "$ROOT/val/templates/val.config.json" > "$VAL_CONFIG_FILE"
val_config_load
set +e
driver_compose_service >/dev/null 2>&1
status=$?
set -e
[ "$status" -eq 3 ] || fail 'ambiguous Compose services did not return infra status 3'

printf '%s\n' 'PASS: VAL container ownership contract'
