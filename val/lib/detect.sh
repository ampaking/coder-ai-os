#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

VAL_DRIVER=''
VAL_BROWSER_MODE=''

val_compose_running() {
  val_command_ready docker || return 1
  docker compose ps --status running --quiet 2>/dev/null | awk 'NF { found=1 } END { exit(found ? 0 : 1) }'
}

val_detect_driver() {
  local configured
  configured="$(val_config_get '.env')"
  if [ "$configured" != auto ]; then
    VAL_DRIVER="$configured"
  elif { [ -f "$VAL_PROJECT_DIR/docker-compose.yml" ] || [ -f "$VAL_PROJECT_DIR/docker-compose.yaml" ] || [ -f "$VAL_PROJECT_DIR/compose.yml" ] || [ -f "$VAL_PROJECT_DIR/compose.yaml" ]; } && val_compose_running; then
    VAL_DRIVER='compose'
  elif [ -f "$VAL_PROJECT_DIR/Dockerfile" ]; then
    VAL_DRIVER='docker'
  elif [ -n "$(val_config_get '.serve')" ]; then
    VAL_DRIVER='node'
  elif [ -n "$(val_config_get '.url')" ]; then
    VAL_DRIVER='remote'
  else
    val_die 3 'cannot detect a VAL environment driver'
    return 3
  fi
  export VAL_DRIVER
  val_log "driver=$VAL_DRIVER"
  printf '%s\n' "$VAL_DRIVER"
}

val_detect_browser_mode() {
  local configured
  configured="$(val_config_get '.browserMode')"
  if [ "$configured" != auto ]; then
    VAL_BROWSER_MODE="$configured"
  elif val_command_ready docker && docker info >/dev/null 2>&1; then
    VAL_BROWSER_MODE='docker'
  elif val_command_ready npm; then
    VAL_BROWSER_MODE='local'
  else
    val_die 3 'browser runtime missing: install Docker or npx'
    return 3
  fi
  export VAL_BROWSER_MODE
  printf '%s\n' "$VAL_BROWSER_MODE"
}
