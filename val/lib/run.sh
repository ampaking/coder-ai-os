#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

val_run_cleanup() {
  val_server_stop || true
}

val_run_infra_report() {
  local run_dir="$1" detail="$2" report manifest
  report="$run_dir/final/report.md"
  manifest="$run_dir/manifest.json"
  val_require_safe_output "$run_dir/final"
  mkdir -p "$run_dir/final"
  {
    printf '# VALIDATION %s\n\n' "$VAL_RUN_ID"
    printf '%s\n\n' '**INFRA ERROR** — exit code 3'
    printf '%s\n' "$detail"
    if [ -f "$VAL_SERVER_LOG" ]; then
      printf '\n%s\n\n```text\n' '## Server log (last 50 lines)'
      awk '{lines[NR % 50]=$0} END {start=(NR > 50 ? NR - 49 : 1); for (i=start; i<=NR; i++) print lines[i % 50]}' "$VAL_SERVER_LOG"
      printf '%s\n' '```'
    fi
  } > "$report"
  cp "$report" "$run_dir/report.md"
  jq -n --arg runId "$VAL_RUN_ID" --arg report "$report" --arg detail "$detail" '{protocolVersion:1,runId:$runId,exitCode:3,report:$report,summary:{pass:0,fail:0,manual:0,blocked:0},infraError:$detail,criteria:[],findings:[],shots:[],rounds:0}' > "$manifest"
}

val_run_shots() {
  printf '%s' "$VAL_CONFIG_JSON" | jq -c --argjson darkEnabled "${VAL_DARK_ENABLED:-true}" --arg priority "${VAL_PRIORITY_ROUTES:-}" '
    ($priority | split("\n") | map(select(length > 0))) as $priorityRoutes
    | (reduce ($priorityRoutes + .routes)[] as $route ([]; if index($route) == null then . + [$route] else . end)) as $routes
    | [ $routes[] as $route
      | .viewports[] as $viewport
      | .themes[] as $theme
      | select($theme != "dark" or $darkEnabled)
      | {route:$route,viewport:$viewport,theme:$theme}
    ][0:.maxShots]
  '
}

val_run_priority_routes() {
  local checklist="$1" route token
  printf '%s\n' "${VAL_CHANGED_ROUTES:-}"
  while IFS= read -r route; do
    [ "$route" != / ] || continue
    token="${route#/}"
    if printf '%s\n' "${VAL_ALLOWED_FILES:-}" | awk -v token="$token" 'index(tolower($0),tolower(token)) {found=1} END {exit !found}'; then printf '%s\n' "$route"; fi
  done < <(val_config_get '.routes[]')
  jq -r '.[] | select(.active != false and (.target | type == "string") and (.target | startswith("/"))) | .target' "$checklist"
}

val_run_has_dark_theme() {
  local file
  [ "$VAL_DRIVER" != remote ] || return 0
  while IFS= read -r -d '' file; do
    if awk 'BEGIN{IGNORECASE=1} /dark:|prefers-color-scheme[[:space:]]*:[[:space:]]*dark|data-theme[^>]*(dark|black)|class[^>]*dark|--[[:alnum:]_-]*dark/ {found=1; exit} END {exit(found ? 0 : 1)}' "$file"; then return 0; fi
  done < <(find "$VAL_PROJECT_DIR" \
    \( -name .git -o -name .coder-ai -o -name .ai -o -name node_modules -o -name vendor \) -prune -o \
    -type f \( -name '*.css' -o -name '*.scss' -o -name '*.html' -o -name '*.tsx' -o -name '*.jsx' -o -name '*.vue' -o -name '*.svelte' -o -name '*.twig' -o -name '*.erb' -o -name '*.php' \) -print0)
  return 1
}

val_run_capture_round() {
  local round_dir="$1" auth_state="$2" browser_result="$3"
  val_require_safe_output "$round_dir/shots"
  mkdir -p "$round_dir/shots"
  jq -n --arg url "$VAL_URL" --arg outDir "$round_dir/shots" --arg baselineDir "$VAL_STATE_DIR/baseline" \
    --arg authState "$auth_state" --arg fixtureFile "${VAL_FIXTURE_FILE:-}" --argjson shots "$(val_run_shots)" \
    '{protocolVersion:1,url:$url,outDir:$outDir,baselineDir:$baselineDir,authState:(if $authState == "" then null else $authState end),fixtureFile:(if $fixtureFile == "" then null else $fixtureFile end),settle:true,checks:["overflow","clipped","axe","focus","tap-target","viewport","pixel"],shots:$shots}' \
    | val_browser_run > "$browser_result"
}

