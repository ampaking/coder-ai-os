#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

VAL_SOURCE_DIR="$ROOT/val"
VAL_PROJECT_DIR="$TMP/project"
VAL_STATE_DIR="$VAL_PROJECT_DIR/.coder-ai/val"
export VAL_SOURCE_DIR VAL_PROJECT_DIR VAL_STATE_DIR
mkdir -p "$VAL_STATE_DIR/round/shots" "$VAL_PROJECT_DIR/src"
printf '%s\n' 'button { color: gray; }' > "$VAL_PROJECT_DIR/src/ui.css"
printf '%s' 'fixture-image' > "$VAL_STATE_DIR/round/shots/fail.png"
jq -n --arg shot "$VAL_STATE_DIR/round/shots/fail.png" '{findings:[{criterionId:"c-fail",status:"fail",detail:"contrast low",shot:$shot},{criterionId:"c-pass",status:"pass",detail:"ok",shot:""}]}' > "$TMP/results.json"

# shellcheck source=val/lib/common.sh
source "$ROOT/val/lib/common.sh"
# shellcheck source=val/lib/judge.sh
source "$ROOT/val/lib/judge.sh"
# shellcheck source=val/lib/run.sh
source "$ROOT/val/lib/run.sh"

val_judge "$TMP/results.json" "$TMP/offline.json"
jq -e 'length == 1 and .[0].actionable == false and .[0].criterionId == "c-fail"' "$TMP/offline.json" >/dev/null || fail 'offline judge did not remain non-actionable'

curl() {
  local payload=''
  while [ "$#" -gt 0 ]; do
    if [ "$1" = --data-binary ]; then payload="$(awk '{sub(/^@/, ""); print}' <<< "$2")"; shift 2; else shift; fi
  done
  jq -e '.input[1].content | any(.type == "input_image")' "$payload" >/dev/null || fail 'failing screenshot was not attached'
  jq -e '.input[1].content[0].text | contains("button { color: gray; }")' "$payload" >/dev/null || fail 'allowlisted fallback source context was not attached'
  jq -n '{output:[{content:[{type:"output_text",text:"[{\"criterionId\":\"c-fail\",\"finding\":\"contrast low\",\"file\":\"src/ui.css\",\"line\":1,\"expected\":\"4.5:1\",\"actual\":\"3.1:1\",\"suggestedFix\":\"use a darker color\"}]"}]}]}'
}
export -f curl
VAL_API_KEY='fixture-key'
VAL_ALLOWED_FILES='src/ui.css'
export VAL_API_KEY VAL_ALLOWED_FILES
val_judge "$TMP/results.json" "$TMP/online.json"
jq -e 'length == 1 and .[0].actionable == true and .[0].file == "src/ui.css"' "$TMP/online.json" >/dev/null || fail 'valid judge response was rejected'
val_run_merge_judgments "$TMP/results.json" "$TMP/online.json"
jq -e '.findings[0].file == "src/ui.css" and .findings[0].line == 1 and .findings[0].expected == "4.5:1"' "$TMP/results.json" >/dev/null || fail 'judge localization was not merged into final findings'

printf '%s\n' 'TOP_SECRET=never-send' > "$VAL_PROJECT_DIR/.env"
VAL_ALLOWED_FILES=$'.env\nsrc/ui.css'
export VAL_ALLOWED_FILES
if val_judge_safe_snippet '.env' 1 >/dev/null 2>&1; then fail 'secret file was accepted as judge context'; fi

printf '%s\n' 'PASS: VAL failure-only judge contract'
