#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

VAL_BROWSER_VERSION='1.62.1'
VAL_BROWSER_CACHE_DIR="${VAL_BROWSER_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/coder-ai-os/val/browser-$VAL_BROWSER_VERSION}"
VAL_BROWSER_IMAGE="${VAL_BROWSER_IMAGE:-coder-ai-os-val-browser:$VAL_BROWSER_VERSION}"
PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$VAL_BROWSER_CACHE_DIR/browsers}"
export VAL_BROWSER_VERSION VAL_BROWSER_CACHE_DIR VAL_BROWSER_IMAGE PLAYWRIGHT_BROWSERS_PATH

val_browser_validate_request() {
  jq -e '
    .protocolVersion == 1
    and (.url | type == "string" and length > 0)
    and (.outDir | type == "string" and length > 0)
    and ((.operation // "capture") as $operation
      | if $operation == "capture" then (.shots | type == "array" and length > 0)
        elif $operation == "auth-check" then (.authState | type == "string" and length > 0) and (.successCheck | type == "string" and length > 0)
        elif $operation == "auth-login" then (.authState | type == "string" and length > 0) and (.successCheck | type == "string" and length > 0) and (.steps | type == "array" and length > 0)
        elif $operation == "auth-manual" then (.authState | type == "string" and length > 0) and (.successCheck | type == "string" and length > 0)
        else false end)
  ' >/dev/null
}

val_browser_prepare_local() {
  local source_hash installed_hash
  val_command_ready npm || { val_die 3 'local browser mode requires npm'; return 3; }
  source_hash="$(cksum "$VAL_SOURCE_DIR/browser/package.json" "$VAL_SOURCE_DIR/browser/package-lock.json" "$VAL_SOURCE_DIR/browser/run.mjs" | cksum | awk '{print $1}')"
  installed_hash="$(awk 'NR == 1 {print; exit}' "$VAL_BROWSER_CACHE_DIR/source.cksum" 2>/dev/null || true)"
  if [ "$source_hash" != "$installed_hash" ]; then
    mkdir -p "$VAL_BROWSER_CACHE_DIR"
    cp "$VAL_SOURCE_DIR/browser/package.json" "$VAL_SOURCE_DIR/browser/package-lock.json" "$VAL_SOURCE_DIR/browser/run.mjs" "$VAL_BROWSER_CACHE_DIR/"
    ( cd "$VAL_BROWSER_CACHE_DIR" && npm ci --omit=dev --ignore-scripts ) >&2
    ( cd "$VAL_BROWSER_CACHE_DIR" && npm exec -- playwright install chromium ) >&2
    printf '%s\n' "$source_hash" > "$VAL_BROWSER_CACHE_DIR/source.cksum"
  fi
}

val_browser_run_local() {
  val_browser_prepare_local
  node "$VAL_BROWSER_CACHE_DIR/run.mjs"
}

val_browser_prepare_docker() {
  local source_hash installed_hash
  val_command_ready docker || { val_die 3 'Docker browser mode requires Docker'; return 3; }
  source_hash="$(cksum "$VAL_SOURCE_DIR/browser/Dockerfile" "$VAL_SOURCE_DIR/browser/package.json" "$VAL_SOURCE_DIR/browser/package-lock.json" "$VAL_SOURCE_DIR/browser/run.mjs" | cksum | awk '{print $1}')"
  installed_hash="$(docker image inspect --format '{{index .Config.Labels "coder-ai-os.val.source"}}' "$VAL_BROWSER_IMAGE" 2>/dev/null || true)"
  if [ "$source_hash" != "$installed_hash" ]; then
    docker build --label "coder-ai-os.val.source=$source_hash" --tag "$VAL_BROWSER_IMAGE" "$VAL_SOURCE_DIR/browser" >&2 || { val_die 3 'failed to build the pinned VAL browser image'; return 3; }
  fi
}

val_browser_remap_docker_request() {
  local host_mode="$1"
  jq -ce --arg state "$VAL_STATE_DIR" --arg hostMode "$host_mode" '
    def state_path:
      if . == null then null
      elif . == $state or startswith($state + "/") then "/val-state" + .[($state | length):]
      else error("browser file path is outside VAL_STATE_DIR") end;
    .outDir |= state_path
    | .baselineDir |= state_path
    | .authState |= state_path
    | .fixtureFile |= state_path
    | if $hostMode == "desktop" then
        .url |= sub("^http://(127\\.0\\.0\\.1|localhost)(?=[:/])"; "http://host.docker.internal")
        | .url |= sub("^https://(127\\.0\\.0\\.1|localhost)(?=[:/])"; "https://host.docker.internal")
      else . end
  '
}

val_browser_run_docker() {
  local host_mode request
  local docker_args=(run --rm --interactive --ipc host --user "$(id -u):$(id -g)" --env HOME=/tmp --volume "$VAL_STATE_DIR:/val-state")
  val_browser_prepare_docker
  host_mode="${VAL_DOCKER_HOST_MODE:-auto}"
  if [ "$host_mode" = auto ]; then
    case "$(uname -s)" in Linux) host_mode='linux' ;; *) host_mode='desktop' ;; esac
  fi
  case "$host_mode" in
    linux) docker_args+=(--network host) ;;
    desktop) ;;
    *) val_die 3 "invalid VAL_DOCKER_HOST_MODE: $host_mode"; return 3 ;;
  esac
  request="$(val_browser_remap_docker_request "$host_mode")" || { val_die 3 'Docker browser request contains an unsafe path'; return 3; }
  printf '%s\n' "$request" | docker "${docker_args[@]}" "$VAL_BROWSER_IMAGE"
}

val_browser_run() {
  local request mode
  request="$(jq -c '.')" || { val_die 3 'browser request is not valid JSON'; return 3; }
  printf '%s' "$request" | val_browser_validate_request || { val_die 3 'browser request does not match protocolVersion 1'; return 3; }
  [ -n "$VAL_CONFIG_JSON" ] || val_config_load
  mode="$(val_detect_browser_mode)"
  case "$mode" in
    local) printf '%s\n' "$request" | val_browser_run_local || { val_die 3 'local browser runtime failed'; return 3; } ;;
    docker) printf '%s\n' "$request" | val_browser_run_docker || { val_die 3 'Docker browser runtime failed'; return 3; } ;;
  esac
}
