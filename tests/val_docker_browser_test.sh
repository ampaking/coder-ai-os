#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

DOCKER_ARGS="$TMP/docker.args"
DOCKER_STDIN="$TMP/docker.stdin"
docker() {
  if [ "${1:-}" = image ] && [ "${2:-}" = inspect ]; then return 0; fi
  if [ "${1:-}" = info ]; then return 0; fi
  if [ "${1:-}" = build ]; then printf '%s\n' "$@" > "$TMP/docker.build.args"; return 0; fi
  printf '%s\n' "$@" > "$DOCKER_ARGS"
  tee "$DOCKER_STDIN"
}

VAL_PROJECT_DIR="$TMP/project with spaces"
VAL_STATE_DIR="$VAL_PROJECT_DIR/.coder-ai/val"
VAL_CONFIG_FILE="$VAL_STATE_DIR/config.json"
VAL_SOURCE_DIR="$ROOT/val"
VAL_BROWSER_IMAGE='fixture-browser:1'
export VAL_PROJECT_DIR VAL_STATE_DIR VAL_CONFIG_FILE VAL_SOURCE_DIR VAL_BROWSER_IMAGE
mkdir -p "$VAL_STATE_DIR/runs/test/shots" "$VAL_STATE_DIR/baseline"
cp "$ROOT/val/templates/val.config.json" "$VAL_CONFIG_FILE"

# shellcheck source=val/lib/common.sh
source "$ROOT/val/lib/common.sh"
# shellcheck source=val/lib/config.sh
source "$ROOT/val/lib/config.sh"
# shellcheck source=val/lib/detect.sh
source "$ROOT/val/lib/detect.sh"
# shellcheck source=val/lib/browser.sh
source "$ROOT/val/lib/browser.sh"
val_config_load
[ "$(val_detect_browser_mode)" = docker ] || fail 'auto browser mode did not prefer an available Docker daemon'

request="$(jq -n --arg outDir "$VAL_STATE_DIR/runs/test/shots" --arg baselineDir "$VAL_STATE_DIR/baseline" '{protocolVersion:1,url:"http://127.0.0.1:4100",outDir:$outDir,baselineDir:$baselineDir,authState:null,shots:[{route:"/",viewport:[375,812],theme:"light"}]}')"
VAL_DOCKER_HOST_MODE='desktop'
export VAL_DOCKER_HOST_MODE
result="$(printf '%s\n' "$request" | val_browser_run_docker)"
[ -f "$TMP/docker.build.args" ] || fail 'unlabeled stale browser image was not rebuilt'
grep -q -- 'coder-ai-os.val.source=' "$TMP/docker.build.args" || fail 'browser image build omitted its source hash label'
printf '%s' "$result" | jq -e '.url == "http://host.docker.internal:4100" and (.outDir | startswith("/val-state/")) and (.baselineDir | startswith("/val-state/"))' >/dev/null || fail 'Docker Desktop request was not remapped'
grep -q -- "$VAL_STATE_DIR:/val-state" "$DOCKER_ARGS" || fail 'VAL state mount missing'
if grep -q -- '--network' "$DOCKER_ARGS"; then fail 'Docker Desktop incorrectly used host networking'; fi

VAL_DOCKER_HOST_MODE='linux'
printf '%s\n' "$request" | val_browser_run_docker >/dev/null
grep -qFx -- '--network' "$DOCKER_ARGS" || fail 'Linux browser did not request host networking'
grep -qFx -- 'host' "$DOCKER_ARGS" || fail 'Linux host network value missing'
jq -e '.url == "http://127.0.0.1:4100"' "$DOCKER_STDIN" >/dev/null || fail 'Linux URL was unexpectedly rewritten'

unsafe="$(printf '%s' "$request" | jq '.outDir="/tmp/outside"')"
set +e
printf '%s\n' "$unsafe" | val_browser_run_docker >/dev/null 2>&1
status=$?
set -e
[ "$status" -eq 3 ] || fail 'unsafe Docker path was accepted'

printf '%s\n' 'PASS: VAL Docker browser adapter'
