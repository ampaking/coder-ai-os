#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

driver_compose() {
  (cd "$VAL_PROJECT_DIR" && docker compose "$@")
}

driver_compose_service() {
  local configured services count
  configured="$(val_config_get '.container.service // empty' || true)"
  if [ -n "$configured" ]; then printf '%s\n' "$configured"; return 0; fi
  services="$(driver_compose config --services)"
  count="$(printf '%s\n' "$services" | awk 'NF {count++} END {print count+0}')"
  [ "$count" -eq 1 ] || { val_die 3 'Compose has multiple services; set container.service'; return 3; }
  printf '%s\n' "$services"
}

driver_compose_port() {
  local configured exposed count
  configured="$(val_config_get '.container.port // empty' || true)"
  if [ -n "$configured" ]; then printf '%s\n' "$configured"; return 0; fi
  exposed="$(docker inspect "$VAL_CONTAINER_ID" | jq -r '.[0].Config.ExposedPorts // {} | keys[] | split("/")[0]')"
  count="$(printf '%s\n' "$exposed" | awk 'NF {count++} END {print count+0}')"
  [ "$count" -eq 1 ] || { val_die 3 'Compose service requires exactly one exposed port or container.port config'; return 3; }
  printf '%s\n' "$exposed"
}

driver_start() {
  local existing container_port mapping
  VAL_COMPOSE_SERVICE="$(driver_compose_service)" || return 3
  existing="$(driver_compose ps --quiet "$VAL_COMPOSE_SERVICE")"
  if [ -n "$existing" ] && [ "$(docker inspect -f '{{.State.Running}}' "$existing" 2>/dev/null || true)" = true ]; then
    VAL_CONTAINER_ID="$existing"
    VAL_STARTED_BY_US='false'
  else
    driver_compose up --detach --no-deps "$VAL_COMPOSE_SERVICE" >> "$VAL_SERVER_LOG" 2>&1 || { driver_compose logs --tail 50 "$VAL_COMPOSE_SERVICE" >> "$VAL_SERVER_LOG" 2>&1 || true; val_die 3 "Compose start failed; see $VAL_SERVER_LOG"; return 3; }
    VAL_CONTAINER_ID="$(driver_compose ps --quiet "$VAL_COMPOSE_SERVICE")"
    VAL_STARTED_BY_US='true'
  fi
  [ -n "$VAL_CONTAINER_ID" ] || { val_die 3 'Compose did not return a container ID'; return 3; }
  VAL_OWNER_TYPE='container'
  export VAL_COMPOSE_SERVICE VAL_CONTAINER_ID VAL_OWNER_TYPE VAL_STARTED_BY_US
  container_port="$(driver_compose_port)" || return 3
  mapping="$(driver_compose port "$VAL_COMPOSE_SERVICE" "$container_port" | awk 'NF {value=$0} END {print value}')"
  VAL_PORT="${mapping##*:}"
  case "$VAL_PORT" in *[!0-9]*|'') val_die 3 'Compose did not publish a usable host port'; return 3 ;; esac
  val_server_resolve_url
  VAL_CONTAINER_NAME="$(docker inspect -f '{{.Name}}' "$VAL_CONTAINER_ID" | awk '{sub(/^\//, ""); print}')"
  export VAL_COMPOSE_SERVICE VAL_CONTAINER_ID VAL_CONTAINER_NAME VAL_PORT VAL_URL VAL_OWNER_TYPE VAL_STARTED_BY_US
}

driver_alive() {
  [ "$(docker inspect -f '{{.State.Running}}' "$VAL_CONTAINER_ID" 2>/dev/null || true)" = true ]
}

driver_health() {
  local health
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$VAL_CONTAINER_ID" 2>/dev/null || true)"
  if [ -n "$health" ]; then [ "$health" = healthy ]; else val_server_health_configured; fi
}

driver_reload() {
  printf '%s\n' rebuild
}

driver_stop() {
  [ "$VAL_STARTED_BY_US" = true ] || return 0
  val_server_identity_matches || return 1
  driver_compose stop --timeout 5 "$VAL_COMPOSE_SERVICE" >> "$VAL_SERVER_LOG" 2>&1 || true
}

driver_failure_logs() {
  driver_compose logs --tail 50 "$VAL_COMPOSE_SERVICE" 2>&1 || true
}
