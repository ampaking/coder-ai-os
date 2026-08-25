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
VAL_CONFIG_FILE="$VAL_STATE_DIR/config.json"
export VAL_SOURCE_DIR VAL_PROJECT_DIR VAL_STATE_DIR VAL_CONFIG_FILE
mkdir -p "$VAL_STATE_DIR"
cp "$ROOT/val/templates/val.config.json" "$VAL_CONFIG_FILE"

# shellcheck source=val/lib/common.sh
source "$ROOT/val/lib/common.sh"
# shellcheck source=val/lib/config.sh
source "$ROOT/val/lib/config.sh"
# shellcheck source=val/lib/checklist.sh
source "$ROOT/val/lib/checklist.sh"
# shellcheck source=val/lib/run.sh
source "$ROOT/val/lib/run.sh"

output="$TMP/checklist.json"
val_config_load
normalized="$(jq -n '[{target:"/",assert:"known",method:"deterministic",checkId:"overflow",confidence:"explicit"},{target:"/",assert:"unknown",method:"deterministic",checkId:"invented",confidence:"assumed"},{target:"/",assert:"soft",method:"vlm",confidence:"assumed"}]' | val_checklist_normalize)"
jq -e '.[0].status == "pending" and .[1].status == "manual" and .[2].status == "manual"' <<< "$normalized" >/dev/null || fail 'unsupported prompt criteria could become fake-green'
val_checklist_update "$output" 'Make the pricing page feel premium'
[ "$(jq 'length' "$output")" -eq 8 ] || fail 'defaults and prompt criterion were not merged'
jq -e 'any(.[]; .source == "prompt" and .method == "manual" and .status == "manual")' "$output" >/dev/null || fail 'offline prompt was fake-green instead of manual'
first_id="$(jq -r '.[] | select(.source == "prompt") | .id' "$output")"
val_checklist_update "$output" 'Make the pricing page feel premium'
[ "$(jq 'length' "$output")" -eq 8 ] || fail 'criterion target/assert deduplication failed'
[ "$(jq -r '.[] | select(.source == "prompt") | .id' "$output")" = "$first_id" ] || fail 'criterion hash is unstable'

val_checklist_extract_prompt() {
  jq -n --arg old "$first_id" '[{source:"prompt",target:"UI",assert:"Use a utilitarian visual style",method:"manual",confidence:"explicit",status:"manual",active:true,supersedes:[$old]}]'
}
val_checklist_update "$output" 'Use a utilitarian visual style'
jq -e --arg old "$first_id" 'any(.[]; .id == $old and .active == false and (.supersededBy | type == "string"))' "$output" >/dev/null || fail 'contradicting criterion did not supersede history'
jq -e 'any(.[]; .assert == "Use a utilitarian visual style" and .active == true)' "$output" >/dev/null || fail 'new contradicting criterion is not active'

browser="$TMP/browser.json"
jq -n '{protocolVersion:1,shots:[{path:"shot.png",route:"/",viewport:[375,812],theme:"light",checks:[{id:"overflow",status:"pass",detail:"ok"},{id:"clipped",status:"pass",detail:"ok"},{id:"axe",status:"pass",detail:"ok"},{id:"focus",status:"pass",detail:"ok"},{id:"tap-target",status:"pass",detail:"ok"},{id:"viewport",status:"pass",detail:"ok"},{id:"pixel",status:"manual",detail:"missing"}]}]}' > "$browser"
# shellcheck source=val/lib/verify.sh
source "$ROOT/val/lib/verify.sh"
val_verify "$browser" "$output" "$TMP/results.json"
jq -e '.summary == {pass:6,fail:0,manual:2,blocked:0}' "$TMP/results.json" >/dev/null || fail 'criterion-level verification summary is wrong'
jq -e 'any(.[]; .id == "c-default-overflow" and .status == "pass")' "$output" >/dev/null || fail 'resolved criterion status was not persisted'

VAL_CONFIG_JSON="$(jq '.routes=["/","/pricing"] | .viewports=[[375,812],[1440,900]] | .themes=["light"] | .maxShots=1' "$VAL_CONFIG_FILE")"
VAL_ALLOWED_FILES='src/pricing/Card.tsx'
VAL_CHANGED_ROUTES=''
VAL_PRIORITY_ROUTES="$(val_run_priority_routes "$output")"
export VAL_CONFIG_JSON VAL_ALLOWED_FILES VAL_CHANGED_ROUTES VAL_PRIORITY_ROUTES
jq -e 'length == 1 and .[0].route == "/pricing"' <<< "$(val_run_shots)" >/dev/null || fail 'changed route did not receive maxShots priority'

printf '%s\n' 'PASS: VAL persistent checklist defaults and honest manual fallback'
