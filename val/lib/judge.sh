#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

val_judge_response_text() {
  jq -ce '[.output[]?.content[]? | select(.type == "output_text") | .text] | join("")'
}

val_judge_safe_shot_data() {
  local shot="$1" resolved_parent state_root
  [ -n "$shot" ] && [ -f "$shot" ] || return 1
  resolved_parent="$(cd "$(dirname "$shot")" && pwd -P)"
  state_root="$(cd "$VAL_STATE_DIR" && pwd -P)"
  case "$resolved_parent/${shot##*/}" in "$state_root"/*) base64 < "$shot" | tr -d '\n' ;; *) return 1 ;; esac
}

val_judge_safe_snippet() {
  local file="$1" line="$2" allowed resolved project_root start end
  [ -n "$file" ] || return 1
  val_path_is_sensitive "$file" && return 1
  allowed="$(printf '%s\n' "${VAL_ALLOWED_FILES:-}" | awk -v target="$file" '$0 == target {print; exit}')"
  [ -n "$allowed" ] || return 1
  case "$line" in *[!0-9]*|'') line=1 ;; esac
  project_root="$(cd "$VAL_PROJECT_DIR" && pwd -P)"
  [ ! -L "$VAL_PROJECT_DIR/$file" ] || return 1
  resolved="$(cd "$(dirname "$VAL_PROJECT_DIR/$file")" && pwd -P)/${file##*/}" || return 1
  case "$resolved" in "$project_root"/*) ;; *) return 1 ;; esac
  start=$((line > 15 ? line - 15 : 1))
  end=$((line + 15))
  awk -v start="$start" -v end="$end" 'NR >= start && NR <= end {printf "%d: %s\n", NR, $0}' "$resolved"
}

val_judge_allowed_context() {
  local file resolved project_root count=0
  project_root="$(cd "$VAL_PROJECT_DIR" && pwd -P)"
  while IFS= read -r file; do
    [ "$count" -lt 5 ] || break
    val_path_is_sensitive "$file" && continue
    case "$file" in
      *.tsx|*.jsx|*.ts|*.js|*.vue|*.svelte|*.css|*.scss|*.html|*.htm|*.twig|*.erb|*.php) ;;
      *) continue ;;
    esac
    resolved="$project_root/$file"
    [ -f "$resolved" ] && [ ! -L "$resolved" ] || continue
    printf '\n--- %s ---\n' "$file"
    awk 'NR <= 240 {printf "%d: %s\n", NR, $0}' "$resolved"
    count=$((count + 1))
  done <<< "${VAL_ALLOWED_FILES:-}"
}

val_judge_request_item() {
  local finding="$1" output="$2" shot snippet image_file
  shot="$(printf '%s' "$finding" | jq -r '.shot // empty')"
  image_file="$(mktemp "${TMPDIR:-/tmp}/val-judge-image.XXXXXX")"
  if val_judge_safe_shot_data "$shot" >/dev/null 2>&1; then
    { printf '%s' 'data:image/png;base64,'; base64 < "$shot" | tr -d '\n'; } > "$image_file"
  else
    : > "$image_file"
  fi
  snippet="$(val_judge_safe_snippet "$(printf '%s' "$finding" | jq -r '.file // empty')" "$(printf '%s' "$finding" | jq -r '.line // 1')" 2>/dev/null || true)"
  [ -n "$snippet" ] || snippet="$(val_judge_allowed_context)"
  jq -n --argjson finding "$finding" --rawfile image "$image_file" --arg snippet "$snippet" '
    {criterionId:$finding.criterionId,content:([{
      type:"input_text",text:("Finding JSON: " + ($finding|tojson) + "\nAllowlisted source snippet:\n" + $snippet)
    }] + (if $image == "" then [] else [{type:"input_image",image_url:$image}] end))}' > "$output"
  rm -f "$image_file"
}

val_judge() {
  local results="$1" output="$2" temporary finding item_dir item_file items_file payload_file response text endpoint index=0
  temporary="$(mktemp "$(dirname "$output")/.judgments.XXXXXX")"
  if [ -z "${VAL_API_KEY:-}" ]; then
    jq '[.findings[] | select(.status == "fail" or .checkId == "manual") | {criterionId,finding:.detail,file:(.file // null),line:(.line // null),expected:"requires review",actual:.detail,suggestedFix:null,actionable:false}]' "$results" > "$temporary"
  else
    item_dir="$(mktemp -d "$(dirname "$output")/.judge-items.XXXXXX")"
    while IFS= read -r finding; do
      index=$((index + 1))
      item_file="$item_dir/$index.json"
      val_judge_request_item "$finding" "$item_file" || { rm -rf "$item_dir"; rm -f "$temporary"; return 3; }
    done < <(jq -c '.findings[] | select(.status == "fail" or .checkId == "manual")' "$results")
    items_file="$item_dir/items.json"
    jq -s '.' "$item_dir"/[0-9]*.json > "$items_file"
    payload_file="$item_dir/payload.json"
    jq -n --arg model "${VAL_JUDGE_MODEL:-gpt-5-mini}" --slurpfile items "$items_file" '
      {model:$model,input:([{role:"system",content:[{type:"input_text",text:"Return one JSON array with exactly one object per supplied criterion. Each object must contain criterionId, finding, file, line, expected, actual, suggestedFix. suggestedFix must be a complete unified diff for the provided file with a/ and b/ paths, or null. Never use markdown fences or invent repository context."}]}] + ($items[0] | map({role:"user",content:.content})))}' > "$payload_file"
    endpoint="${VAL_API_URL:-https://api.openai.com/v1/responses}"
    response="$(curl --silent --show-error --fail --max-time 90 --header 'Content-Type: application/json' --header "Authorization: Bearer $VAL_API_KEY" --data-binary "@$payload_file" "$endpoint")" || { rm -rf "$item_dir"; rm -f "$temporary"; val_die 3 'judge model request failed'; return 3; }
    text="$(printf '%s' "$response" | val_judge_response_text)" || { rm -rf "$item_dir"; rm -f "$temporary"; val_die 3 'judge response contained no output text'; return 3; }
    printf '%s' "$text" | jq -ce --argjson expected "$index" --slurpfile requested "$items_file" '
      fromjson
      | select(type == "array" and length == $expected)
      | map(select((.criterionId|type)=="string" and (.finding|type)=="string" and (.expected|type)=="string" and (.actual|type)=="string")
        | select((.file == null or (.file|type)=="string") and (.line == null or (.line|type)=="number") and (.suggestedFix == null or (.suggestedFix|type)=="string"))
        | . + {actionable:(.file != null and .suggestedFix != null)})
      | select(length == $expected)
      | select((map(.criterionId) | sort) == ($requested[0] | map(.criterionId) | sort))
    ' > "$temporary" || { rm -rf "$item_dir"; rm -f "$temporary"; val_die 3 'judge model returned invalid repair objects'; return 3; }
    rm -rf "$item_dir"
  fi
  mv "$temporary" "$output"
}
