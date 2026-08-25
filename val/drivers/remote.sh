#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

driver_start() {
  val_die 3 'remote driver cannot start a server'
  return 3
}

driver_health() {
  val_server_health_configured
}

driver_alive() {
  return 0
}

driver_reload() {
  printf '%s\n' 'hmr'
}

driver_stop() {
  return 0
}

driver_failure_logs() {
  return 0
}
