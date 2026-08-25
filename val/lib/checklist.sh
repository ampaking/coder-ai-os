#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

val_checklist_defaults() {
  jq -n '[
    {id:"c-default-overflow",source:"default",target:"all routes",assert:"no horizontal overflow",method:"deterministic",checkId:"overflow",confidence:"explicit",status:"pending",active:true},
    {id:"c-default-clipped",source:"default",target:"visible landmarks",assert:"elements are not clipped or offscreen",method:"deterministic",checkId:"clipped",confidence:"explicit",status:"pending",active:true},
    {id:"c-default-axe",source:"default",target:"all routes",assert:"WCAG A/AA automated checks pass",method:"deterministic",checkId:"axe",confidence:"explicit",status:"pending",active:true},
    {id:"c-default-focus",source:"default",target:"interactive controls",assert:"keyboard focus is visible",method:"deterministic",checkId:"focus",confidence:"explicit",status:"pending",active:true},
    {id:"c-default-tap",source:"default",target:"interactive controls",assert:"tap targets are at least 44x44",method:"deterministic",checkId:"tap-target",confidence:"explicit",status:"pending",active:true},
    {id:"c-default-viewport",source:"default",target:"all routes",assert:"viewport metadata permits zoom",method:"deterministic",checkId:"viewport",confidence:"explicit",status:"pending",active:true},
    {id:"c-default-pixel",source:"default",target:"accepted baseline",assert:"pixel drift stays within 0.1%",method:"deterministic",checkId:"pixel",confidence:"explicit",status:"pending",active:true}
  ]'
}

val_checklist_normalize() {
  jq -ce '
    map(
      .method = (if (.method | IN("deterministic", "vlm", "manual")) then .method else "manual" end)
      | .checkId = (.checkId // null)
      | .status = (
          if .method == "deterministic" and (.checkId | IN("overflow", "clipped", "axe", "focus", "tap-target", "viewport", "pixel")) then "pending"
          else "manual" end
        )
      | .active = true
      | .supersedes = (.supersedes // [])
    )
  '
}

val_checklist_extract_prompt() {
  local prompt="$1" previous="${2:-[]}" endpoint response payload
  [ -n "$prompt" ] || { printf '%s\n' '[]'; return 0; }
  if [ -z "${VAL_API_KEY:-}" ]; then
    jq -n --arg prompt "$prompt" '[{source:"prompt",target:"UI",assert:$prompt,method:"manual",confidence:"explicit",status:"manual",active:true}]'
    return 0
  fi
  endpoint="${VAL_API_URL:-https://api.openai.com/v1/responses}"
  payload="$(jq -n --arg model "${VAL_CHECKLIST_MODEL:-gpt-5-mini}" --arg prompt "$prompt" --argjson previous "$previous" '{model:$model,input:[{role:"system",content:"Return only a JSON array of testable UI criteria. Each object: target, assert, method (deterministic|vlm|manual), confidence (explicit|assumed), optional checkId, and optional supersedes (IDs from prior criteria contradicted by the new prompt). A deterministic checkId may only be overflow, clipped, axe, focus, tap-target, viewport, or pixel. Use manual when no runnable check exists."},{role:"user",content:("Prior criteria JSON: " + ($previous|tojson) + "\nNew prompt: " + $prompt)}]}')"
  response="$(curl --silent --show-error --fail --max-time 60 \
    --header 'Content-Type: application/json' \
    --header "Authorization: Bearer $VAL_API_KEY" \
    --data "$payload" "$endpoint")" || { val_die 3 'checklist model request failed'; return 3; }
  printf '%s' "$response" | jq -ce '
    ([.output[]?.content[]? | select(.type == "output_text") | .text] | join("") | fromjson)
    | map(select((.target|type)=="string" and (.assert|type)=="string") | . + {source:"prompt"})
  ' | val_checklist_normalize || { val_die 3 'checklist model returned invalid criteria JSON'; return 3; }
}

val_checklist_assign_ids() {
  local line key checksum
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    key="$(printf '%s' "$line" | jq -r '[.target,.assert] | join("\u001f")')"
    checksum="$(printf '%s' "$key" | cksum | awk '{print $1}')"
    printf '%s' "$line" | jq -c --arg id "c-$checksum" '. + {id:$id}'
  done
}

val_checklist_update() {
  local output="$1" prompt="${2:-}" previous candidates identified temporary
  previous='[]'
  [ ! -f "$output" ] || previous="$(jq -ce 'if type == "array" then . else error("invalid checklist") end' "$output")"
  candidates="$(val_checklist_extract_prompt "$prompt" "$previous")" || return 3
  identified="$(printf '%s' "$candidates" | jq -c '.[]' | val_checklist_assign_ids | jq -sc '.')"
  temporary="$(mktemp "$(dirname "$output")/.checklist.XXXXXX")"
  jq -n --argjson defaults "$(val_checklist_defaults)" --argjson previous "$previous" --argjson incoming "$identified" '
    ([ $incoming[] | . as $new | (.supersedes // [])[] | {old:.,new:$new.id} ]) as $supersessions
    | [ ($defaults + $previous)[]
        | . as $criterion
        | ([ $supersessions[] | select(.old == $criterion.id) ][0] // null) as $match
        | if $match == null then . else . + {active:false,supersededBy:$match.new} end
      ] as $history
    | reduce ($history + $incoming)[] as $criterion ([];
      ($criterion.target + "\u001f" + $criterion.assert) as $key
      | if any(.[]; (.target + "\u001f" + .assert) == $key) then . else . + [$criterion] end)
  ' > "$temporary"
  mv "$temporary" "$output"
}
