#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

VAL_RUN_ID="${VAL_RUN_ID:-run-$$}"
VAL_LOCK_DIR="${VAL_LOCK_DIR:-$VAL_STATE_DIR/locks}"
VAL_LOCK_FILE="${VAL_LOCK_FILE:-$VAL_LOCK_DIR/$VAL_RUN_ID.json}"
VAL_SERVER_LOG="${VAL_SERVER_LOG:-$VAL_STATE_DIR/server.log}"
VAL_RUNNER_PID="$$"
VAL_RUNNER_STARTED_AT=''
VAL_SERVER_PID=''
VAL_SERVER_PGID=''
VAL_SERVER_STARTED_AT=''
VAL_STARTED_BY_US='false'
VAL_PORT=''
VAL_URL=''
VAL_DRIVER_SOURCED='false'
VAL_OWNER_TYPE='external'
VAL_CONTAINER_ID=''
VAL_CONTAINER_NAME=''
VAL_CONTAINER_IMAGE=''
VAL_COMPOSE_SERVICE=''
export VAL_RUN_ID VAL_LOCK_DIR VAL_LOCK_FILE VAL_SERVER_LOG VAL_RUNNER_PID VAL_RUNNER_STARTED_AT VAL_OWNER_TYPE VAL_CONTAINER_ID VAL_CONTAINER_NAME VAL_CONTAINER_IMAGE VAL_COMPOSE_SERVICE

val_server_init_runner() {
  case "$VAL_RUN_ID" in
    *[!A-Za-z0-9._-]*) val_die 3 "invalid VAL_RUN_ID: $VAL_RUN_ID"; return 3 ;;
  esac
  if [ -z "$VAL_RUNNER_STARTED_AT" ]; then
    VAL_RUNNER_STARTED_AT="$(ps -o lstart= -p "$VAL_RUNNER_PID" 2>/dev/null | awk '{$1=$1; print}')"
    [ -n "$VAL_RUNNER_STARTED_AT" ] || { val_die 3 'cannot identify the VAL runner process'; return 3; }
    export VAL_RUNNER_STARTED_AT
  fi
}

val_server_health_url() {
  local url="$1"
  curl --silent --show-error --fail --output /dev/null --max-time 2 "$url" 2>/dev/null
}

val_server_health_configured() {
  local health_path endpoint
  health_path="$(val_config_get '.healthPath')"
  endpoint="${VAL_URL%/}${health_path}"
  val_server_health_url "$endpoint"
}

val_server_process_start() {
  local pid="$1"
  ps -o lstart= -p "$pid" 2>/dev/null | awk '{$1=$1; print}'
}

val_server_identity_matches() {
  local actual_pgid actual_started
  if [ "$VAL_OWNER_TYPE" = container ]; then
    [ -n "$VAL_CONTAINER_ID" ] || return 1
    [ "$(docker inspect -f '{{.Id}}' "$VAL_CONTAINER_ID" 2>/dev/null || true)" = "$VAL_CONTAINER_ID" ] || return 1
    if [ "$VAL_DRIVER" = docker ]; then
      [ "$(docker inspect -f '{{index .Config.Labels "coder-ai-os.val.run-id"}}' "$VAL_CONTAINER_ID" 2>/dev/null || true)" = "$VAL_RUN_ID" ]
    else
      [ -n "$VAL_COMPOSE_SERVICE" ] \
        && [ "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$VAL_CONTAINER_ID" 2>/dev/null || true)" = "$VAL_COMPOSE_SERVICE" ]
    fi
    return
  fi
  [ "$VAL_OWNER_TYPE" = process ] || return 1
  [ -n "$VAL_SERVER_PID" ] && [ -n "$VAL_SERVER_PGID" ] && [ -n "$VAL_SERVER_STARTED_AT" ] || return 1
  kill -0 "$VAL_SERVER_PID" 2>/dev/null || return 1
  actual_pgid="$(ps -o pgid= -p "$VAL_SERVER_PID" 2>/dev/null | awk '{print $1}')"
  actual_started="$(val_server_process_start "$VAL_SERVER_PID")"
  [ "$actual_pgid" = "$VAL_SERVER_PGID" ] && [ "$actual_started" = "$VAL_SERVER_STARTED_AT" ]
}

