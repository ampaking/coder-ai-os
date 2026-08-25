#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

val_auth_state_path() {
  local configured parent filename resolved_parent state_root
  configured="$(val_config_get '.auth.storageState')"
  case "$configured" in
    /*) ;;
    *) configured="$VAL_PROJECT_DIR/$configured" ;;
  esac
  case "$configured" in
    "$VAL_STATE_DIR"/*) ;;
    *) val_die 3 'auth.storageState must be inside .coder-ai/val'; return 3 ;;
  esac
  case "/$configured/" in
    */../*|*/./*) val_die 3 'auth.storageState must not contain path traversal'; return 3 ;;
  esac
  parent="${configured%/*}"
  filename="${configured##*/}"
  [ -n "$filename" ] || { val_die 3 'auth.storageState must name a file'; return 3; }
  val_require_safe_output "$parent"
  mkdir -p "$parent"
  resolved_parent="$(cd "$parent" && pwd -P)"
  state_root="$(cd "$VAL_STATE_DIR" && pwd -P)"
  case "$resolved_parent/$filename" in
    "$state_root"/*) printf '%s\n' "$resolved_parent/$filename" ;;
    *) val_die 3 'auth.storageState must be inside .coder-ai/val'; return 3 ;;
  esac
}

val_auth_expanded_steps() {
  printf '%s' "$VAL_CONFIG_JSON" | jq -ce '
    .auth.steps | map(
      if has("fill") then
        .value as $placeholder
        | ($placeholder | capture("^\\$\\{(?<name>[A-Za-z_][A-Za-z0-9_]*)\\}$").name) as $name
        | if env[$name] == null then error("missing authentication environment variable: " + $name)
          else .value = env[$name] end
      else . end
    )
  '
}

val_auth_browser_request() {
  local operation="$1" state="$2" evidence="$3" steps="${4:-[]}"
  jq -n \
    --arg operation "$operation" \
    --arg url "${VAL_URL%/}$(val_config_get '.auth.loginUrl')" \
    --arg outDir "$evidence" \
    --arg authState "$state" \
    --arg successCheck "$(val_config_get '.auth.successCheck')" \
    --argjson steps "$steps" \
    --argjson allowedOrigins "$(val_config_get '.auth.allowedOrigins // []')" \
    '{protocolVersion:1,operation:$operation,url:$url,outDir:$outDir,authState:$authState,successCheck:$successCheck,steps:$steps,allowedOrigins:$allowedOrigins}'
}

val_auth_prepare() {
  local run_dir="$1" state evidence result steps attempt status
  if [ "$(val_config_get '.auth == null')" = true ]; then
    printf '%s\n' ''
    return 0
  fi
  state="$(val_auth_state_path)" || return 3
  evidence="$run_dir/auth"
  val_require_safe_output "$evidence"
  mkdir -p "$evidence"
  result="$(mktemp "$run_dir/.auth-result.XXXXXX")"
  if [ -f "$state" ]; then
    chmod 600 "$state"
    if val_auth_browser_request auth-check "$state" "$evidence" | val_browser_run > "$result" \
      && [ "$(jq -r '.status // empty' "$result")" = valid ]; then
      rm -f "$result"
      printf '%s\n' "$state"
      return 0
    fi
  fi
  steps="$(val_auth_expanded_steps)" || { rm -f "$result"; val_die 3 'authentication environment is incomplete'; return 3; }
  for attempt in 1 2; do
    if val_auth_browser_request auth-login "$state" "$evidence/attempt-$attempt" "$steps" | val_browser_run > "$result"; then
      status="$(jq -r '.status // empty' "$result")"
      if [ "$status" = authenticated ]; then
        chmod 600 "$state"
        rm -f "$result"
        printf '%s\n' "$state"
        return 0
      fi
    fi
    val_log "authentication attempt $attempt failed; evidence=$evidence/attempt-$attempt"
  done
  cp "$result" "$evidence/failure.json"
  rm -f "$result"
  val_die 3 "authentication failed after 2 attempts; evidence: $evidence"
  return 3
}

val_auth_manual() {
  local run_dir="$1" state evidence result
  [ "$(val_config_get '.auth == null')" = false ] || { val_die 3 'auth must be configured with loginUrl, storageState, and successCheck'; return 3; }
  state="$(val_auth_state_path)" || return 3
  evidence="$run_dir/auth/manual"
  val_require_safe_output "$evidence"
  mkdir -p "$evidence"
  result="$(mktemp "$run_dir/.manual-auth.XXXXXX")"
  if ! val_auth_browser_request auth-manual "$state" "$evidence" | val_browser_run > "$result" \
    || [ "$(jq -r '.status // empty' "$result")" != authenticated ]; then
    cp "$result" "$evidence/failure.json"
    rm -f "$result"
    val_die 3 "manual authentication failed; evidence: $evidence"
    return 3
  fi
  chmod 600 "$state"
  rm -f "$result"
  printf '%s\n' "$state"
}
