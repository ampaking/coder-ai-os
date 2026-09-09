#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP="$(mktemp -d)"
SERVER_PID=''

cleanup() {
  [ -z "$SERVER_PID" ] || kill -TERM "$SERVER_PID" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
node "$ROOT/tests/fixtures/val_fixture_server.mjs" "$port" &
SERVER_PID=$!
url="http://127.0.0.1:$port"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl --fail --silent "$url/count" >/dev/null 2>&1 && break
  sleep 0.1
done

project="$TMP/project"
mkdir -p "$project/.coder-ai/val" "$project/.coder-ai/scripts/val-fixtures"
RUN_CACHE="${VAL_TEST_BROWSER_CACHE_DIR:-$TMP/val-browser-fixture-test}"
jq --arg url "$url" '.env="remote" | .browserMode="local" | .url=$url | .routes=["/dashboard"] | .themes=["light"] | .viewports=[[375,812]] | .auth=null | .fixture={status:"required",adapter:".coder-ai/scripts/val-fixtures/root.mjs"}' \
  "$ROOT/val/templates/val.config.json" > "$project/.coder-ai/val/config.json"
cat > "$project/.coder-ai/scripts/val-fixtures/root.mjs" <<'EOF'
#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";

const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, value, index, values) => {
  if (value.startsWith("--")) pairs.push([value.slice(2), values[index + 1]]);
  return pairs;
}, []));
const origin = new URL(args.url).origin;
const authState = path.join(args.state, "fixture-auth.json");
fs.writeFileSync(authState, JSON.stringify({cookies:[],origins:[{origin,localStorage:[{name:"val-auth",value:"ok"}]}]}));
fs.writeFileSync(args.output, JSON.stringify({version:1,authState,routes:[{url:`${origin}/api/profile`,method:"GET",status:200,contentType:"application/json",body:{role:"fixture-admin"}}]}));
EOF
chmod +x "$project/.coder-ai/scripts/val-fixtures/root.mjs"

set +e
output="$(cd "$project" && VAL_BROWSER_CACHE_DIR="$RUN_CACHE" "$ROOT/val/val" run --task fixture-e2e 2>&1)"
status=$?
set -e
[ "$status" -ne 3 ] || { printf '%s\n' "$output" >&2; fail 'fixture-backed VAL run had an infrastructure failure'; }
run_dir="$project/.coder-ai/val/runs/fixture-e2e"
[ -f "$run_dir/manifest.json" ] || fail 'fixture run manifest missing'
[ "$(jq -r '.exitCode' "$run_dir/manifest.json")" -ne 3 ] || fail 'fixture run ended as infrastructure failure'
[ "$(curl --fail --silent "$url/count")" -eq 0 ] || fail 'fixture API request escaped to the live test backend'
[ "$(stat -c '%a' "$project/.coder-ai/val/fixture.json" 2>/dev/null || stat -f '%Lp' "$project/.coder-ai/val/fixture.json")" = 600 ] || fail 'fixture contract mode is not 600'
[ "$(stat -c '%a' "$project/.coder-ai/val/fixture-auth.json" 2>/dev/null || stat -f '%Lp' "$project/.coder-ai/val/fixture-auth.json")" = 600 ] || fail 'fixture auth state mode is not 600'

outside="$TMP/outside-auth.json"
printf '%s\n' '{"cookies":[],"origins":[]}' > "$outside"
sed -i.bak "s|const authState = path.join(args.state, \"fixture-auth.json\");|const authState = \"$outside\";|" "$project/.coder-ai/scripts/val-fixtures/root.mjs"
rm "$project/.coder-ai/scripts/val-fixtures/root.mjs.bak"
set +e
(cd "$project" && "$ROOT/val/val" run --task fixture-escape) > "$TMP/escape.log" 2>&1
status=$?
set -e
[ "$status" -eq 3 ] || fail 'out-of-state auth fixture was not rejected'
grep -q 'outside VAL_STATE_DIR' "$TMP/escape.log" || fail 'unsafe fixture rejection is not actionable'

printf '%s\n' 'PASS: VAL fixture auth and API isolation'
