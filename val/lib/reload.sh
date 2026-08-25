#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

VAL_MOUNT_WARNING_EMITTED='false'

val_reload_pattern_matches() {
  local file="$1" expression="$2" pattern prefix suffix choices choice expanded
  while IFS= read -r pattern; do
    [ -n "$pattern" ] || continue
    # Config values are used only as shell patterns inside [[ ]], never executed.
    # shellcheck disable=SC2053
    if [[ "$file" == $pattern ]]; then return 0; fi
    case "$pattern" in
      *'{'*'}'*)
        prefix="${pattern%%\{*}"
        choices="${pattern#*\{}"
        choices="${choices%%\}*}"
        suffix="${pattern#*\}}"
        while IFS= read -r choice; do
          expanded="$prefix$choice$suffix"
          # shellcheck disable=SC2053
          if [[ "$file" == $expanded ]]; then return 0; fi
        done < <(printf '%s' "$choices" | tr ',' '\n')
        ;;
    esac
  done < <(val_config_get "$expression | .[]")
  return 1
}

val_reload_is_view_file() {
  local file="$1"
  if val_reload_pattern_matches "$file" '.watchGlobs'; then return 0; fi
  case "$file" in
    *.tsx|*.jsx|*.vue|*.svelte|*.css|*.scss|*.html|*.htm|*.twig|*.erb|*.blade.php) return 0 ;;
    *) return 1 ;;
  esac
}

val_reload_is_dependency_or_config() {
  local file="$1" base
  base="${file##*/}"
  if val_reload_pattern_matches "$file" '.restartOn' || val_reload_pattern_matches "$base" '.restartOn'; then return 0; fi
  case "$base" in
    package.json|package-lock.json|pnpm-lock.yaml|yarn.lock|bun.lock|bun.lockb|requirements.txt|Pipfile|poetry.lock|pyproject.toml|Gemfile|Gemfile.lock|composer.json|composer.lock|tailwind.config.*|*.config.*) return 0 ;;
    *) return 1 ;;
  esac
}

val_reload_is_recreate_file() {
  local file="$1" base
  base="${file##*/}"
  case "$base" in
    .env|.env.*|docker-compose.yml|docker-compose.yaml|compose.yml|compose.yaml) return 0 ;;
    *) return 1 ;;
  esac
}

val_reload_is_dockerfile() {
  case "${1##*/}" in Dockerfile|Dockerfile.*|*.Dockerfile) return 0 ;; *) return 1 ;; esac
}

val_reload_rank_action() {
  local current="$1" candidate="$2"
  case "$current:$candidate" in
    recreate:*|*:recreate) printf '%s\n' recreate ;;
    rebuild:*|*:rebuild) printf '%s\n' rebuild ;;
    restart:*|*:restart) printf '%s\n' restart ;;
    *) printf '%s\n' hmr ;;
  esac
}

val_reload_warn_unmounted() {
  [ "$VAL_MOUNT_WARNING_EMITTED" = false ] || return 0
  printf '%s\n' '⚠ source not bind-mounted — every fix round will rebuild (slow). Add a volume mount.' >&2
  val_log 'source not bind-mounted; view changes require rebuild'
  VAL_MOUNT_WARNING_EMITTED='true'
}

val_reload_decide() {
  local driver="$1" source_mounted="$2" health_ok="$3"
  shift 3
  local action='hmr' file candidate
  if [ "$health_ok" != true ]; then
    case "$driver" in docker|compose) printf '%s\n' recreate ;; *) printf '%s\n' restart ;; esac
    return 0
  fi
  for file in "$@"; do
    candidate='hmr'
    if val_reload_is_recreate_file "$file"; then
      case "$driver" in docker|compose) candidate='recreate' ;; *) candidate='restart' ;; esac
    elif val_reload_is_dockerfile "$file" || val_reload_is_dependency_or_config "$file"; then
      case "$driver" in docker|compose) candidate='rebuild' ;; *) candidate='restart' ;; esac
    elif val_reload_is_view_file "$file"; then
      case "$driver:$source_mounted" in docker:false|compose:false)
        candidate='rebuild'
        val_reload_warn_unmounted
        ;;
      esac
    fi
    action="$(val_reload_rank_action "$action" "$candidate")"
  done
  printf '%s\n' "$action"
}

