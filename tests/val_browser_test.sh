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

capture() {
  local out_dir="$1" result_file="$2" baseline_dir="${3:-}"
  jq -n --arg url "$url" --arg outDir "$out_dir" --arg baselineDir "$baseline_dir" '{protocolVersion:1,url:$url,outDir:$outDir,baselineDir:(if $baselineDir == "" then null else $baselineDir end),settle:true,checks:["overflow","clipped","axe","focus","tap-target","viewport","pixel"],shots:[{route:"/val_deterministic_page.html",viewport:[375,812],theme:"light"}]}' \
    | val_browser_run > "$result_file"
  jq -e '.protocolVersion == 1 and (.shots | length == 1)' "$result_file" >/dev/null || fail 'browser response contract failed'
}

capture "$TMP/round-1" "$TMP/result-1.json"
capture "$TMP/round-2" "$TMP/result-2.json" "$TMP/round-1"
first="$(jq -r '.shots[0].path' "$TMP/result-1.json")"
second="$(jq -r '.shots[0].path' "$TMP/result-2.json")"
cmp -s "$first" "$second" || fail 'unchanged captures are not byte-identical'
jq -e '[.shots[0].checks[] | select(.id == "overflow" and .status == "fail")] | length == 1' "$TMP/result-2.json" >/dev/null || fail 'overflow check did not fail'
jq -e '[.shots[0].checks[] | select(.id == "clipped" and .status == "fail")] | length == 1' "$TMP/result-2.json" >/dev/null || fail 'clipped check did not fail'
jq -e '[.shots[0].checks[] | select(.id == "tap-target" and .status == "fail")] | length == 1' "$TMP/result-2.json" >/dev/null || fail 'tap-target check did not fail'
jq -e '[.shots[0].checks[] | select(.id == "focus" and .status == "fail")] | length == 1' "$TMP/result-2.json" >/dev/null || fail 'focus check did not fail'
jq -e '[.shots[0].checks[] | select(.id == "viewport" and .status == "pass")] | length == 1' "$TMP/result-2.json" >/dev/null || fail 'viewport check did not pass'
jq -e '[.shots[0].checks[] | select(.id == "pixel" and .status == "pass")] | length == 1' "$TMP/result-2.json" >/dev/null || fail 'identical baseline did not pass pixel check'

jq -n --arg url "$url" --arg outDir "$TMP/collision" '{protocolVersion:1,url:$url,outDir:$outDir,settle:true,checks:[],shots:[{route:"/",viewport:[375,812],theme:"light"},{route:"/root",viewport:[375,812],theme:"light"}]}' \
  | val_browser_run > "$TMP/collision.json"
[ "$(jq -r '.shots[0].path' "$TMP/collision.json")" != "$(jq -r '.shots[1].path' "$TMP/collision.json")" ] || fail 'colliding route slugs overwrote one screenshot'

# shellcheck source=val/lib/verify.sh
source "$ROOT/val/lib/verify.sh"
printf '%s\n' '[]' > "$TMP/checklist.json"
val_verify "$TMP/result-2.json" "$TMP/checklist.json" "$TMP/results.json"
jq -e '([.findings[] | select(.status == "fail")] | length) >= 3 and any(.findings[]; .checkId == "overflow" and .severity == "P0")' "$TMP/results.json" >/dev/null || fail 'verification severity merge failed'

printf '%s\n' 'PASS: VAL deterministic browser capture (zero diff)'
