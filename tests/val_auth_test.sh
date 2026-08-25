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

project="$TMP/project"
mkdir -p "$project/.coder-ai/val"
jq --arg url "$url" '.env="remote" | .browserMode="local" | .url=$url | .routes=["/dashboard"] | .themes=["light"] | .viewports=[[375,812]] | .auth={loginUrl:"/login",steps:[{fill:"input[name=email]",value:"${VAL_TEST_USER}"},{fill:"input[name=password]",value:"${VAL_TEST_PASS}"},{click:"button[type=submit]"},{waitFor:"/dashboard"}],storageState:".coder-ai/val/auth.json",successCheck:"text=Logout"}' \
  "$ROOT/val/templates/val.config.json" > "$project/.coder-ai/val/config.json"

run_auth() {
  (cd "$project" && VAL_BROWSER_CACHE_DIR="${VAL_TEST_BROWSER_CACHE_DIR:-/tmp/coder-ai-os-val-browser-test-1.62.1}" VAL_TEST_USER="$1" VAL_TEST_PASS="$2" "$ROOT/val/val" auth)
}

run_auth 'test@example.invalid' 'fixture-password' > "$TMP/success-1.log"
[ "$(curl --fail --silent "$url/count")" -eq 1 ] || fail 'first authentication did not login exactly once'
run_auth 'test@example.invalid' 'fixture-password' > "$TMP/success-2.log"
[ "$(curl --fail --silent "$url/count")" -eq 1 ] || fail 'valid state was not reused'
state="$project/.coder-ai/val/auth.json"
[ "$(stat -f '%Lp' "$state" 2>/dev/null || stat -c '%a' "$state")" = 600 ] || fail 'reused state mode is not 600'

rm -f "$state"
set +e
run_auth 'test@example.invalid' 'wrong-fixture-value' > "$TMP/failure.log" 2>&1
status=$?
set -e
[ "$status" -eq 3 ] || fail 'wrong credentials did not return infra exit 3'
[ "$(curl --fail --silent "$url/count")" -eq 3 ] || fail 'wrong credentials did not stop after two attempts'
failure="$(find "$project/.coder-ai/val/runs" -path '*/auth/failure.json' | tail -n 1)"
[ -f "$failure" ] || fail 'auth failure JSON was not preserved'
jq -e '.status == "failed" and (.steps[-1].status == "fail")' "$failure" >/dev/null || fail 'failing auth step was not reported'
if rg -q 'wrong-fixture-value' "$project/.coder-ai/val/runs"; then fail 'credential leaked into auth artifacts'; fi

printf '%s\n' 'PASS: VAL authentication reuse and bounded failure policy'
