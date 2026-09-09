#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

VAL_CONFIG_JSON=''

val_config_defaults() {
  jq -n '{
    env: "auto",
    browserMode: "auto",
    serve: "pnpm dev",
    portFlag: "env",
    url: "http://localhost:${PORT}",
    healthPath: "/",
    readyTimeoutMs: 60000,
    authTimeoutMs: 15000,
    reuseExisting: true,
    routes: ["/", "/pricing"],
    viewports: [[375, 812], [768, 1024], [1440, 900]],
    themes: ["light", "dark"],
    restartOn: ["package.json", "*lock*", "*.config.*", ".env"],
    watchGlobs: ["**/*.{tsx,jsx,vue,svelte,css,scss,html,twig,erb,blade.php}"],
    maxShots: 40,
    container: {service: null, port: null, image: null},
    auth: null,
    fixture: {status: "disabled", adapter: null}
  }'
}

val_config_validate() {
  jq -e '
    type == "object"
    and (.env | IN("auto", "node", "docker", "compose", "remote"))
    and (.browserMode | IN("auto", "docker", "local"))
    and (.portFlag | IN("env", "arg"))
    and (.serve | type == "string")
    and (.url | type == "string" and length > 0)
    and (.healthPath | type == "string" and startswith("/"))
    and (.readyTimeoutMs | type == "number" and . > 0)
    and (.authTimeoutMs | type == "number" and . > 0)
    and (.reuseExisting | type == "boolean")
    and (.routes | type == "array" and all(.[]; type == "string" and startswith("/")))
    and (.viewports | type == "array" and all(.[]; type == "array" and length == 2 and all(.[]; type == "number" and . > 0)))
    and (.themes | type == "array" and all(.[]; IN("light", "dark")))
    and (.restartOn | type == "array" and all(.[]; type == "string"))
    and (.watchGlobs | type == "array" and all(.[]; type == "string"))
    and (.auth == null or ((.auth.allowedOrigins // []) | type == "array" and all(.[]; type == "string")))
    and (.fixture | type == "object")
    and (.fixture.status | IN("disabled", "required"))
    and (.fixture.adapter == null or (.fixture.adapter | type == "string" and startswith(".coder-ai/scripts/val-fixtures/")))
    and (.maxShots | type == "number" and . >= 1 and floor == .)
    and (.container | type == "object")
    and (.container.service == null or (.container.service | type == "string" and length > 0))
    and (.container.port == null or (.container.port | type == "number" and . > 0 and floor == .))
    and (.container.image == null or (.container.image | type == "string" and length > 0))
    and (.auth == null or (
      (.auth | type == "object")
      and (.auth.loginUrl | type == "string" and startswith("/"))
      and (.auth.storageState | type == "string" and length > 0)
      and (.auth.successCheck | type == "string" and length > 0)
      and (.auth.steps | type == "array" and length > 0)
      and all(.auth.steps[];
        (keys | map(IN("fill", "value", "click", "waitFor")) | all)
        and ([has("fill"), has("click"), has("waitFor")] | map(select(.)) | length == 1)
        and (if has("fill") then
          (.fill | type == "string" and length > 0)
          and (.value | type == "string" and test("^\\$\\{[A-Za-z_][A-Za-z0-9_]*\\}$"))
        elif has("click") then (.click | type == "string" and length > 0)
        else (.waitFor | type == "string" and length > 0) end)
      )
    ))
  ' >/dev/null
}

val_config_load() {
  local file="${1:-$VAL_CONFIG_FILE}"
  local defaults user

  val_require_core
  defaults="$(val_config_defaults)"
  if [ -f "$file" ]; then
    if ! user="$(jq -e 'if type == "object" then . else error("expected object") end' "$file")"; then
      val_die 3 "invalid JSON object: $file"
      return 3
    fi
  else
    user='{}'
  fi
  VAL_CONFIG_JSON="$(jq -n --argjson defaults "$defaults" --argjson user "$user" '$defaults * $user')"
  if ! printf '%s' "$VAL_CONFIG_JSON" | val_config_validate; then
    val_die 3 "invalid VAL configuration: $file"
    return 3
  fi
  export VAL_CONFIG_JSON
}

val_config_get() {
  local expression="$1"
  [ -n "$VAL_CONFIG_JSON" ] || val_config_load
  printf '%s' "$VAL_CONFIG_JSON" | jq -er "$expression"
}
