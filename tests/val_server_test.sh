#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP="$(mktemp -d)"
OWNED_PID=''
EXTERNAL_PID=''
RUNNER_ONE=''
RUNNER_TWO=''
ABANDONED_RUNNER=''

cleanup() {
  [ -z "$OWNED_PID" ] || kill -TERM "$OWNED_PID" 2>/dev/null || true
  [ -z "$EXTERNAL_PID" ] || kill -TERM "$EXTERNAL_PID" 2>/dev/null || true
  [ -z "$RUNNER_ONE" ] || kill -TERM "$RUNNER_ONE" 2>/dev/null || true
  [ -z "$RUNNER_TWO" ] || kill -TERM "$RUNNER_TWO" 2>/dev/null || true
  [ -z "$ABANDONED_RUNNER" ] || kill -TERM "$ABANDONED_RUNNER" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

wait_for_file() {
  local file="$1"
  local attempt
  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    [ -s "$file" ] && return 0
    sleep 0.1
  done
  return 1
}

project="$TMP/project with spaces"
mkdir -p "$project/.coder-ai/val"
serve="$ROOT/tests/fixtures/val_http_server.py"
sed "s|__SERVE__|$serve|" "$ROOT/tests/fixtures/val.node.config.json" > "$project/.coder-ai/val/config.json"

VAL_PROJECT_DIR="$project"
VAL_CONFIG_FILE="$project/.coder-ai/val/config.json"
VAL_SOURCE_DIR="$ROOT/val"
export VAL_PROJECT_DIR VAL_CONFIG_FILE VAL_SOURCE_DIR
# shellcheck source=val/lib/common.sh
source "$ROOT/val/lib/common.sh"
# shellcheck source=val/lib/config.sh
source "$ROOT/val/lib/config.sh"
# shellcheck source=val/lib/detect.sh
source "$ROOT/val/lib/detect.sh"
# shellcheck source=val/lib/server.sh
source "$ROOT/val/lib/server.sh"

val_server_start
OWNED_PID="$VAL_SERVER_PID"
[ -f "$VAL_LOCK_FILE" ] || fail 'owned server lock missing'
curl --fail --silent "$VAL_URL/ready" >/dev/null || fail 'owned server is not healthy'

VAL_LOCK_FILE_ORIGINAL="$VAL_LOCK_FILE"
stop_output="$(val_server_stop_all)"
printf '%s\n' "$stop_output" | grep -q 'stopped 1 managed server' || fail 'stop-all did not report the owned server'
if kill -0 "$OWNED_PID" 2>/dev/null; then fail 'stop-all left the owned server running'; fi
[ ! -f "$VAL_LOCK_FILE_ORIGINAL" ] || fail 'stop-all left the owned lock behind'
val_server_start
OWNED_PID="$VAL_SERVER_PID"

first_pid="$VAL_SERVER_PID"
temporary_lock="$(mktemp "$VAL_STATE_DIR/.test-lock.XXXXXX")"
jq '.startedAtEpoch=0' "$VAL_LOCK_FILE" > "$temporary_lock"
mv "$temporary_lock" "$VAL_LOCK_FILE"
val_server_start
OWNED_PID="$VAL_SERVER_PID"
[ "$VAL_SERVER_PID" != "$first_pid" ] || fail 'stale owned server was reused'
if kill -0 "$first_pid" 2>/dev/null; then fail 'stale owned server remains alive'; fi

val_server_stop
[ ! -f "$VAL_LOCK_FILE" ] || fail 'owned server lock remains after stop'
if kill -0 "$OWNED_PID" 2>/dev/null; then fail 'owned server remains alive after stop'; fi
OWNED_PID=''

external_port="$(val_server_allocate_port)"
python3 "$serve" "$external_port" >/dev/null 2>&1 &
EXTERNAL_PID=$!
external_url="http://127.0.0.1:$external_port"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl --fail --silent "$external_url/ready" >/dev/null 2>&1 && break
  sleep 0.1
done
jq --arg url "$external_url" '.url=$url | .healthPath="/ready" | .reuseExisting=true' "$ROOT/val/templates/val.config.json" > "$project/.coder-ai/val/config.json"
VAL_CONFIG_JSON=''
VAL_DRIVER='node'
VAL_DRIVER_SOURCED='false'
export VAL_CONFIG_JSON VAL_DRIVER VAL_DRIVER_SOURCED
val_server_start
[ "$VAL_STARTED_BY_US" = false ] || fail 'external server was claimed as owned'
VAL_DRIVER='remote'
VAL_DRIVER_SOURCED='false'
val_server_load_driver
[ "$(driver_reload ignored.css)" = hmr ] || fail 'remote driver did not preserve read-only reload behavior'
val_server_stop
kill -0 "$EXTERNAL_PID" 2>/dev/null || fail 'external server was stopped'
VAL_DRIVER='node'
VAL_DRIVER_SOURCED='false'

printf '%s\n' '{broken' > "$VAL_LOCK_FILE"
VAL_CONFIG_JSON=''
sed "s|__SERVE__|$serve|" "$ROOT/tests/fixtures/val.node.config.json" > "$project/.coder-ai/val/config.json"
val_server_start
OWNED_PID="$VAL_SERVER_PID"
jq -e '.protocolVersion == 1' "$VAL_LOCK_FILE" >/dev/null || fail 'corrupt lock was not replaced'
val_server_stop
OWNED_PID=''

runner="$ROOT/tests/fixtures/val_server_runner.sh"
release_one="$TMP/release-one"
release_two="$TMP/release-two"
info_one="$TMP/info-one.json"
info_two="$TMP/info-two.json"
bash "$runner" "$ROOT" "$project" parallel-one "$info_one" "$release_one" &
RUNNER_ONE=$!
bash "$runner" "$ROOT" "$project" parallel-two "$info_two" "$release_two" &
RUNNER_TWO=$!
wait_for_file "$info_one" || fail 'first parallel runner did not start'
wait_for_file "$info_two" || fail 'second parallel runner did not start'
port_one="$(jq -r '.port' "$info_one")"
port_two="$(jq -r '.port' "$info_two")"
[ "$port_one" != "$port_two" ] || fail 'parallel runs selected the same port'
lock_count="$(find "$VAL_LOCK_DIR" -type f -name '*.json' | wc -l | awk '{print $1}')"
if [ "$lock_count" -ne 2 ]; then
  find "$VAL_LOCK_DIR" -type f -name '*.json' -print >&2
  fail "parallel runs kept $lock_count locks instead of 2"
fi
touch "$release_one" "$release_two"
wait "$RUNNER_ONE"
wait "$RUNNER_TWO"
RUNNER_ONE=''
RUNNER_TWO=''

signal_release="$TMP/release-signal"
signal_info="$TMP/info-signal.json"
bash "$runner" "$ROOT" "$project" signal-run "$signal_info" "$signal_release" &
RUNNER_ONE=$!
wait_for_file "$signal_info" || fail 'signal runner did not start'
signal_server="$(jq -r '.pid' "$signal_info")"
kill -TERM "$RUNNER_ONE"
wait "$RUNNER_ONE" 2>/dev/null || true
RUNNER_ONE=''
if kill -0 "$signal_server" 2>/dev/null; then fail 'SIGTERM left the owned server alive'; fi
[ ! -e "$VAL_LOCK_DIR/signal-run.json" ] || fail 'SIGTERM left its lock behind'

interrupt_release="$TMP/release-interrupt"
interrupt_info="$TMP/info-interrupt.json"
set -m
bash "$runner" "$ROOT" "$project" interrupt-run "$interrupt_info" "$interrupt_release" &
RUNNER_ONE=$!
set +m
wait_for_file "$interrupt_info" || fail 'interrupt runner did not start'
interrupt_server="$(jq -r '.pid' "$interrupt_info")"
kill -INT -"$RUNNER_ONE"
wait "$RUNNER_ONE" 2>/dev/null || true
RUNNER_ONE=''
if kill -0 "$interrupt_server" 2>/dev/null; then fail 'SIGINT left the owned server alive'; fi
[ ! -e "$VAL_LOCK_DIR/interrupt-run.json" ] || fail 'SIGINT left its lock behind'

abandoned_release="$TMP/release-abandoned"
abandoned_info="$TMP/info-abandoned.json"
bash "$runner" "$ROOT" "$project" abandoned "$abandoned_info" "$abandoned_release" &
ABANDONED_RUNNER=$!
wait_for_file "$abandoned_info" || fail 'abandoned runner did not start'
abandoned_server="$(jq -r '.pid' "$abandoned_info")"
kill -KILL "$ABANDONED_RUNNER"
wait "$ABANDONED_RUNNER" 2>/dev/null || true
ABANDONED_RUNNER=''
kill -0 "$abandoned_server" 2>/dev/null || fail 'kill -9 fixture did not leave an orphan server'
VAL_RUN_ID='recovery'
VAL_LOCK_FILE="$VAL_LOCK_DIR/$VAL_RUN_ID.json"
export VAL_RUN_ID VAL_LOCK_FILE
val_server_start
OWNED_PID="$VAL_SERVER_PID"
if kill -0 "$abandoned_server" 2>/dev/null; then fail 'abandoned owned server was not cleaned'; fi
val_server_stop
OWNED_PID=''

printf '%s\n' 'PASS: VAL server lifecycle'
