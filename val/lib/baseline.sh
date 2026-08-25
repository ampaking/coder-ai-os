#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

val_baseline_promote() {
  local run_id="$1" run_dir shots_dir baseline_dir count=0 file temporary manifest_tmp
  case "$run_id" in *[!A-Za-z0-9._-]*|'') val_die 3 "invalid baseline run id: $run_id"; return 3 ;; esac
  run_dir="$VAL_STATE_DIR/runs/$run_id"
  shots_dir="$run_dir/final/shots"
  [ -d "$shots_dir" ] || shots_dir="$run_dir/round-1/shots"
  baseline_dir="$VAL_STATE_DIR/baseline"
  [ -f "$run_dir/manifest.json" ] || { val_die 3 "run manifest missing: $run_id"; return 3; }
  jq -e '.protocolVersion == 1' "$run_dir/manifest.json" >/dev/null 2>&1 || { val_die 3 "run manifest invalid: $run_id"; return 3; }
  [ -d "$shots_dir" ] || { val_die 3 "run screenshots missing: $run_id"; return 3; }
  val_require_safe_output "$baseline_dir"
  mkdir -p "$baseline_dir"
  while IFS= read -r file; do
    [ ! -L "$file" ] || { val_die 3 "refusing symlink screenshot: $file"; return 3; }
    temporary="$(mktemp "$baseline_dir/.baseline.XXXXXX")"
    cp "$file" "$temporary"
    mv "$temporary" "$baseline_dir/$(basename "$file")"
    count=$((count + 1))
  done < <(find "$shots_dir" -maxdepth 1 -type f -name '*.png' -print | sort)
  [ "$count" -gt 0 ] || { val_die 3 "run has no screenshots: $run_id"; return 3; }
  manifest_tmp="$(mktemp "$baseline_dir/.manifest.XXXXXX")"
  jq -n --arg runId "$run_id" --argjson promotedAt "$(date +%s)" --argjson shots "$count" \
    '{protocolVersion:1,sourceRun:$runId,promotedAtEpoch:$promotedAt,shots:$shots}' > "$manifest_tmp"
  mv "$manifest_tmp" "$baseline_dir/manifest.json"
  printf 'VAL baseline: promoted %s screenshot(s) from %s\n' "$count" "$run_id"
}
