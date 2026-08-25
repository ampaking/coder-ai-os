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

project="$TMP/end to end app"
mkdir -p "$project/.coder-ai/val"
printf '%s\n' '<main>fixture</main>' > "$project/index.html"
serve="$ROOT/tests/fixtures/val_http_server.py"
sed "s|__SERVE__|$serve|" "$ROOT/tests/fixtures/val.node.config.json" \
  | jq '.browserMode="local" | .routes=["/ready"] | .viewports=[[375,812]] | .themes=["light","dark"]' \
  > "$project/.coder-ai/val/config.json"

set +e
output="$(cd "$project" && VAL_BROWSER_CACHE_DIR=/tmp/coder-ai-os-val-browser-test-1.62.1 "$ROOT/val/val" run --prompt 'stack cards below 768px' --task e2e-fixture 2>&1)"
status=$?
set -e
[ "$status" -eq 1 ] || { printf '%s\n' "$output" >&2; fail "first run returned $status instead of non-green 1"; }
run_dir="$project/.coder-ai/val/runs/e2e-fixture"
[ -f "$run_dir/report.md" ] || fail 'report.md missing'
[ -f "$run_dir/manifest.json" ] || fail 'manifest.json missing'
[ "$(jq -r '.exitCode' "$run_dir/manifest.json")" -eq 1 ] || fail 'manifest exit code mismatch'
jq -e '.summary.manual >= 1' "$run_dir/manifest.json" >/dev/null || fail 'missing baseline was not reported manual'
jq -e 'any(.criteria[]; .source == "prompt" and .assert == "stack cards below 768px")' "$run_dir/manifest.json" >/dev/null || fail 'CLI prompt did not reach checklist extraction'
jq -e '.shots | length == 1 and .[0].path != null' "$run_dir/manifest.json" >/dev/null || fail 'manifest did not retain the complete shot inventory'
jq -e 'all(.findings[] | select(.status == "manual"); .shot | length > 0)' "$run_dir/manifest.json" >/dev/null || fail 'manual criteria lack screenshot evidence'
[ "$(jq '[.shots[] | select(.theme == "dark")] | length' "$run_dir/round-1/browser.json")" -eq 0 ] || fail 'dark shots were not dropped for a project without a dark token'
if find "$project/.coder-ai/val/locks" -type f -name '*.json' -print -quit | grep -q .; then fail 'run left an ownership lock'; fi

(cd "$project" && "$ROOT/val/val" baseline promote --run e2e-fixture >/dev/null)
[ "$(find "$project/.coder-ai/val/baseline" -maxdepth 1 -name 'ready--*__375x812__light.png' | awk 'END {print NR}')" -eq 1 ] || fail 'baseline screenshot was not promoted'
[ "$(jq -r '.sourceRun' "$project/.coder-ai/val/baseline/manifest.json")" = e2e-fixture ] || fail 'baseline manifest source mismatch'

set +e
output="$(cd "$project" && VAL_BROWSER_CACHE_DIR=/tmp/coder-ai-os-val-browser-test-1.62.1 "$ROOT/val/val" run --task e2e-green 2>&1)"
status=$?
set -e
# The HTTP fixture intentionally lacks accessibility metadata, but its pixel baseline must pass.
[ "$status" -eq 1 ] || { printf '%s\n' "$output" >&2; fail "second run returned unexpected status $status"; }
jq -e 'any(.shots[0].checks[]; .id == "pixel" and .status == "pass")' "$project/.coder-ai/val/runs/e2e-green/round-1/browser.json" >/dev/null || fail 'promoted baseline did not produce a pixel pass'

printf '%s\n' 'PASS: VAL end-to-end capture, verify, and report'
