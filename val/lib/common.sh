#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

if [ "${VAL_DEBUG:-0}" = 1 ]; then
  set -x
fi

VAL_PROJECT_DIR="${VAL_PROJECT_DIR:-$(pwd -P)}"
VAL_STATE_DIR="${VAL_STATE_DIR:-$VAL_PROJECT_DIR/.coder-ai/val}"
VAL_CONFIG_FILE="${VAL_CONFIG_FILE:-$VAL_STATE_DIR/config.json}"
export VAL_PROJECT_DIR VAL_STATE_DIR VAL_CONFIG_FILE

val_die() {
  local code="$1"
  shift
  printf 'VAL: %s\n' "$*" >&2
  return "$code"
}

val_log() {
  local message="$*"
  if [ -n "${VAL_LOG_FILE:-}" ]; then
    printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$message" >> "$VAL_LOG_FILE"
  fi
  if [ "${VAL_DEBUG:-0}" = 1 ]; then
    printf 'VAL DEBUG: %s\n' "$message" >&2
  fi
}

val_path_is_sensitive() {
  local path lower base
  path="$1"
  lower="$(printf '%s' "$path" | tr '[:upper:]' '[:lower:]')"
  base="${lower##*/}"
  case "$lower" in
    .env|.env.*|*/.env|*/.env.*|*.pem|*.key|*.p12|*.pfx|*.jks|*.keystore|*credentials*|*secret*|*token*|*service-account*.json|*service_account*.json) return 0 ;;
  esac
  case "$base" in id_rsa|id_dsa|id_ecdsa|id_ed25519) return 0 ;; esac
  return 1
}

val_require_safe_output() {
  local path="$1" current relative
  case "$path" in "$VAL_STATE_DIR"|"$VAL_STATE_DIR"/*) ;; *) val_die 3 "output escapes VAL state: $path"; return 3 ;; esac
  [ ! -L "$VAL_STATE_DIR" ] || { val_die 3 "refusing symlinked VAL path: $VAL_STATE_DIR"; return 3; }
  [ "$path" != "$VAL_STATE_DIR" ] || return 0
  relative="${path#"$VAL_STATE_DIR"/}"
  current="$VAL_STATE_DIR"
  while [ -n "$relative" ]; do
    current="$current/${relative%%/*}"
    [ ! -L "$current" ] || { val_die 3 "refusing symlinked VAL output: $current"; return 3; }
    [ "$relative" = "${relative#*/}" ] && break
    relative="${relative#*/}"
  done
}

val_bash_ready() {
  [ -n "${BASH_VERSINFO[0]:-}" ] && [ "${BASH_VERSINFO[0]}" -ge 4 ]
}

val_command_ready() {
  command -v "$1" >/dev/null 2>&1
}

val_require_safe_state() {
  local path
  for path in \
    "$VAL_PROJECT_DIR/.coder-ai" \
    "$VAL_STATE_DIR" \
    "$VAL_CONFIG_FILE" \
    "$VAL_STATE_DIR/locks" \
    "$VAL_STATE_DIR/runs" \
    "$VAL_STATE_DIR/baseline" \
    "$VAL_STATE_DIR/browser-cache"; do
    [ ! -L "$path" ] || { val_die 3 "refusing symlinked VAL path: $path"; return 3; }
  done
}

val_require_core() {
  local missing=()
  local command

  val_bash_ready || missing+=("bash>=4")
  for command in jq curl awk git ps; do
    val_command_ready "$command" || missing+=("$command")
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    if ! val_bash_ready; then
      printf '%s\n' 'VAL requires Bash 4+. On macOS: brew install bash' >&2
    fi
    val_die 3 "missing required dependencies: ${missing[*]}"
  fi
  val_require_safe_state
}

val_doctor_row() {
  local name="$1" status="$2" detail="$3"
  printf '%-18s %-8s %s\n' "$name" "$status" "$detail"
}

val_doctor() {
  local failed=0
  local command path

  printf '%-18s %-8s %s\n' 'CAPABILITY' 'STATUS' 'DETAIL'
  if val_bash_ready; then
    val_doctor_row 'bash>=4' 'ready' "${BASH_VERSION}"
  else
    val_doctor_row 'bash>=4' 'missing' 'brew install bash (macOS)'
    failed=1
  fi

  for command in jq curl awk git ps; do
    path="$(command -v "$command" 2>/dev/null || true)"
    if [ -n "$path" ]; then
      val_doctor_row "$command" 'ready' "$path"
    else
      val_doctor_row "$command" 'missing' 'required'
      failed=1
    fi
  done

  if val_require_safe_state >/dev/null 2>&1; then
    val_doctor_row 'state-path' 'ready' "$VAL_STATE_DIR"
  else
    val_doctor_row 'state-path' 'unsafe' 'refusing symlinked VAL path'
    failed=1
  fi

  if val_command_ready docker; then
    val_doctor_row 'browser:docker' 'available' 'daemon verified when capture starts'
  elif val_command_ready npx; then
    val_doctor_row 'browser:local' 'ready' 'isolated npx cache runtime'
  else
    val_doctor_row 'browser' 'missing' 'Docker or npx is required for capture'
    failed=1
  fi

  if [ -f "$VAL_CONFIG_FILE" ]; then
    if val_command_ready jq && val_config_load "$VAL_CONFIG_FILE" >/dev/null 2>&1; then
      val_doctor_row 'config' 'ready' "$VAL_CONFIG_FILE"
    else
      val_doctor_row 'config' 'invalid' "$VAL_CONFIG_FILE"
      failed=1
    fi
  else
    val_doctor_row 'config' 'optional' "$VAL_CONFIG_FILE (defaults apply)"
  fi

  [ "$failed" -eq 0 ] || return 3
}
