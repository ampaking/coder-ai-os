#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP="$(mktemp -d)"
SERVER_PID=''

cleanup() {
  [ -z "$SERVER_PID" ] || kill -TERM "$SERVER_PID" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
python3 -m http.server "$port" --bind 127.0.0.1 --directory "$ROOT/tests/fixtures" >/dev/null 2>&1 &
SERVER_PID=$!
url="http://127.0.0.1:$port"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl --fail --silent "$url/val_deterministic_page.html" >/dev/null 2>&1 && break
  sleep 0.1
done

VAL_PROJECT_DIR="$TMP/project"
VAL_STATE_DIR="$VAL_PROJECT_DIR/.coder-ai/val"
VAL_CONFIG_FILE="$VAL_STATE_DIR/config.json"
VAL_SOURCE_DIR="$ROOT/val"
VAL_BROWSER_IMAGE='coder-ai-os-val-browser:1.62.1'
export VAL_PROJECT_DIR VAL_STATE_DIR VAL_CONFIG_FILE VAL_SOURCE_DIR VAL_BROWSER_IMAGE
mkdir -p "$VAL_STATE_DIR/runs/docker/shots"

# shellcheck source=val/lib/common.sh
source "$ROOT/val/lib/common.sh"
# shellcheck source=val/lib/config.sh
source "$ROOT/val/lib/config.sh"
# shellcheck source=val/lib/detect.sh
source "$ROOT/val/lib/detect.sh"
# shellcheck source=val/lib/browser.sh
source "$ROOT/val/lib/browser.sh"

request="$(jq -n --arg url "$url" --arg outDir "$VAL_STATE_DIR/runs/docker/shots" '{protocolVersion:1,url:$url,outDir:$outDir,baselineDir:null,authState:null,settle:true,checks:["viewport"],shots:[{route:"/val_deterministic_page.html",viewport:[375,812],theme:"light"}]}')"
result="$(printf '%s\n' "$request" | val_browser_run_docker)"
printf '%s' "$result" | jq -e '.protocolVersion == 1 and (.shots | length == 1)' >/dev/null || fail 'Docker browser response invalid'
[ "$(find "$VAL_STATE_DIR/runs/docker/shots" -maxdepth 1 -name 'val_deterministic_page.html--*__375x812__light.png' | awk 'END {print NR}')" -eq 1 ] || fail 'Docker screenshot missing from host state'

printf '%s\n' 'PASS: VAL real Docker browser capture'
