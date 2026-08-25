#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

driver_container_port() {
  local configured
  configured="$(val_config_get '.container.port // empty' || true)"
  if [ -n "$configured" ]; then printf '%s\n' "$configured"; return 0; fi
  docker image inspect "$VAL_CONTAINER_IMAGE" | jq -er '.[0].Config.ExposedPorts | keys | if length == 1 then .[0] | split("/")[0] else empty end'
}

driver_start() {
  local configured_image image_key project_key container_port mapping
  configured_image="$(val_config_get '.container.image // empty' || true)"
  if [ -n "$configured_image" ]; then
    VAL_CONTAINER_IMAGE="$configured_image"
  else
    image_key="$(printf '%s' "$VAL_PROJECT_DIR" | cksum | awk '{print $1}')"
    VAL_CONTAINER_IMAGE="coder-ai-os-val-app:$image_key"
    docker build --tag "$VAL_CONTAINER_IMAGE" "$VAL_PROJECT_DIR" >> "$VAL_SERVER_LOG" 2>&1 || { val_die 3 "Docker build failed; see $VAL_SERVER_LOG"; return 3; }
  fi
  container_port="$(driver_container_port)" || { val_die 3 'Docker requires exactly one EXPOSE port or container.port config'; return 3; }
  project_key="$(printf '%s' "$VAL_PROJECT_DIR" | cksum | awk '{print $1}')"
  VAL_CONTAINER_NAME="coder-ai-os-val-$project_key-$VAL_RUN_ID-$$"
  if ! VAL_CONTAINER_ID="$(docker run --detach --rm \
      --name "$VAL_CONTAINER_NAME" \
      --label "coder-ai-os.val.run-id=$VAL_RUN_ID" \
      --publish "127.0.0.1::$container_port" \
      "$VAL_CONTAINER_IMAGE")"; then
    val_die 3 'Docker failed to start the application container'
    return 3
  fi
  VAL_OWNER_TYPE='container'
  VAL_STARTED_BY_US='true'
  export VAL_CONTAINER_IMAGE VAL_CONTAINER_NAME VAL_CONTAINER_ID VAL_OWNER_TYPE VAL_STARTED_BY_US
  mapping="$(docker port "$VAL_CONTAINER_ID" "$container_port/tcp" | awk 'NF {value=$0} END {print value}')"
  VAL_PORT="${mapping##*:}"
  case "$VAL_PORT" in *[!0-9]*|'') val_die 3 'Docker did not publish a usable host port'; return 3 ;; esac
  val_server_resolve_url
  export VAL_CONTAINER_IMAGE VAL_CONTAINER_NAME VAL_CONTAINER_ID VAL_PORT VAL_URL VAL_OWNER_TYPE VAL_STARTED_BY_US
}

driver_alive() {
  [ "$(docker inspect -f '{{.State.Running}}' "$VAL_CONTAINER_ID" 2>/dev/null || true)" = true ]
}

driver_health() {
  val_server_health_configured
}

driver_reload() {
  printf '%s\n' rebuild
}

driver_stop() {
  [ "$VAL_STARTED_BY_US" = true ] || return 0
  val_server_identity_matches || return 1
  docker stop --time 5 "$VAL_CONTAINER_ID" >> "$VAL_SERVER_LOG" 2>&1 || true
}

driver_failure_logs() {
  docker logs --tail 50 "$VAL_CONTAINER_ID" 2>&1 || true
}