val_reload_source_mounted() {
  local container="$1"
  docker inspect "$container" 2>/dev/null | jq -e --arg source "$VAL_PROJECT_DIR" '
    .[0].Mounts | any(.[]; .Source as $mount | $source == $mount or ($source | startswith($mount + "/")))
  ' >/dev/null
}

val_reload_restart_owned_server() {
  val_server_stop
  val_server_start
}

val_reload_apply_compose() {
  local action="$1" container_port mapping
  case "$action" in
    rebuild) driver_compose up --detach --build --no-deps "$VAL_COMPOSE_SERVICE" >> "$VAL_SERVER_LOG" 2>&1 ;;
    recreate) driver_compose up --detach --force-recreate --no-deps "$VAL_COMPOSE_SERVICE" >> "$VAL_SERVER_LOG" 2>&1 ;;
    restart) driver_compose restart "$VAL_COMPOSE_SERVICE" >> "$VAL_SERVER_LOG" 2>&1 ;;
    *) return 3 ;;
  esac
  VAL_CONTAINER_ID="$(driver_compose ps --quiet "$VAL_COMPOSE_SERVICE")"
  [ -n "$VAL_CONTAINER_ID" ] || { val_die 3 'Compose reload did not return a container ID'; return 3; }
  VAL_STARTED_BY_US='true'
  VAL_OWNER_TYPE='container'
  container_port="$(driver_compose_port)" || return 3
  mapping="$(driver_compose port "$VAL_COMPOSE_SERVICE" "$container_port" | awk 'NF {value=$0} END {print value}')"
  VAL_PORT="${mapping##*:}"
  case "$VAL_PORT" in *[!0-9]*|'') val_die 3 'Compose reload did not publish a usable host port'; return 3 ;; esac
  val_server_resolve_url
  VAL_CONTAINER_NAME="$(docker inspect -f '{{.Name}}' "$VAL_CONTAINER_ID" | awk '{sub(/^\//, ""); print}')"
  export VAL_CONTAINER_ID VAL_CONTAINER_NAME VAL_PORT VAL_URL VAL_STARTED_BY_US VAL_OWNER_TYPE
  val_server_wait_healthy || { driver_failure_logs >> "$VAL_SERVER_LOG" 2>&1 || true; val_die 3 'Compose reload never became healthy'; return 3; }
  val_server_write_lock
}

val_reload_apply_docker() {
  local action="$1" configured_image
  if [ "$action" = rebuild ]; then
    configured_image="$(val_config_get '.container.image // empty' || true)"
    [ -n "$configured_image" ] || configured_image="$VAL_CONTAINER_IMAGE"
    docker build --tag "$configured_image" "$VAL_PROJECT_DIR" >> "$VAL_SERVER_LOG" 2>&1 || { val_die 3 'Docker reload build failed'; return 3; }
  fi
  val_reload_restart_owned_server
}

val_reload_apply() {
  local action="$1"
  case "$action" in hmr) return 0 ;; restart|rebuild|recreate) ;; *) val_die 3 "invalid reload action: $action"; return 3 ;; esac
  if [ "$VAL_STARTED_BY_US" != true ]; then
    val_log "reload blocked: action=$action server was not started by VAL"
    return 2
  fi
  val_server_load_driver
  case "$VAL_DRIVER" in
    node) val_reload_restart_owned_server ;;
    docker) val_reload_apply_docker "$action" ;;
    compose) val_reload_apply_compose "$action" ;;
    *) val_log "reload blocked: driver=$VAL_DRIVER cannot mutate external server"; return 2 ;;
  esac
}
