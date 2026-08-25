#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

VAL_SOURCE_DIR="$ROOT/val"
VAL_PROJECT_DIR="$TMP/project"
VAL_STATE_DIR="$VAL_PROJECT_DIR/.coder-ai/val"
VAL_ALLOWED_FILES='src/ui.css'
VAL_LOG_FILE="$TMP/val.log"
export VAL_SOURCE_DIR VAL_PROJECT_DIR VAL_STATE_DIR VAL_ALLOWED_FILES VAL_LOG_FILE
mkdir -p "$VAL_PROJECT_DIR/src" "$TMP/round"
printf '%s\n' 'old' > "$VAL_PROJECT_DIR/src/ui.css"
: > "$VAL_LOG_FILE"
# shellcheck source=val/lib/common.sh
source "$ROOT/val/lib/common.sh"
# shellcheck source=val/lib/fix.sh
source "$ROOT/val/lib/fix.sh"

allowed='--- a/src/ui.css
+++ b/src/ui.css
@@ -1 +1 @@
-old
+new'
judgment="$(jq -n --arg patch "$allowed" '{criterionId:"c-1",file:"src/ui.css",suggestedFix:$patch,actionable:true}')"
val_fix_apply_judgment "$judgment" "$TMP/round" "$TMP/attempts.json" >/dev/null || fail 'allowlisted patch was rejected'
[ "$(awk 'NR == 1 {print}' "$VAL_PROJECT_DIR/src/ui.css")" = new ] || fail 'checked patch was not applied to the temporary project'

outside="$(printf '%s' "$allowed" | sed 's|src/ui.css|src/other.css|g')"
outside_judgment="$(jq -n --arg patch "$outside" '{criterionId:"c-2",file:"src/other.css",suggestedFix:$patch,actionable:true}')"
if val_fix_apply_judgment "$outside_judgment" "$TMP/round" "$TMP/attempts.json" >/dev/null; then fail 'out-of-scope patch was accepted'; fi

printf '%s\n' secret > "$VAL_PROJECT_DIR/.env"
VAL_ALLOWED_FILES=$'src/ui.css\n.env'
export VAL_ALLOWED_FILES
secret_patch="$(printf '%s' "$allowed" | sed 's|src/ui.css|.env|g')"
if val_fix_validate_patch <(printf '%s\n' "$secret_patch"); then fail 'secret patch was accepted'; fi

printf '%s\n' other > "$VAL_PROJECT_DIR/src/other.txt"
rename_patch="$allowed
diff --git a/src/other.txt b/src/renamed.txt
similarity index 100%
rename from src/other.txt
rename to src/renamed.txt"
if val_fix_validate_patch <(printf '%s\n' "$rename_patch"); then fail 'out-of-scope rename metadata was accepted'; fi
VAL_ALLOWED_FILES='src/ui.css'
export VAL_ALLOWED_FILES

for _ in 1 2; do val_fix_apply_judgment "$judgment" "$TMP/round" "$TMP/attempts.json" >/dev/null || true; done
set +e
val_fix_apply_judgment "$judgment" "$TMP/round" "$TMP/attempts.json" >/dev/null
status=$?
set -e
[ "$status" -eq 2 ] || fail 'fourth attempt was not blocked by the three-attempt cap'
[ "$(jq -r '.["c-1"]' "$TMP/attempts.json")" -eq 3 ] || fail 'attempt ledger is incorrect'

printf '%s\n' 'PASS: VAL guarded patch confinement and attempt cap'