val_run_reload_for_files() {
  local changed="$1" health_ok=true source_mounted=true action file
  local changed_files=()
  val_server_health_configured || health_ok=false
  if [ "$VAL_DRIVER" = docker ] || [ "$VAL_DRIVER" = compose ]; then val_reload_source_mounted "$VAL_CONTAINER_ID" || source_mounted=false; fi
  while IFS= read -r file; do [ -n "$file" ] && changed_files+=("$file"); done <<< "$changed"
  action="$(val_reload_decide "$VAL_DRIVER" "$source_mounted" "$health_ok" "${changed_files[@]}")"
  val_log "reload=$action"
  val_reload_apply "$action"
}

val_run_merge_judgments() {
  local results="$1" judgments="$2" temporary
  [ -f "$judgments" ] || return 0
  temporary="$(mktemp "$(dirname "$results")/.results.XXXXXX")"
  jq --slurpfile judgments "$judgments" '
    .findings |= map(
      . as $finding
      | ([ $judgments[0][]
           | select(.criterionId == $finding.criterionId and .finding == $finding.detail) ][-1] // null) as $judgment
      | if $judgment == null then . else . + {
          file: $judgment.file,
          line: $judgment.line,
          expected: $judgment.expected,
          actual: $judgment.actual
        } end
    )
  ' "$results" > "$temporary"
  mv "$temporary" "$results"
}

val_run() {
  local requested_id="${1:-}" run_dir round_dir browser_result checklist results report manifest status auth_state fixture_file=''
  local judgments attempts round=1 started_seconds="$SECONDS" blocked_reason='' changed reload_status shot
  val_require_core
  val_config_load
  if [ -n "$requested_id" ]; then
    VAL_RUN_ID="$requested_id"
  else
    VAL_RUN_ID="run-$(date '+%Y%m%d-%H%M%S')-$$"
  fi
  case "$VAL_RUN_ID" in *[!A-Za-z0-9._-]*) val_die 3 "invalid run id: $VAL_RUN_ID"; return 3 ;; esac
  VAL_LOCK_FILE="$VAL_LOCK_DIR/$VAL_RUN_ID.json"
  run_dir="$VAL_STATE_DIR/runs/$VAL_RUN_ID"
  checklist="$run_dir/checklist.json"
  report="$run_dir/final/report.md"
  manifest="$run_dir/manifest.json"
  attempts="$run_dir/attempts.json"
  val_require_safe_output "$run_dir"
  mkdir -p "$run_dir"
  VAL_LOG_FILE="$run_dir/val.log"
  export VAL_RUN_ID VAL_LOCK_FILE VAL_LOG_FILE
  val_require_safe_output "$VAL_LOG_FILE"
  : > "$VAL_LOG_FILE"
  trap val_run_cleanup EXIT
  trap 'val_run_cleanup; trap - EXIT; exit 130' INT TERM

  VAL_ALLOWED_FILES="$(val_fix_collect_allowed_files)"
  export VAL_ALLOWED_FILES
  VAL_DRIVER=''
  val_detect_driver >/dev/null
  val_log "driver=$VAL_DRIVER"
  if val_run_has_dark_theme; then VAL_DARK_ENABLED='true'; else VAL_DARK_ENABLED='false'; val_log 'dark theme dropped: no local dark class/token found'; fi
  export VAL_DARK_ENABLED
  val_server_start || { val_run_infra_report "$run_dir" 'server failed to become healthy'; return 3; }
  VAL_BROWSER_MODE="$(val_detect_browser_mode)"
  export VAL_BROWSER_MODE
  fixture_file="$(val_fixture_prepare)" || { val_run_infra_report "$run_dir" 'fixture preparation failed'; return 3; }
  VAL_FIXTURE_FILE="$fixture_file"
  export VAL_FIXTURE_FILE
  if [ -n "$fixture_file" ] && [ "$(jq -r '.authState // empty' "$fixture_file")" != "" ]; then
    auth_state="$(jq -r '.authState' "$fixture_file")"
  else
    auth_state="$(val_auth_prepare "$run_dir")" || { val_run_infra_report "$run_dir" 'authentication failed'; return 3; }
  fi
  val_checklist_update "$checklist" "${VAL_PROMPT:-}" || { val_run_infra_report "$run_dir" 'checklist extraction failed'; return 3; }
  VAL_PRIORITY_ROUTES="$(val_run_priority_routes "$checklist")"
  export VAL_PRIORITY_ROUTES
  while [ "$round" -le 10 ]; do
    round_dir="$run_dir/round-$round"
    browser_result="$round_dir/browser.json"
    results="$round_dir/results.json"
    judgments="$round_dir/judgments.json"
    val_require_safe_output "$round_dir"
    mkdir -p "$round_dir"
    val_run_capture_round "$round_dir" "$auth_state" "$browser_result" || { val_run_infra_report "$run_dir" "browser capture failed in round $round"; return 3; }
    val_verify "$browser_result" "$checklist" "$results" || { val_run_infra_report "$run_dir" "verification failed in round $round"; return 3; }
    if [ "$(jq -r '.summary.fail' "$results")" -eq 0 ]; then break; fi
    val_judge "$results" "$judgments" || { val_run_infra_report "$run_dir" "judge failed in round $round"; return 3; }
    if [ "$(jq '[.[] | select(.actionable == true)] | length' "$judgments")" -eq 0 ]; then break; fi
    if [ $((SECONDS - started_seconds)) -ge 1200 ]; then blocked_reason='20-minute wall-clock cap reached'; break; fi
    changed="$(val_fix_apply_judgments "$judgments" "$round_dir" "$attempts" || true)"
    if [ -n "$changed" ]; then
      set +e
      val_run_reload_for_files "$changed"
      reload_status=$?
      set -e
      if [ "$reload_status" -ne 0 ]; then
        [ "$reload_status" -ne 2 ] || blocked_reason='required reload refused because the server was not started by VAL'
        [ -n "$blocked_reason" ] || return 3
        break
      fi
    elif val_fix_attempts_exhausted "$judgments" "$attempts"; then
      blocked_reason='three fix attempts exhausted for each actionable finding'
      break
    fi
    round=$((round + 1))
  done
  if [ "$round" -gt 10 ]; then round=10; blocked_reason='10-round cap reached'; fi
  round_dir="$run_dir/round-$round"
  results="$round_dir/results.json"
  judgments="$round_dir/judgments.json"
  [ -f "$judgments" ] || printf '%s\n' '[]' > "$judgments"
  val_run_merge_judgments "$results" "$judgments"
  if [ -n "$blocked_reason" ]; then val_fix_write_blocked "$run_dir" "$blocked_reason" "$results" "$attempts" "$judgments"; fi
  val_require_safe_output "$run_dir/final/shots"
  mkdir -p "$run_dir/final/shots"
  for shot in "$round_dir/shots/"*.png; do [ ! -f "$shot" ] || cp "$shot" "$run_dir/final/shots/"; done
  if val_report "$results" "$report" "$manifest" "$VAL_DRIVER" "$VAL_BROWSER_MODE" "$round" "$(if [ -n "$blocked_reason" ]; then printf 2; fi)"; then status=0; else status=$?; fi
  cp "$report" "$run_dir/report.md"
  local evidence_outcome='warning'
  [ "$status" -eq 0 ] && evidence_outcome='passed'
  { [ "$status" -eq 1 ] || [ "$status" -eq 2 ]; } && evidence_outcome='failed'
  # Prefer the current name; fall back to the legacy link so an older install still records.
  local cli=""
  command -v coder-ai    >/dev/null 2>&1 && cli=coder-ai
  [ -z "$cli" ] && command -v coder-ai-os >/dev/null 2>&1 && cli=coder-ai-os
  if [ -n "$cli" ]; then
    "$cli" tasks validation-result --collector val --run-id "$VAL_RUN_ID" \
      --outcome "$evidence_outcome" >/dev/null 2>&1 || true
  fi
  val_run_cleanup
  trap - EXIT INT TERM
  printf 'VAL report: %s\n' "$report"
  return "$status"
}
