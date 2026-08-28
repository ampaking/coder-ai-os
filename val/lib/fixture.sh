#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

val_fixture_prepare() {
  local status adapter output result auth_state auth_parent canonical_state temporary
  status="$(val_config_get '.fixture.status // "disabled"')"
  [ "$status" != disabled ] || return 0
  adapter="$(val_config_get '.fixture.adapter')"
  case "$adapter" in .coder-ai-os-script/val-fixtures/*.mjs) ;; *) val_die 3 'fixture adapter must be generated under .coder-ai-os-script/val-fixtures'; return 3 ;; esac
  adapter="$VAL_PROJECT_DIR/$adapter"
  [ -f "$adapter" ] || { val_die 3 "fixture adapter is missing: ${adapter#"$VAL_PROJECT_DIR/"}"; return 3; }
  output="$VAL_STATE_DIR/fixture.json"
  result="$(node "$adapter" --url "$VAL_URL" --state "$VAL_STATE_DIR" --output "$output" 2>&1)" || {
    val_log "fixture adapter blocked: $result"
    val_die 3 "fixture adapter is incomplete for $(val_config_get '.generated.appId // "project"'): $result"
    return 3
  }
  [ -f "$output" ] || { val_die 3 'fixture adapter did not write fixture.json'; return 3; }
  jq -e '
    .version == 1
    and (.authState == null or (.authState | type == "string" and length > 0))
    and ((.routes // []) | type == "array")
    and all((.routes // [])[];
      (.url | type == "string" and test("^https?://"))
      and (.method == null or (.method | type == "string" and test("^[A-Z]+$")))
      and (.status == null or (.status | type == "number" and floor == . and . >= 100 and . <= 599))
      and (.contentType == null or (.contentType | type == "string" and length > 0))
    )
  ' "$output" >/dev/null \
    || { val_die 3 'fixture adapter wrote an invalid fixture contract'; return 3; }
  auth_state="$(jq -r '.authState // empty' "$output")"
  if [ -n "$auth_state" ]; then
    case "$auth_state" in /*) ;; *) auth_state="$VAL_STATE_DIR/$auth_state" ;; esac
    [ -f "$auth_state" ] && [ ! -L "$auth_state" ] \
      || { val_die 3 'fixture authState must be a regular non-symlink file inside VAL_STATE_DIR'; return 3; }
    auth_parent="$(cd -P "$(dirname "$auth_state")" >/dev/null 2>&1 && pwd)" \
      || { val_die 3 'fixture authState parent cannot be resolved'; return 3; }
    canonical_state="$(cd -P "$VAL_STATE_DIR" >/dev/null 2>&1 && pwd)"
    case "$auth_parent/$(basename "$auth_state")" in "$canonical_state"/*) ;; *) val_die 3 'fixture authState is outside VAL_STATE_DIR'; return 3 ;; esac
    auth_state="$auth_parent/$(basename "$auth_state")"
    chmod 600 "$auth_state"
    temporary="$(mktemp "$VAL_STATE_DIR/.fixture.XXXXXX")"
    jq --arg authState "$auth_state" '.authState = $authState' "$output" > "$temporary"
    mv "$temporary" "$output"
  fi
  chmod 600 "$output"
  printf '%s\n' "$output"
}
