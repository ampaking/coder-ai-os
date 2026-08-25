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
node "$ROOT/tests/fixtures/val_auth_server.mjs" "$port" &
SERVER_PID=$!
url="http://127.0.0.1:$port"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl --fail --silent "$url/login" >/dev/null 2>&1 && break
  sleep 0.1
done

VAL_PROJECT_DIR="$TMP/project"
VAL_STATE_DIR="$VAL_PROJECT_DIR/.coder-ai/val"
VAL_CONFIG_FILE="$VAL_STATE_DIR/config.json"
VAL_SOURCE_DIR="$ROOT/val"
VAL_BROWSER_CACHE_DIR="${VAL_TEST_BROWSER_CACHE_DIR:-/tmp/coder-ai-os-val-browser-test-1.62.1}"
export VAL_PROJECT_DIR VAL_STATE_DIR VAL_CONFIG_FILE VAL_SOURCE_DIR VAL_BROWSER_CACHE_DIR
mkdir -p "$VAL_STATE_DIR"
jq '.browserMode="local"' "$ROOT/val/templates/val.config.json" > "$VAL_CONFIG_FILE"

# shellcheck source=val/lib/common.sh
source "$ROOT/val/lib/common.sh"
# shellcheck source=val/lib/config.sh
source "$ROOT/val/lib/config.sh"
# shellcheck source=val/lib/detect.sh
source "$ROOT/val/lib/detect.sh"
# shellcheck source=val/lib/browser.sh
source "$ROOT/val/lib/browser.sh"

state="$VAL_STATE_DIR/auth.json"
evidence="$VAL_STATE_DIR/runs/auth-test/auth"
request="$(jq -n --arg url "$url/login" --arg outDir "$evidence" --arg authState "$state" '{protocolVersion:1,operation:"auth-login",url:$url,outDir:$outDir,authState:$authState,successCheck:"text=Logout",steps:[{fill:"input[name=email]",value:"test@example.invalid"},{fill:"input[name=password]",value:"fixture-password"},{click:"button[type=submit]"},{waitFor:"/dashboard"}]}')"
printf '%s\n' "$request" | val_browser_run > "$TMP/login.json"
jq -e '.status == "authenticated" and (.steps | length == 4)' "$TMP/login.json" >/dev/null || fail 'login response was not authenticated'
[ "$(stat -f '%Lp' "$state" 2>/dev/null || stat -c '%a' "$state")" = 600 ] || fail 'storage state mode is not 600'
[ "$(find "$evidence" -name 'step-*.png' | awk 'END {print NR}')" -eq 4 ] || fail 'auth step evidence is incomplete'
if rg -q 'fixture-password|test@example.invalid' "$TMP/login.json"; then fail 'credentials leaked in browser JSON output'; fi

jq -n --arg url "$url/login" --arg outDir "$evidence" --arg authState "$state" '{protocolVersion:1,operation:"auth-check",url:$url,outDir:$outDir,authState:$authState,successCheck:"text=Logout"}' \
  | val_browser_run > "$TMP/check.json"
jq -e '.status == "valid"' "$TMP/check.json" >/dev/null || fail 'saved storage state was not reusable'

jq -n --arg url "$url" --arg outDir "$VAL_STATE_DIR/runs/public-test/shots" '{protocolVersion:1,url:$url,outDir:$outDir,baselineDir:null,authState:null,checks:["viewport"],shots:[{route:"/public",viewport:[375,812],theme:"light"},{route:"/login",viewport:[375,812],theme:"light"}]}' \
  | val_browser_run > "$TMP/public.json"
jq -e '(.shots | length == 2) and (.shots[0].checks[0].status == "pass") and (.shots[1].blocked == "auth-required")' "$TMP/public.json" >/dev/null || fail 'public routes did not continue around an auth-required route'

printf '%s\n' 'PASS: VAL browser authentication protocol'
