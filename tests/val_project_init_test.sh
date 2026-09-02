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

node_project="$TMP/old node app"
mkdir -p "$node_project/src"
printf '%s\n' '{"scripts":{"dev":"webpack-dev-server"},"devDependencies":{"webpack":"3.0.0"}}' > "$node_project/package.json"
printf '%s\n' '# old lock' > "$node_project/yarn.lock"
printf '%s\n' '<div>legacy</div>' > "$node_project/src/App.jsx"
"$ROOT/val/project-init" "$node_project" >/dev/null
node_config="$node_project/.coder-ai/val/config.json"
[ "$(jq -r '.generated.detectedKind' "$node_config")" = package-script ] || fail 'legacy Node UI was not detected'
[ "$(jq -r '.serve' "$node_config")" = 'yarn dev' ] || fail 'yarn serve command was not generated'
[ -x "$node_project/.coder-ai/val/run" ] || fail 'project VAL runner was not generated'
grep -q '^auth.json$' "$node_project/.coder-ai/val/.gitignore" || fail 'VAL runtime secrets are not ignored'
[ "$(jq -r '.apps | length' "$node_project/.coder-ai/val/apps.json")" = 1 ] || fail 'single web app registry missing'
[ "$(jq -r '.apps[0].id' "$node_project/.coder-ai/val/apps.json")" = root ] || fail 'root app id is unstable'
[ -f "$node_project/.coder-ai/val/apps/root/config.json" ] || fail 'isolated root app config missing'
[ -x "$node_project/.coder-ai/scripts/val-fixtures/root.mjs" ] || fail 'test-auth fixture scaffold missing'
if node "$node_project/.coder-ai/scripts/val-fixtures/root.mjs" 2>"$TMP/fixture-error"; then fail 'generated fixture scaffold did not fail closed'; fi
grep -q 'Never read production credentials' "$TMP/fixture-error" || fail 'fixture scaffold lacks safe completion guidance'

before="$(cksum "$node_config")"
jq '.routes=["/custom"]' "$node_config" > "$node_config.user"
mv "$node_config.user" "$node_config"
custom="$(cksum "$node_config")"
"$ROOT/val/project-init" "$node_project" >/dev/null
[ "$(cksum "$node_config")" = "$custom" ] || fail 'existing VAL config was overwritten'
[ "$before" != "$custom" ] || fail 'config preservation fixture did not change config'
"$ROOT/bin/coder-ai-os" setup "$node_project" >/dev/null
[ "$(cksum "$node_config")" = "$custom" ] || fail 'coder-ai-os setup overwrote existing VAL config'

django_project="$TMP/django app"
mkdir -p "$django_project/templates"
printf '%s\n' '#!/usr/bin/env python3' > "$django_project/manage.py"
printf '%s\n' '<main>Django</main>' > "$django_project/templates/index.html"
"$ROOT/val/project-init" "$django_project" >/dev/null
[ "$(jq -r '.generated.detectedKind' "$django_project/.coder-ai/val/config.json")" = django ] || fail 'Django UI was not detected'
grep -q 'manage.py runserver' "$django_project/.coder-ai/val/config.json" || fail 'Django serve command missing'

backend_project="$TMP/backend only"
mkdir -p "$backend_project"
printf '%s\n' 'print("api")' > "$backend_project/api.py"
"$ROOT/val/project-init" "$backend_project" >/dev/null
[ ! -f "$backend_project/.coder-ai/val/config.json" ] || fail 'non-UI project received an invented config'
grep -q "Detection: \`none\`" "$backend_project/.coder-ai/val/SETUP.md" || fail 'non-UI guidance missing'

symlink_ignore_project="$TMP/symlink ignore"
mkdir -p "$symlink_ignore_project/.coder-ai/val" "$symlink_ignore_project/src"
printf '%s\n' '<main>UI</main>' > "$symlink_ignore_project/src/index.html"
ln -s "$TMP/dangling-ignore-target" "$symlink_ignore_project/.coder-ai/val/.gitignore"
if "$ROOT/val/project-init" "$symlink_ignore_project" >/dev/null 2>&1; then fail 'project setup followed a runtime .gitignore symlink'; fi
[ ! -e "$TMP/dangling-ignore-target" ] || fail 'project setup wrote outside state through .gitignore symlink'

monorepo="$TMP/web monorepo"
mkdir -p "$monorepo/apps/admin/src" "$monorepo/apps/user/src" "$monorepo/packages/shared"
printf '%s\n' '{"private":true,"workspaces":["apps/*","packages/*"]}' > "$monorepo/package.json"
printf '%s\n' '{"name":"admin-web","scripts":{"dev":"next dev"},"dependencies":{"next":"15.0.0"}}' > "$monorepo/apps/admin/package.json"
printf '%s\n' '{"name":"user-web","scripts":{"dev":"vite"},"dependencies":{"react":"19.0.0","vite":"6.0.0"}}' > "$monorepo/apps/user/package.json"
printf '%s\n' '{"name":"shared"}' > "$monorepo/packages/shared/package.json"
printf '%s\n' 'export default function Page(){return null}' > "$monorepo/apps/admin/src/page.tsx"
printf '%s\n' 'export default function App(){return null}' > "$monorepo/apps/user/src/App.tsx"
printf '%s\n' 'lockfileVersion: 9' > "$monorepo/pnpm-lock.yaml"
"$ROOT/val/project-init" "$monorepo" >/dev/null
[ "$(jq -r '.apps | length' "$monorepo/.coder-ai/val/apps.json")" = 2 ] || fail 'monorepo web apps were not isolated'
jq -e '.apps | map(.id) == ["apps-admin","apps-user"]' "$monorepo/.coder-ai/val/apps.json" >/dev/null || fail 'monorepo app ids are not stable'
grep -q 'pnpm --dir apps/admin run dev' "$monorepo/.coder-ai/val/apps/apps-admin/config.json" || fail 'admin serve command is not root-safe'
grep -q 'pnpm --dir apps/user run dev' "$monorepo/.coder-ai/val/apps/apps-user/config.json" || fail 'user serve command is not root-safe'
if (cd "$monorepo" && PATH="$ROOT/val:$PATH" ./.coder-ai/val/run doctor >/dev/null 2>&1); then fail 'ambiguous monorepo run did not require --app'; fi
(cd "$monorepo" && PATH="$ROOT/val:$PATH" ./.coder-ai/val/run --app apps-admin doctor >/dev/null) || fail 'selected monorepo app doctor failed'

printf '%s\n' 'PASS: VAL automatic project setup'