val_server_signal_owned() {
  local signal="$1"
  [ "$VAL_STARTED_BY_US" = true ] || return 0
  if ! val_server_identity_matches; then
    val_log 'refused signal: server identity no longer matches lock'
    return 1
  fi
  kill -"$signal" -"$VAL_SERVER_PGID" 2>/dev/null || true
}

val_server_wait_stopped() {
  local seconds="$1" started="$SECONDS"
  while kill -0 "$VAL_SERVER_PID" 2>/dev/null; do
    [ $((SECONDS - started)) -lt "$seconds" ] || return 1
    sleep 0.1
  done
}

val_server_port_from_url() {
  local url="$1" authority
  authority="${url#*://}"
  authority="${authority%%/*}"
  case "$authority" in
    *:*) printf '%s\n' "${authority##*:}" ;;
    *)
      case "$url" in
        https://*) printf '%s\n' 443 ;;
        *) printf '%s\n' 80 ;;
      esac
      ;;
  esac
}

val_server_allocate_port() {
  if val_command_ready python3; then
    python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
  elif val_command_ready ruby; then
    ruby -rsocket -e 's=TCPServer.new("127.0.0.1",0); puts s.addr[1]; s.close'
  elif val_command_ready node; then
    node -e 'const s=require("net").createServer();s.listen(0,"127.0.0.1",()=>{console.log(s.address().port);s.close()})'
  elif val_command_ready php; then
    # The single-quoted value is PHP source, not a shell expansion.
    # shellcheck disable=SC2016
    php -r '$s=stream_socket_server("tcp://127.0.0.1:0",$e,$m);echo parse_url(stream_socket_get_name($s,false),PHP_URL_PORT),PHP_EOL;fclose($s);'
  else
    val_die 3 'local server mode needs python3, ruby, node, or php to allocate a port'
    return 3
  fi
}

val_server_resolve_url() {
  # Literal config placeholder; expansion happens only through Bash substitution below.
  # shellcheck disable=SC2016
  local template port_placeholder='${PORT}'
  template="$(val_config_get '.url')"
  VAL_URL="${template//$port_placeholder/$VAL_PORT}"
  export VAL_URL
}

val_server_wait_healthy() {
  local timeout_ms started_ms now_ms
  timeout_ms="$(val_config_get '.readyTimeoutMs | floor')"
  if { [ "$VAL_DRIVER" = docker ] || [ "$VAL_DRIVER" = compose ]; } && [ "$timeout_ms" -lt 120000 ]; then
    timeout_ms=120000
  fi
  started_ms=$(( $(date +%s) * 1000 ))
  while ! driver_health; do
    driver_alive || return 1
    now_ms=$(( $(date +%s) * 1000 ))
    [ $((now_ms - started_ms)) -lt "$timeout_ms" ] || return 1
    sleep 0.2
  done
}

val_server_write_lock() {
  local temporary
  mkdir -p "$VAL_LOCK_DIR"
  temporary="$(mktemp "$VAL_LOCK_DIR/.lock.XXXXXX")"
  jq -n \
    --argjson pid "${VAL_SERVER_PID:-null}" \
    --argjson pgid "${VAL_SERVER_PGID:-null}" \
    --argjson port "$VAL_PORT" \
    --arg startedAt "$VAL_SERVER_STARTED_AT" \
    --argjson startedAtEpoch "$(date +%s)" \
    --arg driver "$VAL_DRIVER" \
    --arg url "$VAL_URL" \
    --argjson startedByUs "$VAL_STARTED_BY_US" \
    --arg ownerType "$VAL_OWNER_TYPE" \
    --arg containerId "$VAL_CONTAINER_ID" \
    --arg containerName "$VAL_CONTAINER_NAME" \
    --arg containerImage "$VAL_CONTAINER_IMAGE" \
    --arg composeService "$VAL_COMPOSE_SERVICE" \
    --argjson runnerPid "$VAL_RUNNER_PID" \
    --arg runnerStartedAt "$VAL_RUNNER_STARTED_AT" \
    --arg runId "$VAL_RUN_ID" \
    '{protocolVersion:1,runId:$runId,runnerPid:$runnerPid,runnerStartedAt:$runnerStartedAt,ownerType:$ownerType,pid:$pid,pgid:$pgid,containerId:$containerId,containerName:$containerName,containerImage:$containerImage,composeService:$composeService,port:$port,startedAt:$startedAt,startedAtEpoch:$startedAtEpoch,driver:$driver,url:$url,startedByUs:$startedByUs}' \
    > "$temporary"
  mv "$temporary" "$VAL_LOCK_FILE"
}

