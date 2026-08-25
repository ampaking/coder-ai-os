#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

driver_start() {
  local serve_command port_flag shell_path
  serve_command="$(val_config_get '.serve')"
  port_flag="$(val_config_get '.portFlag')"
  shell_path="${SHELL:-/bin/bash}"

  [ -n "$serve_command" ] || { val_die 3 'serve command is empty'; return 3; }
  : > "$VAL_SERVER_LOG"

  set -m
  if [ "$port_flag" = env ]; then
    PORT="$VAL_PORT" "$shell_path" -c "$serve_command" >> "$VAL_SERVER_LOG" 2>&1 &
  else
    "$shell_path" -c "$serve_command -- --port \"\$1\"" val-port "$VAL_PORT" >> "$VAL_SERVER_LOG" 2>&1 &
  fi
  VAL_SERVER_PID=$!
  set +m

  VAL_SERVER_PGID="$(ps -o pgid= -p "$VAL_SERVER_PID" | awk '{print $1}')"
  if [ -z "$VAL_SERVER_PGID" ] || [ "$VAL_SERVER_PGID" != "$VAL_SERVER_PID" ]; then
    kill -TERM -"$VAL_SERVER_PID" 2>/dev/null || true
    val_die 3 'server did not start in an isolated process group'
    return 3
  fi
  export VAL_SERVER_PID VAL_SERVER_PGID
  VAL_OWNER_TYPE='process'
  export VAL_OWNER_TYPE
}

driver_alive() {
  kill -0 "$VAL_SERVER_PID" 2>/dev/null
}

driver_health() {
  val_server_health_configured
}

driver_reload() {
  printf '%s\n' 'hmr'
}

driver_stop() {
  val_server_signal_owned TERM
  val_server_wait_stopped 5 || val_server_signal_owned KILL
}
