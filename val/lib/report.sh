#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

val_report() {
  local results="$1" report="$2" manifest="$3" driver="$4" browser="$5" rounds="$6" forced_status="${7:-}"
  local pass fail manual blocked status temporary run_dir criteria_count
  pass="$(jq -r '.summary.pass' "$results")"
  fail="$(jq -r '.summary.fail' "$results")"
  manual="$(jq -r '.summary.manual' "$results")"
  blocked="$(jq -r '.summary.blocked' "$results")"
  criteria_count="$(jq '[.criteria[] | select(.active != false)] | length' "$results")"
  run_dir="$(dirname "$manifest")"
  if [ -n "$forced_status" ]; then status="$forced_status"
  elif [ "$fail" -gt 0 ] || [ "$manual" -gt 0 ] || [ "$blocked" -gt 0 ]; then status=1
  else status=0; fi

  val_require_safe_output "$(dirname "$report")"
  mkdir -p "$(dirname "$report")"
  temporary="$(mktemp "$(dirname "$report")/.report.XXXXXX")"
  {
    printf '# VALIDATION %s\n\n' "$VAL_RUN_ID"
    printf "driver=\`%s\` · browser=\`%s\` · rounds=\`%s\`\n\n" "$driver" "$browser" "$rounds"
    printf '%s criteria · %s PASS · %s FAIL · %s MANUAL · %s BLOCKED ROUTES\n\n' "$criteria_count" "$pass" "$fail" "$manual" "$blocked"
    printf '%s\n\n' '## From prompt'
    jq -r '.criteria[] | select(.active != false and .source == "prompt") | "- **\(.status | ascii_upcase)** `\(.id)` — \(.assert)"' "$results"
    printf '\n%s\n\n' '## Defaults'
    jq -r '.criteria[] | select(.active != false and .source == "default") | "- **\(.status | ascii_upcase)** `\(.id)` — \(.assert)"' "$results"
    printf '\n%s\n\n' '## Evidence findings'
    if [ "$(jq '.findings | length' "$results")" -eq 0 ]; then printf '%s\n' 'No findings.'
    else jq -r '.findings[] | "- **\(.severity) \(.status | ascii_upcase)** `\(.checkId)` — \(.detail)\n  - Route: `\(.route)` · viewport: `\(.viewport[0])x\(.viewport[1])` · theme: `\(.theme)`\n  - Shot: `\(.shot)`" + (if .file then "\n  - Location: `\(.file):\(.line // 1)`" else "" end)' "$results"; fi
    printf '\n%s\n\n' '## Manual'
    jq -r '.criteria[] | select(.active != false and .status == "manual") | "- `\(.id)` — \(.assert)"' "$results"
    if [ "$blocked" -gt 0 ]; then
      printf '\n%s\n\n' '## Blocked routes'
      jq -r '.findings[] | select(.status == "blocked") | "- `\(.route)` — \(.detail)"' "$results"
    fi
    if [ -f "$run_dir/attempts.json" ]; then
      printf '\n%s\n\n' '## Fix attempts'
      jq -r 'to_entries[] | "- `\(.key)` — \(.value) attempt(s)"' "$run_dir/attempts.json"
    fi
    printf '\n%s\n' 'Manual items remain non-green and require human review; baselines are never promoted automatically.'
  } > "$temporary"
  mv "$temporary" "$report"

  temporary="$(mktemp "$(dirname "$manifest")/.manifest.XXXXXX")"
  jq -n \
    --arg runId "$VAL_RUN_ID" --arg driver "$driver" --arg browser "$browser" \
    --arg report "$report" --arg results "$results" --argjson rounds "$rounds" --argjson exitCode "$status" \
    --slurpfile data "$results" \
    '{protocolVersion:1,runId:$runId,driver:$driver,browser:$browser,rounds:$rounds,report:$report,results:$results,exitCode:$exitCode,summary:$data[0].summary,criteria:$data[0].criteria,findings:$data[0].findings,shots:($data[0].shots // [])}' \
    > "$temporary"
  mv "$temporary" "$manifest"
  return "$status"
}