val_server_read_lock() {
  [ -f "$VAL_LOCK_FILE" ] || return 1
  jq -e '.protocolVersion == 1 and (.port | type == "number") and (.startedByUs | type == "boolean")' "$VAL_LOCK_FILE" >/dev/null 2>&1 || return 1
  VAL_SERVER_PID="$(jq -r '.pid // empty' "$VAL_LOCK_FILE")"
  VAL_SERVER_PGID="$(jq -r '.pgid // empty' "$VAL_LOCK_FILE")"
  VAL_PORT="$(jq -r '.port' "$VAL_LOCK_FILE")"
  VAL_SERVER_STARTED_AT="$(jq -r '.startedAt // empty' "$VAL_LOCK_FILE")"
  VAL_STARTED_BY_US="$(jq -r '.startedByUs' "$VAL_LOCK_FILE")"
  VAL_OWNER_TYPE="$(jq -r '.ownerType // (if .pid then "process" else "external" end)' "$VAL_LOCK_FILE")"
  VAL_CONTAINER_ID="$(jq -r '.containerId // empty' "$VAL_LOCK_FILE")"
  VAL_CONTAINER_NAME="$(jq -r '.containerName // empty' "$VAL_LOCK_FILE")"
  VAL_CONTAINER_IMAGE="$(jq -r '.containerImage // empty' "$VAL_LOCK_FILE")"
  VAL_COMPOSE_SERVICE="$(jq -r '.composeService // empty' "$VAL_LOCK_FILE")"
  VAL_DRIVER="$(jq -r '.driver' "$VAL_LOCK_FILE")"
  VAL_URL="$(jq -r '.url' "$VAL_LOCK_FILE")"
  export VAL_SERVER_PID VAL_SERVER_PGID VAL_PORT VAL_SERVER_STARTED_AT VAL_STARTED_BY_US VAL_OWNER_TYPE VAL_CONTAINER_ID VAL_CONTAINER_NAME VAL_CONTAINER_IMAGE VAL_COMPOSE_SERVICE VAL_DRIVER VAL_URL
}

val_server_lock_is_stale() {
  local file="${1:-$VAL_LOCK_FILE}" started_at_epoch now
  started_at_epoch="$(jq -r '.startedAtEpoch // 0' "$file" 2>/dev/null || printf '%s' 0)"
  case "$started_at_epoch" in *[!0-9]*|'') return 0 ;; esac
  now="$(date +%s)"
  [ $((now - started_at_epoch)) -gt 1800 ]
}

val_server_runner_owns_lock() {
  local file="$1" runner_pid runner_started actual_started
  runner_pid="$(jq -r '.runnerPid // empty' "$file" 2>/dev/null || true)"
  runner_started="$(jq -r '.runnerStartedAt // empty' "$file" 2>/dev/null || true)"
  [ -n "$runner_pid" ] && [ -n "$runner_started" ] || return 1
  kill -0 "$runner_pid" 2>/dev/null || return 1
  actual_started="$(val_server_process_start "$runner_pid")"
  [ "$actual_started" = "$runner_started" ]
}

