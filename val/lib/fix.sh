#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

val_fix_collect_allowed_files() {
  local base="${VAL_BASE_REF:-}" file
  if [ "$(git -C "$VAL_PROJECT_DIR" rev-parse --is-inside-work-tree 2>/dev/null || true)" != true ]; then
    return 0
  fi
  {
    if [ -n "$base" ]; then git -C "$VAL_PROJECT_DIR" diff --name-only --diff-filter=ACMR "$base"...HEAD; fi
    git -C "$VAL_PROJECT_DIR" diff --name-only --diff-filter=ACMR
    git -C "$VAL_PROJECT_DIR" diff --cached --name-only --diff-filter=ACMR
    git -C "$VAL_PROJECT_DIR" ls-files --others --exclude-standard
  } | awk '!seen[$0]++' | while IFS= read -r file; do
    [ -n "$file" ] || continue
    case "$file" in .coder-ai/*|.git/*) continue ;; esac
    val_path_is_sensitive "$file" && continue
    [ ! -L "$VAL_PROJECT_DIR/$file" ] || continue
    printf '%s\n' "$file"
  done
}

val_fix_file_allowed() {
  local target="$1"
  printf '%s\n' "${VAL_ALLOWED_FILES:-}" | awk -v target="$target" '$0 == target {found=1} END {exit !found}'
}

val_fix_patch_paths() {
  awk '
    /^(---|\+\+\+) / {
      path=$2
      if (path == "/dev/null") next
      sub(/^[ab]\//, "", path)
      if (!seen[path]++) print path
    }
  '
}

val_fix_validate_patch() {
  local patch_file="$1" path count=0
  grep -q '^--- ' "$patch_file" && grep -q '^+++ ' "$patch_file" || return 1
  if grep -Eq '^(GIT binary patch|```|rename (from|to) |copy (from|to) |(old|new) mode |new file mode 120000|deleted file mode 120000)' "$patch_file"; then return 1; fi
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    case "$path" in /*|../*|*/../*) return 1 ;; esac
    val_path_is_sensitive "$path" && return 1
    val_fix_file_allowed "$path" || return 1
    [ ! -L "$VAL_PROJECT_DIR/$path" ] || return 1
    count=$((count + 1))
  done < <(val_fix_patch_paths < "$patch_file")
  [ "$count" -gt 0 ] || return 1
  git -C "$VAL_PROJECT_DIR" apply --no-index --check --whitespace=error-all "$patch_file" >/dev/null 2>&1
}

val_fix_attempt_count() {
  local ledger="$1" criterion="$2"
  [ -f "$ledger" ] || { printf '%s\n' 0; return; }
  jq -r --arg criterion "$criterion" '.[$criterion] // 0' "$ledger"
}

val_fix_record_attempt() {
  local ledger="$1" criterion="$2" temporary
  temporary="$(mktemp "$(dirname "$ledger")/.attempts.XXXXXX")"
  if [ -f "$ledger" ]; then
    jq --arg criterion "$criterion" '.[$criterion] = ((.[$criterion] // 0) + 1)' "$ledger" > "$temporary"
  else
    jq -n --arg criterion "$criterion" '{($criterion):1}' > "$temporary"
  fi
  mv "$temporary" "$ledger"
}

val_fix_apply_judgment() {
  local judgment="$1" round_dir="$2" ledger="$3" criterion file suggested patch_file attempts
  criterion="$(printf '%s' "$judgment" | jq -r '.criterionId')"
  file="$(printf '%s' "$judgment" | jq -r '.file // empty')"
  suggested="$(printf '%s' "$judgment" | jq -r '.suggestedFix // empty')"
  [ "$(printf '%s' "$judgment" | jq -r '.actionable')" = true ] && [ -n "$file" ] && [ -n "$suggested" ] || return 1
  attempts="$(val_fix_attempt_count "$ledger" "$criterion")"
  [ "$attempts" -lt 3 ] || return 2
  val_fix_record_attempt "$ledger" "$criterion"
  val_fix_file_allowed "$file" || { val_log "rejected fix outside task diff: $file"; return 1; }
  patch_file="$(mktemp "$round_dir/.candidate.XXXXXX.patch")"
  printf '%s\n' "$suggested" > "$patch_file"
  if ! val_fix_validate_patch "$patch_file"; then
    rm -f "$patch_file"
    val_log "rejected invalid patch for $criterion"
    return 1
  fi
  git -C "$VAL_PROJECT_DIR" apply --no-index --whitespace=error-all "$patch_file"
  if [ -f "$round_dir/patch.diff" ]; then printf '\n' >> "$round_dir/patch.diff"; fi
  cat "$patch_file" >> "$round_dir/patch.diff"
  rm -f "$patch_file"
  printf '%s\n' "$file"
}

val_fix_apply_judgments() {
  local judgments="$1" round_dir="$2" ledger="$3" judgment applied=0
  while IFS= read -r judgment; do
    if val_fix_apply_judgment "$judgment" "$round_dir" "$ledger"; then applied=1; fi
  done < <(jq -c '.[] | select(.actionable == true)' "$judgments")
  [ "$applied" -eq 1 ]
}

val_fix_attempts_exhausted() {
  local judgments="$1" ledger="$2" criterion found=0
  while IFS= read -r criterion; do
    found=1
    [ "$(val_fix_attempt_count "$ledger" "$criterion")" -ge 3 ] || return 1
  done < <(jq -r '.[] | select(.actionable == true) | .criterionId' "$judgments")
  [ "$found" -eq 1 ]
}

val_fix_write_blocked() {
  local run_dir="$1" reason="$2" results="$3" attempts="$4" judgments="$5"
  {
    printf '# VAL blocked — %s\n\n' "$VAL_RUN_ID"
    printf 'Reason: %s\n\n' "$reason"
    printf '%s\n\n' '## Remaining findings'
    jq -r '.findings[] | select(.status == "fail") | "- [\(.criterionId)] \(.detail) — shot: \(.shot)"' "$results"
    printf '\n%s\n\n' '## Attempts'
    if [ -f "$attempts" ]; then jq -r 'to_entries[] | "- \(.key): \(.value)"' "$attempts"; else printf '%s\n' '- none applied'; fi
    printf '\n%s\n\n' '## Last judgments'
    jq -r '.[] | "- [\(.criterionId)] \(.finding) — actionable=\(.actionable) file=\(.file // "unknown")"' "$judgments"
  } > "$run_dir/BLOCKED.md"
}