val_server_cleanup_abandoned_locks() {
  local file own_lock requested_driver
  own_lock="$VAL_LOCK_FILE"
  requested_driver="$VAL_DRIVER"
  mkdir -p "$VAL_LOCK_DIR"
  for file in "$VAL_LOCK_DIR"/*.json; do
    [ -e "$file" ] || continue
    [ "$file" != "$own_lock" ] || continue
    if val_server_runner_owns_lock "$file" && ! val_server_lock_is_stale "$file"; then
      continue
    fi
    VAL_LOCK_FILE="$file"
    if val_server_read_lock && [ "$VAL_STARTED_BY_US" = true ] && val_server_identity_matches; then
      VAL_DRIVER_SOURCED='false'
      val_server_load_driver
      driver_stop || true
    fi
    rm -f "$file"
  done
  VAL_LOCK_FILE="$own_lock"
  VAL_DRIVER="$requested_driver"
  VAL_DRIVER_SOURCED='false'
  VAL_SERVER_PID=''
  VAL_SERVER_PGID=''
  VAL_SERVER_STARTED_AT=''
  VAL_STARTED_BY_US='false'
  VAL_OWNER_TYPE='external'
  VAL_CONTAINER_ID=''
  VAL_CONTAINER_NAME=''
  VAL_CONTAINER_IMAGE=''
  VAL_COMPOSE_SERVICE=''
  export VAL_LOCK_FILE VAL_DRIVER VAL_SERVER_PID VAL_SERVER_PGID VAL_SERVER_STARTED_AT VAL_STARTED_BY_US VAL_OWNER_TYPE VAL_CONTAINER_ID VAL_CONTAINER_NAME VAL_CONTAINER_IMAGE VAL_COMPOSE_SERVICE
}

val_server_load_driver() {
  [ "$VAL_DRIVER_SOURCED" = false ] || return 0
  case "$VAL_DRIVER" in
    node)
      # shellcheck source=val/drivers/node.sh
      source "$VAL_SOURCE_DIR/drivers/node.sh"
      ;;
    docker)
      # shellcheck source=val/drivers/docker.sh
      source "$VAL_SOURCE_DIR/drivers/docker.sh"
      ;;
    compose)
      # shellcheck source=val/drivers/compose.sh
      source "$VAL_SOURCE_DIR/drivers/compose.sh"
      ;;
    remote)
      # shellcheck source=val/drivers/remote.sh
      source "$VAL_SOURCE_DIR/drivers/remote.sh"
      ;;
    *)
      val_die 3 "driver not implemented yet: $VAL_DRIVER"
      return 3
      ;;
  esac
  VAL_DRIVER_SOURCED='true'
}

val_server_try_reuse_lock() {
  if ! val_server_read_lock; then
    rm -f "$VAL_LOCK_FILE"
    return 1
  fi
  if val_server_lock_is_stale; then
    if [ "$VAL_STARTED_BY_US" = true ] && val_server_identity_matches; then
      val_server_load_driver
      driver_stop || true
    fi
    rm -f "$VAL_LOCK_FILE"
    return 1
  fi
  if val_server_health_configured; then
    if [ "$VAL_STARTED_BY_US" = true ] && ! val_server_identity_matches; then
      val_die 3 'healthy locked URL has mismatched process identity; refusing to replace its ownership record'
      return 2
    fi
    val_log "reused locked server url=$VAL_URL startedByUs=$VAL_STARTED_BY_US"
    return 0
  fi
  if [ "$VAL_STARTED_BY_US" = true ] && val_server_identity_matches; then
    val_server_load_driver
    driver_stop || true
  fi
  rm -f "$VAL_LOCK_FILE"
  return 1
}

val_server_try_fixed_url() {
  # shellcheck disable=SC2016
  local template reuse port_placeholder='${PORT}'
  template="$(val_config_get '.url')"
  reuse="$(val_config_get '.reuseExisting')"
  case "$template" in *"$port_placeholder"*) return 1 ;; esac
  [ "$reuse" = true ] || return 1
  VAL_URL="$template"
  val_server_health_configured || return 1
  VAL_PORT="$(val_server_port_from_url "$template")"
  VAL_SERVER_PID=''
  VAL_SERVER_PGID=''
  VAL_SERVER_STARTED_AT=''
  VAL_STARTED_BY_US='false'
  VAL_OWNER_TYPE='external'
  VAL_CONTAINER_ID=''
  VAL_CONTAINER_NAME=''
  VAL_CONTAINER_IMAGE=''
  VAL_COMPOSE_SERVICE=''
  val_server_write_lock
  val_log "reused external server url=$VAL_URL"
}

val_server_start() {
  local attempt reuse_status
  val_config_load
  val_server_init_runner
  [ -n "$VAL_DRIVER" ] || val_detect_driver >/dev/null
  mkdir -p "$VAL_STATE_DIR"
  val_server_cleanup_abandoned_locks
  if val_server_try_reuse_lock; then
    return 0
  else
    reuse_status=$?
    [ "$reuse_status" -ne 2 ] || return 3
  fi
  if [ "$VAL_DRIVER" = remote ]; then
    val_server_try_fixed_url || { val_die 3 'remote URL is not healthy'; return 3; }
    return 0
  fi
  if val_server_try_fixed_url; then return 0; fi
  val_server_load_driver
  if [ "$VAL_DRIVER" = docker ] || [ "$VAL_DRIVER" = compose ]; then
    driver_start
    if val_server_wait_healthy; then
      val_server_write_lock
      val_log "started container server url=$VAL_URL startedByUs=$VAL_STARTED_BY_US"
      return 0
    fi
    driver_failure_logs >> "$VAL_SERVER_LOG" 2>&1 || true
    driver_stop || true
    val_die 3 "container server never became healthy; see $VAL_SERVER_LOG"
    return 3
  fi
  for attempt in 1 2 3; do
    VAL_PORT="$(val_server_allocate_port)"
    val_server_resolve_url
    driver_start
    VAL_SERVER_STARTED_AT="$(val_server_process_start "$VAL_SERVER_PID")"
    VAL_STARTED_BY_US='true'
    export VAL_SERVER_STARTED_AT VAL_STARTED_BY_US
    if val_server_wait_healthy; then
      val_server_write_lock
      val_log "started server url=$VAL_URL attempt=$attempt"
      return 0
    fi
    driver_stop || true
  done
  val_die 3 "server never became healthy; see $VAL_SERVER_LOG"
  return 3
}

val_server_stop() {
  val_server_init_runner
  if [ -z "$VAL_SERVER_PID" ] && [ -f "$VAL_LOCK_FILE" ]; then
    val_server_read_lock || { rm -f "$VAL_LOCK_FILE"; return 0; }
  fi
  if [ "$VAL_STARTED_BY_US" = true ]; then
    val_server_load_driver
    driver_stop || true
  fi
  rm -f "$VAL_LOCK_FILE"
  VAL_SERVER_PID=''
  VAL_SERVER_PGID=''
  VAL_STARTED_BY_US='false'
  VAL_OWNER_TYPE='external'
  VAL_CONTAINER_ID=''
  VAL_CONTAINER_NAME=''
  VAL_CONTAINER_IMAGE=''
  VAL_COMPOSE_SERVICE=''
}

val_server_stop_all() {
  local file original_lock="$VAL_LOCK_FILE"
  local stopped=0
  mkdir -p "$VAL_LOCK_DIR"
  for file in "$VAL_LOCK_DIR"/*.json; do
    [ -e "$file" ] || continue
    VAL_LOCK_FILE="$file"
    if ! val_server_read_lock; then
      rm -f "$file"
      continue
    fi
    if [ "$VAL_STARTED_BY_US" = true ]; then
      if val_server_identity_matches; then
        VAL_DRIVER_SOURCED='false'
        val_server_load_driver
        driver_stop || true
        stopped=$((stopped + 1))
      else
        val_log "refused stop: ownership identity no longer matches lock=$file"
      fi
    fi
    rm -f "$file"
  done
  VAL_LOCK_FILE="$original_lock"
  VAL_SERVER_PID=''
  VAL_SERVER_PGID=''
  VAL_STARTED_BY_US='false'
  VAL_OWNER_TYPE='external'
  VAL_CONTAINER_ID=''
  VAL_CONTAINER_NAME=''
  VAL_CONTAINER_IMAGE=''
  VAL_COMPOSE_SERVICE=''
  printf 'VAL stop: stopped %s managed server(s)\n' "$stopped"
}
