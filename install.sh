#!/usr/bin/env bash
# coder-ai-os — install personal AI-agent defaults into global (~) config for
# Claude Code, OpenAI Codex, and Google Antigravity. Idempotent: safe to re-run
# after `git pull`. Managed regions are marked; content outside them is preserved.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACTIVE_PROJECT=""
CODEX_TMP=""
DRY_RUN=0
CODEX_DEFAULT=0
WITH_CODEGRAPH=0
WITH_MCP=0
STATUS=0
YES=0
PROJECT=""
usage(){ echo "usage: install.sh [--init] [--yes] [--dry-run] [--status] [--codex-safety-defaults] [--with-codegraph] [--with-mcp] [--project <repo-path>]"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --yes) YES=1 ;;
    --init) exec "$REPO_DIR/bin/setup" ;;
    --status) STATUS=1 ;;
    --codex-safety-defaults|--codex-default-autonomous) CODEX_DEFAULT=1 ;;
    --with-codegraph) WITH_CODEGRAPH=1 ;;
    --with-mcp) WITH_MCP=1 ;;
    --project) shift; PROJECT="${1:-}"; [ -n "$PROJECT" ] || { echo "--project needs a path" >&2; exit 2; } ;;
    --project=*) PROJECT="${1#*=}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

MD_BEGIN='<!-- >>> coder-ai-os:managed >>> -->'
MD_END='<!-- <<< coder-ai-os:managed <<< -->'
TOML_BEGIN='# >>> coder-ai-os:managed >>>'
TOML_END='# <<< coder-ai-os:managed <<<'
# Legacy markers from the old project name — cleaned up on install so a rename
# doesn't leave duplicate blocks in already-installed global files.
MD_BEGIN_OLD='<!-- >>> ai-agent-os:managed >>> -->'
MD_END_OLD='<!-- <<< ai-agent-os:managed <<< -->'
TOML_BEGIN_OLD='# >>> ai-agent-os:managed >>>'
TOML_END_OLD='# <<< ai-agent-os:managed <<<'

log(){ printf '  %s\n' "$*"; }
reject_symlink(){
  local cur="$1"
  while [ -n "$ACTIVE_PROJECT" ] && [ "$cur" != "$ACTIVE_PROJECT" ] && [ "$cur" != / ]; do
    [ ! -L "$cur" ] || { log "ERROR: refusing symlink path: $cur"; return 1; }
    cur="$(dirname "$cur")"
  done
  [ ! -L "$1" ] || { log "ERROR: refusing symlink target: $1"; return 1; }
}

# OVERLAY_BUILD — set by compile_step to a temp dir holding this machine's PERSONALIZED
# build (config/local.yaml applied). Empty => fall back to the committed defaults-only build/.
OVERLAY_BUILD=""
cleanup_overlay_build(){
  [ -z "$OVERLAY_BUILD" ] || rm -rf "$OVERLAY_BUILD"
  [ -z "$CODEX_TMP" ] || rm -rf "$CODEX_TMP"
}
trap cleanup_overlay_build EXIT

# compile_step — render this machine's personalized build into a TEMP dir when python3 is
# available, so per-machine prefs (config/local.yaml) reach ~/.claude WITHOUT ever
# overwriting the committed, defaults-only build/. Install works without python3 too: it
# just uses the committed build/ as-is.
compile_step(){
  if command -v python3 >/dev/null 2>&1 && [ -x "$REPO_DIR/bin/compile" ]; then
    if [ "$DRY_RUN" = 1 ]; then log "would compile personalized config/*.yaml -> (temp build)"; return; fi
    local tmp; tmp="$(mktemp -d)"
    if python3 "$REPO_DIR/bin/compile" --overlay --out "$tmp" >/dev/null; then
      OVERLAY_BUILD="$tmp"; log "compiled personalized config/*.yaml (committed build/ untouched)";
    else rm -rf "$tmp"; log "SKIP compile (bin/compile failed) — using committed build/ (or shared/rules.md)"; fi
  else
    log "python3 not found — using committed build/*.md (run bin/compile after editing config)"
  fi
}

# tool_body <tool-filename> — the compiled per-tool block: personalized (temp) build if
# compile_step produced one, else the committed defaults-only build/, else the fallback.
tool_body(){
  local f
  [ -n "$OVERLAY_BUILD" ] && [ -f "$OVERLAY_BUILD/$1" ] && { echo "$OVERLAY_BUILD/$1"; return; }
  f="$REPO_DIR/build/$1"
  if [ -f "$f" ]; then echo "$f"; else echo "$REPO_DIR/shared/rules.md"; fi
}

# safe_cp <src-file> <dest-file> — copy only if the destination is absent OR is one of ours
# (carries the coder-ai-os:generated marker). Never overwrites a user's own same-named file.
safe_cp(){
  local src="$1" dest="$2"
  reject_symlink "$dest" || return 1
  if [ -f "$dest" ] && ! grep -Eq 'coder-ai-os:generated|coder-ai-os engineering defaults \(generated\)|(update-ai-context|discover-standards)\.sh —' "$dest" 2>/dev/null; then
    log "  kept your existing $(basename "$dest") — not overwritten (rename to keep both)"; return
  fi
  mkdir -p "$(dirname "$dest")"; cp "$src" "$dest"
}
# seed_cp <src-file> <dest-file> — install shared project state only when absent.
# Unlike generated commands, maintained navigator content must survive every later sync.
seed_cp(){
  local src="$1" dest="$2"
  reject_symlink "$dest" || return 1
  if [ -e "$dest" ]; then
    [ -f "$dest" ] || { log "ERROR: expected a regular file: $dest"; return 1; }
    log "  kept maintained $(basename "$dest")"; return
  fi
  mkdir -p "$(dirname "$dest")"; cp "$src" "$dest"
  log "seeded shared human-AI map -> $dest"
}
# safe_cp_tree <src-dir> <dest-dir> — safe_cp every file, preserving structure.
safe_cp_tree(){
  local src="$1" dest="$2" rel
  [ -d "$src" ] || return 0
  ( cd "$src" && find . -type f ) | while read -r rel; do
    rel="${rel#./}"; safe_cp "$src/$rel" "$dest/$rel"
  done
}

# install_cmds <build-subdir> <dest-dir> <label> — drop compiled command/prompt files into a
# tool's native command dir, never clobbering the user's own same-named commands.
install_cmds(){
  local src="$REPO_DIR/build/commands/$1" dest="$2" f
  [ -d "$src" ] || return 0
  if [ "$DRY_RUN" = 1 ]; then log "would install $3 commands -> $dest (skipping any you already have)"; return; fi
  for f in "$src"/*; do [ -f "$f" ] && safe_cp "$f" "$dest/$(basename "$f")"; done
  log "installed $3 commands -> $dest"
}

# inject <target> <content_file> <begin_marker> <end_marker> [legacy_begin] [legacy_end]
# Replaces the region between the markers (or appends it) with the content file. Any
# legacy-marker region is also stripped, so a project rename never duplicates blocks.
inject(){
  local target="$1" content="$2" begin="$3" end="$4" lbegin="${5:-$3}" lend="${6:-$4}"
  reject_symlink "$target" || return 1
  if [ "$DRY_RUN" = 1 ]; then log "would update managed block -> $target"; return; fi
  mkdir -p "$(dirname "$target")"; touch "$target"
  if ! awk -v b="$begin" -v e="$end" -v lb="$lbegin" -v le="$lend" '
    $0==b || $0==lb {if(open) bad=1; open=1; next}
    $0==e || $0==le {if(!open) bad=1; open=0; next}
    END {exit(bad || open ? 1 : 0)}
  ' "$target"; then
    log "ERROR: unbalanced coder-ai-os markers in $target; left unchanged"; return 1
  fi
  local tmp; tmp="$(mktemp)"
  awk -v b="$begin" -v e="$end" -v lb="$lbegin" -v le="$lend" '
    $0==b || $0==lb {skip=1; next} $0==e || $0==le {skip=0; next} skip!=1{print}
  ' "$target" > "$tmp"
  # trim trailing blank lines from the preserved part
  awk '{a[NR]=$0} END{last=NR; while(last>0 && a[last]==""){last--}; for(i=1;i<=last;i++) print a[i]}' "$tmp" > "$target"
  {
    if [ -s "$target" ]; then echo ""; fi
    echo "$begin"; cat "$content"; echo "$end"
  } >> "$target"
  rm -f "$tmp"
  log "updated -> $target"
}

# write_codex_config <target> — add current Codex safety defaults without replacing user-owned
# top-level values or unrelated TOML. The managed block is prepended because top-level TOML keys
# written after a table header would belong to that table. Existing values always win.
write_codex_config(){
  local target="$1" source="$REPO_DIR/codex/config.defaults.toml" dir work clean block out key line found=0
  if [ "$DRY_RUN" = 1 ]; then log "would merge Codex defaults -> $target (existing values win)"; return; fi
  reject_symlink "$target" || return 1
  if [ -f "$target" ] && grep -Eq "\"\"\"|'''|=[[:space:]]*\\[[^]]*$|=[[:space:]]*\\{[^}]*$" "$target"; then
    log "ERROR: ambiguous multiline TOML in $target; left unchanged"; return 1
  fi
  dir="$(dirname "$target")"; mkdir -p "$dir"; [ -f "$target" ] || : > "$target"
  work="$(mktemp -d "$dir/.coder-ai-os.toml.XXXXXX")" || return 1; CODEX_TMP="$work"
  clean="$work/clean"; block="$work/block"; out="$work/out"; : > "$block"
  if ! awk -v b="$TOML_BEGIN" -v e="$TOML_END" -v lb="$TOML_BEGIN_OLD" -v le="$TOML_END_OLD" '
    $0==b || $0==lb {if(open) bad=1; open=1; next}
    $0==e || $0==le {if(!open) bad=1; open=0; next}
    END {exit(bad || open ? 1 : 0)}
  ' "$target"; then
    log "ERROR: unbalanced coder-ai-os markers in $target; left unchanged"
    rm -rf "$work"; return 1
  fi
  awk -v b="$TOML_BEGIN" -v e="$TOML_END" -v lb="$TOML_BEGIN_OLD" -v le="$TOML_END_OLD" '
    $0==b || $0==lb {skip=1; next} $0==e || $0==le {skip=0; next} skip!=1{print}
  ' "$target" > "$clean"
  for key in approval_policy sandbox_mode; do
    if awk -v k="$key" '
      BEGIN {top=1; pat="^[[:space:]]*(\"" k "\"|\047" k "\047|" k ")[[:space:]]*="}
      /^[[:space:]]*\[/ {top=0}
      top!=0 && $0 ~ pat {found=1}
      END {exit(found ? 0 : 1)}
    ' "$clean"; then
      log "Codex: kept existing $key in $target"; continue
    fi
    line="$(awk -v k="$key" '$0 ~ "^[[:space:]]*" k "[[:space:]]*=" {print; exit}' "$source")"
    [ -n "$line" ] && { printf '%s\n' "$line" >> "$block"; found=1; }
  done
  # [tui].status_line rides in its own appended managed region: tables cannot join the
  # prepended block (user top-level keys after a table header would be captured by it).
  # A user-owned status_line or explicit [tui] table wins — we skip to avoid duplicates.
  local tui_block="$work/tui"
  awk '/^\[tui\]/{t=1} t' "$source" > "$tui_block"
  if [ -s "$tui_block" ]; then
    if grep -Eq '^[[:space:]]*(\[tui\]|status_line[[:space:]]*=)' "$clean"; then
      log "Codex: kept existing [tui]/status_line in $target"
    else
      { printf '%s\n' "$TOML_BEGIN"; cat "$tui_block"; printf '%s\n' "$TOML_END"; } >> "$clean"
    fi
  fi
  if [ "$found" = 1 ]; then
    { printf '%s\n' "$TOML_BEGIN"; cat "$block"; printf '%s\n' "$TOML_END"; cat "$clean"; } > "$out"
  else cat "$clean" > "$out"; fi
  local mode=600
  if [ -f "$target" ]; then mode="$(stat -c '%a' "$target" 2>/dev/null || stat -f '%Lp' "$target" 2>/dev/null || echo 600)"; fi
  chmod "$mode" "$out"
  if command -v python3 >/dev/null 2>&1 && python3 -c 'import tomllib' >/dev/null 2>&1; then
    python3 -c 'import sys,tomllib; tomllib.load(open(sys.argv[1],"rb"))' "$out" || { log "ERROR: merged TOML is invalid; left unchanged"; rm -rf "$work"; CODEX_TMP=""; return 1; }
  fi
  if mv "$out" "$target"; then
    rm -rf "$work"; CODEX_TMP=""; log "merged Codex defaults -> $target (preserved existing values)"
  else rm -rf "$work"; CODEX_TMP=""; return 1; fi
}

merge_claude_settings(){
  local target="$HOME/.claude/settings.json" add="$REPO_DIR/claude/permissions.json"
  if ! command -v jq >/dev/null 2>&1; then
    log "SKIP $target (jq not found — install jq and re-run to apply permissions)"; return
  fi
  if [ "$DRY_RUN" = 1 ]; then log "would jq-merge permissions -> $target"; return; fi
  mkdir -p "$(dirname "$target")"; [ -f "$target" ] || echo '{}' > "$target"
  local tmp; tmp="$(mktemp)"
  jq --slurpfile add "$add" '
    .permissions = (.permissions // {})
    | .permissions.defaultMode = (.permissions.defaultMode // $add[0].permissions.defaultMode)
    | .permissions.allow = (((.permissions.allow // []) + $add[0].permissions.allow) | unique)
    | .permissions.deny  = (((.permissions.deny  // []) + $add[0].permissions.deny)  | unique)
    | .skipAutoPermissionPrompt = (.skipAutoPermissionPrompt // true)
    | .statusLine = (.statusLine // {"type": "command", "command": "~/.claude/statusline.sh"})
  ' "$target" > "$tmp" && mv "$tmp" "$target"
  # Ship the default script only when the user has none — never overwrite a custom one.
  # -L catches a dangling symlink (-e follows it), so we never write through a planted link.
  if [ ! -e "$HOME/.claude/statusline.sh" ] && [ ! -L "$HOME/.claude/statusline.sh" ]; then
    cp "$REPO_DIR/claude/statusline.sh" "$HOME/.claude/statusline.sh" \
      && chmod 755 "$HOME/.claude/statusline.sh" \
      && log "installed default status line -> ~/.claude/statusline.sh (live context %)"
  fi
  log "merged permissions -> $target (kept your defaultMode; unioned guardrails; statusLine if unset)"
}

# register_codegraph — delegate MCP wiring to CodeGraph's own installer, which
# writes each agent's MCP config in its OWN marker blocks (disjoint from ours) and
# is removable via `codegraph uninstall`. We do not hand-roll `claude mcp add`.
# Opt-in via --with-codegraph, because it adds a third-party npx/global dependency.
register_codegraph(){
  if [ "$DRY_RUN" = 1 ]; then log "would run: codegraph install --target=claude,codex --yes --location global"; return; fi
  if ! command -v codegraph >/dev/null 2>&1; then
    if command -v npm >/dev/null 2>&1; then
      log "codegraph not found — installing globally via npm"
      npm i -g @colbymchenry/codegraph || { log "SKIP codegraph: 'npm i -g @colbymchenry/codegraph' failed"; return 1; }
    else
      log "SKIP --with-codegraph: 'codegraph' not on PATH and npm unavailable."
      log "  install it: npm i -g @colbymchenry/codegraph   (or see https://github.com/colbymchenry/codegraph)"
      return 1
    fi
  fi
  # Configures Claude + Codex MCP + auto-allow perms; does NOT build any index.
  codegraph install --target=claude,codex --yes --location global \
    && log "registered CodeGraph MCP (claude, codex)" \
    || log "SKIP codegraph install (command returned non-zero)"
}

# mcp_codex <name> <command> <space-args> — add a marker-guarded [mcp_servers.<name>] block
# to ~/.codex/config.toml (idempotent; one region per server).
mcp_codex(){
  local name="$1" cmd="$2" args="$3" f="$HOME/.codex/config.toml" a toml=""
  for a in $args; do toml="$toml\"$a\", "; done
  local tmp; tmp="$(mktemp)"
  printf '[mcp_servers.%s]\ncommand = "%s"\nargs = [%s]\n' "$name" "$cmd" "${toml%, }" > "$tmp"
  inject "$f" "$tmp" "# >>> coder-ai-os:mcp:$name >>>" "# <<< coder-ai-os:mcp:$name <<<"
  rm -f "$tmp"; log "mcp: registered $name -> Codex (~/.codex/config.toml)"
}

# mcp_json_merge <file> <name> <command> <space-args> <label> — set .mcpServers.<name> via jq
# (Gemini settings.json / Cursor mcp.json use the same shape).
mcp_json_merge(){
  local f="$1" name="$2" cmd="$3" args="$4" label="$5" argsjson
  command -v jq >/dev/null 2>&1 || { log "mcp: SKIP $name -> $label (jq not found)"; return; }
  if [ -z "$args" ]; then argsjson='[]'; else argsjson="$(printf '%s\n' $args | jq -R . | jq -sc .)"; fi
  mkdir -p "$(dirname "$f")"; [ -f "$f" ] || echo '{}' > "$f"
  local tmp; tmp="$(mktemp)"
  jq --arg n "$name" --arg c "$cmd" --argjson a "$argsjson" \
    '.mcpServers = (.mcpServers // {}) | .mcpServers[$n] = {command:$c, args:$a}' "$f" > "$tmp" \
    && mv "$tmp" "$f" && log "mcp: registered $name -> $label ($f)" || { rm -f "$tmp"; log "mcp: SKIP $name -> $label (jq merge failed)"; }
}

# register_mcp — register every generic server in config/mcp.yaml with its target tools, each
# via that tool's native mechanism. Servers with an `installer` field are skipped here (handled
# by their dedicated path, e.g. --with-codegraph). Opt-in via --with-mcp.
register_mcp(){
  command -v python3 >/dev/null 2>&1 || { log "SKIP --with-mcp: python3 needed to read config/mcp.yaml"; return; }
  local name cmd args targets installer
  python3 "$REPO_DIR/bin/compile" --print-mcp | while IFS="$(printf '\t')" read -r name cmd args targets installer; do
    [ -n "$name" ] || continue
    if [ -n "$installer" ]; then log "mcp: $name uses its own installer ($installer) — skip generic (use --with-$installer)"; continue; fi
    if [ "$DRY_RUN" = 1 ]; then log "would register MCP '$name' -> $targets"; continue; fi
    case ",$targets," in *,claude,*)
      if command -v claude >/dev/null 2>&1; then
        claude mcp remove "$name" --scope user >/dev/null 2>&1 || true
        claude mcp add --scope user "$name" -- $cmd $args >/dev/null 2>&1 \
          && log "mcp: registered $name -> Claude (user scope)" \
          || log "mcp: SKIP $name -> Claude (claude mcp add failed)"
      else log "mcp: SKIP $name -> Claude (claude CLI not found)"; fi ;;
    esac
    case ",$targets," in *,codex,*) mcp_codex "$name" "$cmd" "$args" ;; esac
    case ",$targets," in *,gemini,*) mcp_json_merge "$HOME/.gemini/settings.json" "$name" "$cmd" "$args" Gemini ;; esac
    case ",$targets," in *,cursor,*) mcp_json_merge "$HOME/.cursor/mcp.json" "$name" "$cmd" "$args" Cursor ;; esac
  done
}

# scaffold_monorepo <repo> — in a monorepo, reinforce per-package isolation. Augments only
# packages that ALREADY have an AGENTS.md/CLAUDE.md (never creates surprise files) with a
# marker-guarded pointer to the root harness + the monorepo-change skill.
scaffold_monorepo(){
  local proj="$1" pkgroot pkg name target tmp found=0 detected=0
  for pkgroot in packages apps services libs modules; do
    [ -d "$proj/$pkgroot" ] && detected=1 || continue
    for pkg in "$proj/$pkgroot"/*/; do
      [ -d "$pkg" ] || continue
      target=""
      [ -f "${pkg}AGENTS.md" ] && target="${pkg}AGENTS.md"
      [ -z "$target" ] && [ -f "${pkg}CLAUDE.md" ] && target="${pkg}CLAUDE.md"
      [ -n "$target" ] || continue
      if [ "$DRY_RUN" = 1 ]; then log "would reinforce $target with monorepo isolation guidance"; found=$((found+1)); continue; fi
      name="$(basename "$pkg")"; tmp="$(mktemp)"
      printf 'Package: %s (monorepo). Follow the root coder-ai-os harness; work isolated here — change only this package and run its own tests/lint first. For changes that span packages, use the `monorepo-change` skill: fix the owning package, trace dependents, fix each in its own package, then return to root for the full harness check.\n' "$name" > "$tmp"
      inject "$target" "$tmp" "$MD_BEGIN" "$MD_END" "$MD_BEGIN_OLD" "$MD_END_OLD"
      rm -f "$tmp"; found=$((found+1))
    done
  done
  if [ "$found" -gt 0 ]; then log "monorepo: reinforced $found package agent file(s) with isolation guidance"
  elif [ "$detected" = 1 ]; then log "monorepo detected — the 'monorepo-change' skill guides cross-package fixes (no per-package agent files to augment)"; fi
}

# detect_langs <repo> — a comma list of languages present, e.g. "Python, TypeScript".
detect_langs(){
  local p="$1" out="" files
  files="$(git -C "$p" ls-files 2>/dev/null || find "$p" -type f 2>/dev/null)"
  printf '%s\n' "$files" | grep -qE '\.py$'   && out="$out, Python"
  printf '%s\n' "$files" | grep -qE '\.tsx?$' && out="$out, TypeScript"
  printf '%s\n' "$files" | grep -qE '\.jsx?$' && out="$out, JavaScript"
  printf '%s\n' "$files" | grep -qE '\.go$'   && out="$out, Go"
  printf '%s\n' "$files" | grep -qE '\.rs$'   && out="$out, Rust"
  printf '%s\n' "$files" | grep -qE '\.rb$'   && out="$out, Ruby"
  printf '%s\n' "$files" | grep -qE '\.java$' && out="$out, Java"
  echo "${out#, }"
}

# ensure_project_yaml <repo> — create <repo>/.coder-ai/project.yaml (this repo's config) if absent.
ensure_project_yaml(){
  local p="$1" f id langs; f="$1/.coder-ai/project.yaml"
  mkdir -p "$1/.coder-ai"
  [ -f "$f" ] && return 0
  id="$(basename "$(cd "$p" && pwd)")"; langs="$(detect_langs "$p")"
  {
    echo "# $id — this repo's coder-ai-os config (highest precedence). Safety can't be weakened here."
    echo "# Merged over your global profile when compiling THIS repo. Edit freely."
    echo "project:"
    echo "  id: $id"
    echo "user:"
    [ -n "$langs" ] && echo "  languages: [$langs]" || echo "  languages: []"
    echo "# Override anything: tokens.reply_format, workflow.*, skills.enabled, ..."
  } > "$f"
  # project.yaml is shared (committed); generated/ + identity.json are machine-local.
  [ -f "$1/.coder-ai/.gitignore" ] || printf 'generated/\nidentity.json\n' > "$1/.coder-ai/.gitignore"
  log "created $f (languages: ${langs:-none detected})"
}

# _sha <string> — sha256 hex (portable).
_sha(){ if command -v sha256sum >/dev/null 2>&1; then printf '%s' "$1" | sha256sum | cut -d' ' -f1
        elif command -v shasum >/dev/null 2>&1; then printf '%s' "$1" | shasum -a 256 | cut -d' ' -f1
        else printf '%s' "$1" | cksum | cut -d' ' -f1; fi; }

# write_fingerprint <repo> — write/verify .coder-ai/identity.json so App A's config can't leak
# into App B: if the fingerprint doesn't match this repo, the generated cache is rebuilt.
write_fingerprint(){
  local p="$1" f root remote id gh rh; f="$1/.coder-ai/identity.json"
  root="$(git -C "$p" rev-parse --show-toplevel 2>/dev/null || true)"; [ -n "$root" ] || root="$(cd "$p" && pwd)"
  remote="$(git -C "$p" config --get remote.origin.url 2>/dev/null || echo none)"
  id="$(basename "$root")"; gh="sha256:$(_sha "$root")"; rh="sha256:$(_sha "$remote")"
  if [ -f "$f" ] && ! grep -q "\"git_root_hash\": \"$gh\"" "$f" 2>/dev/null; then
    log "fingerprint mismatch — this .coder-ai looks copied from another repo; rebuilding for $id"
    rm -rf "$p/.coder-ai/generated"
  fi
  mkdir -p "$1/.coder-ai"
  printf '{\n  "project_id": "%s",\n  "git_root_hash": "%s",\n  "remote_hash": "%s"\n}\n' "$id" "$gh" "$rh" > "$f"
  log "wrote .coder-ai/identity.json (isolation fingerprint: $id)"
}

# write_project_block <repo> — inject your compiled block into the repo's OWN agent files.
# Project-only model: nothing is written to ~. Marker-guarded; preserves existing content.
write_project_block(){
  local p="$1" gen; gen="$1/.coder-ai/generated"
  local a c g
  a="$gen/AGENTS.md"; [ -f "$a" ] || a="$REPO_DIR/build/AGENTS.md"
  c="$gen/CLAUDE.md"; [ -f "$c" ] || c="$REPO_DIR/build/CLAUDE.md"
  g="$gen/GEMINI.md"; [ -f "$g" ] || g="$REPO_DIR/build/GEMINI.md"
  inject "$p/AGENTS.md" "$a" "$MD_BEGIN" "$MD_END" "$MD_BEGIN_OLD" "$MD_END_OLD"
  inject "$p/CLAUDE.md" "$c" "$MD_BEGIN" "$MD_END" "$MD_BEGIN_OLD" "$MD_END_OLD"
  inject "$p/GEMINI.md" "$g" "$MD_BEGIN" "$MD_END" "$MD_BEGIN_OLD" "$MD_END_OLD"
  log "wrote this repo's block -> $p/{AGENTS,CLAUDE,GEMINI}.md"
}

# write_project_settings <repo> — merge guardrail deny-rules + the Stop hook into the repo's
# .claude/settings.json (project-scoped, additive; keeps your defaultMode and other keys). Needs jq.
write_project_settings(){
  local p="$1" f tmp; f="$p/.claude/settings.json"
  reject_symlink "$f" || return 1
  command -v jq >/dev/null 2>&1 || { log "settings: jq not found — skipping $p/.claude/settings.json"; return; }
  mkdir -p "$p/.claude"; [ -f "$f" ] || echo '{}' > "$f"
  tmp="$(mktemp)"
  jq --slurpfile perm "$REPO_DIR/claude/permissions.json" '
    .permissions = (.permissions // {})
    | .permissions.defaultMode = (.permissions.defaultMode // $perm[0].permissions.defaultMode)
    | .permissions.allow = (((.permissions.allow // []) + $perm[0].permissions.allow) | unique)
    | .permissions.deny  = (((.permissions.deny  // []) + $perm[0].permissions.deny)  | unique)
  ' "$f" > "$tmp" && mv "$tmp" "$f"
  local hookf="$p/.coder-ai/generated/project/settings.hooks.json"
  [ -f "$hookf" ] || hookf="$REPO_DIR/build/project/settings.hooks.json"
  if [ -f "$hookf" ]; then
    tmp="$(mktemp)"
    jq --slurpfile hook "$hookf" '
      .hooks = (.hooks // {})
      | reduce ($hook[0].hooks | to_entries[]) as $event (.;
          .hooks[$event.key] = reduce $event.value[] as $candidate
            (.hooks[$event.key] // [];
              if index($candidate) == null then . + [$candidate] else . end))
    ' "$f" > "$tmp" && mv "$tmp" "$f"
  fi
  log "wrote $p/.claude/settings.json (guardrails + Stop hook, merged)"
}

# Remove only snapshot-hook regions installed by older coder-ai-os releases.
remove_legacy_snapshot_hooks(){
  local p="$1" hook hooks cur begin='# >>> coder-ai-os:snapshot >>>' end='# <<< coder-ai-os:snapshot <<<'
  hooks="$(git -C "$p" rev-parse --git-path hooks 2>/dev/null || true)"
  [ -n "$hooks" ] || hooks="$p/.git/hooks"
  case "$hooks" in /*) ;; *) hooks="$p/$hooks";; esac
  [ -d "$hooks" ] || return 0
  cur="$hooks"
  while [ "$cur" != / ]; do
    [ ! -L "$cur" ] || { log "ERROR: refusing symlink hook path: $cur"; return 1; }
    cur="$(dirname "$cur")"
  done
  for hook in post-merge post-checkout post-rewrite post-commit; do
    local f="$hooks/$hook" begins ends tmp mode
    [ -f "$f" ] || continue
    reject_symlink "$f" || return 1
    begins="$(grep -cFx "$begin" "$f" || true)"
    ends="$(grep -cFx "$end" "$f" || true)"
    [ "$begins" -gt 0 ] || continue
    if [ "$begins" -ne "$ends" ]; then
      log "ERROR: malformed legacy snapshot markers; left unchanged: $f"
      return 1
    fi
    mode="$(stat -c '%a' "$f" 2>/dev/null || stat -f '%Lp' "$f" 2>/dev/null || echo 755)"
    tmp="$(mktemp "$hooks/.legacy-hook.XXXXXX")" || return 1
    awk -v begin="$begin" -v end="$end" '
      $0 == begin { managed=1; next }
      $0 == end   { managed=0; next }
      !managed
    ' "$f" > "$tmp"
    chmod "$mode" "$tmp"; mv "$tmp" "$f"
    log "removed retired managed snapshot hook block -> $f"
  done
}

# drop_project <repo-path> — ONE-SHOT per-repo bootstrap ("install and forget").
# Copies the dev protocol + snapshot generator (self-contained, checked in),
# generates the first snapshot and — if CodeGraph is on PATH — builds the local query index. Auto-detects CodeGraph (no flag needed);
# ongoing updates then happen inside the working repo, never here.
drop_project(){
  local proj="$1"
  if [ ! -d "$proj" ]; then log "SKIP --project: not a directory: $proj"; return 1; fi
  local guarded
  for guarded in .coder-ai .ai .claude .codex .cursor .gemini .github scripts AGENTS.md CLAUDE.md GEMINI.md AI_DEV_PROTOCOL.md; do
    reject_symlink "$proj/$guarded" || return 1
  done
  local have_cg=0; command -v codegraph >/dev/null 2>&1 && have_cg=1
  if [ "$DRY_RUN" = 1 ]; then
    log "would copy AI_DEV_PROTOCOL.md + scripts/update-ai-context.sh -> $proj/"
    log "would generate $proj/.ai/PROJECT_SNAPSHOT.md"
    log "would write your block -> $proj/{AGENTS,CLAUDE,GEMINI}.md + .claude/settings.json (guardrails+hook)"
    log "would write Cursor rule + Copilot instructions + .cursor/commands + .codex/{config.toml,skills} + .gemini/commands into $proj"
    log "would write .claude/{commands,agents,skills} into $proj"
    log "would discover code standards -> .ai/standards.md, project navigator, and resumable memory"
    if [ "$have_cg" = 1 ]; then log "would run 'codegraph init' in $proj (CodeGraph detected)"
    else log "would SKIP index (CodeGraph not on PATH; run './install.sh --with-codegraph' once to add it)"; fi
    return
  fi
  remove_legacy_snapshot_hooks "$proj"
  # .coder-ai workspace: this repo's config + isolation fingerprint, then compile the
  # repo-merged block into .coder-ai/generated/ (falls back to the global build/ if no python3).
  ensure_project_yaml "$proj"
  write_fingerprint "$proj"
  if command -v python3 >/dev/null 2>&1 && [ -x "$REPO_DIR/bin/compile" ]; then
    python3 "$REPO_DIR/bin/compile" --project "$proj" >/dev/null 2>&1 \
      && log "compiled this repo -> $proj/.coder-ai/generated/" \
      || log "SKIP repo compile (using global build/ fallback)"
  fi
  local SRC="$proj/.coder-ai/generated"; [ -f "$SRC/AGENTS.md" ] || SRC="$REPO_DIR/build"

  local protocol="$proj/AI_DEV_PROTOCOL.md" legacy="$proj/.ai/legacy/AI_DEV_PROTOCOL.pre-managed.md" ptmp pmode=644
  reject_symlink "$protocol" || return 1
  if [ -f "$protocol" ] && ! grep -qF "$MD_BEGIN" "$protocol" \
     && cmp -s "$protocol" "$REPO_DIR/protocol/AI_DEV_PROTOCOL.md"; then
    mkdir -p "$proj/.ai/legacy"; reject_symlink "$legacy" || return 1
    [ ! -e "$legacy" ] || { log "ERROR: legacy protocol backup already exists: $legacy"; return 1; }
    cp -p "$protocol" "$legacy"
    pmode="$(stat -c '%a' "$protocol" 2>/dev/null || stat -f '%Lp' "$protocol" 2>/dev/null || echo 644)"
    ptmp="$(mktemp "$proj/.AI_DEV_PROTOCOL.XXXXXX")" || return 1
    { printf '%s\n' "$MD_BEGIN"; cat "$REPO_DIR/protocol/AI_DEV_PROTOCOL.md"; printf '%s\n' "$MD_END"; } > "$ptmp"
    chmod "$pmode" "$ptmp"; mv "$ptmp" "$protocol"
    log "migrated legacy protocol; preserved old copy -> $legacy"
  else
    inject "$protocol" "$REPO_DIR/protocol/AI_DEV_PROTOCOL.md" "$MD_BEGIN" "$MD_END" "$MD_BEGIN_OLD" "$MD_END_OLD"
  fi
  mkdir -p "$proj/scripts" "$proj/.ai"
  safe_cp "$REPO_DIR/scripts/update-ai-context.sh" "$proj/scripts/update-ai-context.sh"
  chmod +x "$proj/scripts/update-ai-context.sh"
  log "dropped AI_DEV_PROTOCOL.md + scripts/update-ai-context.sh -> $proj/"
  # Generate the first snapshot from the project root (script uses $(pwd)).
  ( cd "$proj" && bash scripts/update-ai-context.sh ) \
    && log "generated $proj/.ai/PROJECT_SNAPSHOT.md" \
    || log "SKIP snapshot (generator returned non-zero)"
  # Symbol indexes for the synced scope (L1): sync IS the scope declaration, so every
  # unit the snapshot lists gets its index (function/class -> file:line), root included.
  ( cd "$proj" && bash scripts/update-ai-context.sh --symbols-all ) >/dev/null 2>&1 \
    && log "generated $proj/.ai/symbols/ code atlas ($(find "$proj/.ai/symbols" -type f -name '*.md' 2>/dev/null | wc -l | tr -d ' ') .md maps: INDEX -> unit -> folder, import graphs + symbol tables; each file mapped once)" \
    || log "SKIP symbol indexes (generator returned non-zero)"
  # Learn the repo's own code style so agents write matching code.
  safe_cp "$REPO_DIR/scripts/discover-standards.sh" "$proj/scripts/discover-standards.sh"
  chmod +x "$proj/scripts/discover-standards.sh"
  ( cd "$proj" && bash scripts/discover-standards.sh ) \
    && log "discovered code standards -> $proj/.ai/standards.md" \
    || log "SKIP standards (generator returned non-zero)"
  if [ -f "$SRC/project/PROJECT_NAVIGATOR.md" ]; then
    seed_cp "$SRC/project/PROJECT_NAVIGATOR.md" "$proj/.ai/PROJECT_NAVIGATOR.md"
  fi
  # Your personal block, written into THIS repo's own agent files (Claude/Codex/Gemini read them).
  write_project_block "$proj"
  # Repo-scoped adapters: Cursor rule (our own dedicated file) + Copilot instructions
  # (marker block, so an existing file is preserved).
  if [ -f "$SRC/cursor.mdc" ]; then
    mkdir -p "$proj/.cursor/rules"
    safe_cp "$SRC/cursor.mdc" "$proj/.cursor/rules/coder-ai-os.mdc"
    log "wrote $proj/.cursor/rules/coder-ai-os.mdc"
  fi
  if [ -f "$SRC/copilot-instructions.md" ]; then
    mkdir -p "$proj/.github"
    inject "$proj/.github/copilot-instructions.md" "$SRC/copilot-instructions.md" "$MD_BEGIN" "$MD_END" "$MD_BEGIN_OLD" "$MD_END_OLD"
    log "updated $proj/.github/copilot-instructions.md"
  fi
  # Repo-scoped harness artifacts: commands, subagents, skills (+ HOOKS/MCP notes) and a memory dir.
  if [ -d "$SRC/project/.claude" ]; then
    mkdir -p "$proj/.claude"
    safe_cp_tree "$SRC/project/.claude" "$proj/.claude"
    log "wrote .claude/{commands,agents,skills} into $proj (kept any of your own same-named)"
  fi
  if [ -d "$SRC/commands/cursor" ]; then
    for f in "$SRC/commands/cursor/"*; do [ -f "$f" ] && safe_cp "$f" "$proj/.cursor/commands/$(basename "$f")"; done
    log "wrote .cursor/commands into $proj (kept any of your own same-named)"
  fi
  # Project-scoped skills/commands for the other tools (Codex reads .agents/skills; Gemini .gemini/commands).
  write_codex_config "$proj/.codex/config.toml"
  [ -d "$SRC/skills/codex" ] && { safe_cp_tree "$SRC/skills/codex" "$proj/.agents/skills"; log "wrote .agents/skills into $proj"; }
  [ -d "$SRC/project/.codex" ] && { safe_cp_tree "$SRC/project/.codex" "$proj/.codex"; log "wrote .codex/agents into $proj"; }
  if [ -d "$SRC/commands/gemini" ]; then
    for f in "$SRC/commands/gemini/"*; do [ -f "$f" ] && safe_cp "$f" "$proj/.gemini/commands/$(basename "$f")"; done
    log "wrote .gemini/commands into $proj (kept any of your own same-named)"
  fi
  # Guardrails (deny-rules) + Stop hook -> the repo's own .claude/settings.json (project-scoped).
  write_project_settings "$proj"
  if [ -f "$SRC/project/MCP.md" ]; then
    local mcp_target="$proj/.ai/MCP.md" mcp_legacy
    mcp_legacy="$(mktemp)"
    sed '1{/coder-ai-os:generated/d;}' "$SRC/project/MCP.md" > "$mcp_legacy"
    if [ -f "$mcp_target" ] && cmp -s "$mcp_target" "$mcp_legacy"; then
      reject_symlink "$mcp_target" || { rm -f "$mcp_legacy"; return 1; }
      cp "$SRC/project/MCP.md" "$mcp_target"; log "migrated generated MCP notes -> $mcp_target"
    else safe_cp "$SRC/project/MCP.md" "$mcp_target"; fi
    rm -f "$mcp_legacy"
  fi
  # Portable, cross-agent memory (markdown, git-committed — no DB). Seed templates if absent.
  mkdir -p "$proj/.ai/memory"
  [ -f "$proj/.ai/memory/INDEX.md" ] || cat > "$proj/.ai/memory/INDEX.md" <<'EOF'
# Memory index — durable facts & decisions (read this FIRST; portable across all agents)

One line per memory below: `- [title](file.md) — hook`. Add a file per durable fact, decision,
or gotcha. Any agent (Claude/Codex/Cursor) reads and appends here, so nothing is lost on switch.
EOF
  [ -f "$proj/.ai/memory/CURRENT.md" ] || cat > "$proj/.ai/memory/CURRENT.md" <<'EOF'
# Current task — where we left off (any agent resumes from here)

## Goal

## Original intent

## Definition of done

## Active epic/task

## In progress

## Next

## Decisions

## Files changed

## Blockers

## Validation evidence (command + observed)

## Updated

EOF
  log "seeded .ai/memory/{INDEX,CURRENT}.md (cross-agent, resumable)"
  scaffold_monorepo "$proj"
  if [ "$have_cg" = 1 ]; then
    ( cd "$proj" && codegraph init ) \
      && log "built CodeGraph index -> $proj/.codegraph/ (auto-syncs on change)" \
      || log "SKIP codegraph init (returned non-zero)"
  else
    log "no CodeGraph index (optional) — snapshot is the fallback; add it with './install.sh --with-codegraph'"
  fi
  # Setup writes its last batch of files (AGENTS/CLAUDE/GEMINI.md, .claude/, .codex/, .cursor/,
  # .github/, .codegraph/) AFTER the first snapshot above, and the structural fingerprint now
  # counts untracked files — so re-sync the snapshot and the atlas INDEX once everything is on
  # disk. Without this, a brand-new setup reports "snapshot STALE" on its very first
  # --check / doctor run, and agents distrust a map that is actually correct.
  ( cd "$proj" && bash scripts/update-ai-context.sh && bash scripts/update-ai-context.sh --symbols . ) >/dev/null 2>&1 \
    && log "re-synced snapshot + atlas INDEX (fresh for --check / doctor)" \
    || log "SKIP final re-sync (generator returned non-zero)"
}

# main — the install flow. Wrapped so the script can be sourced (e.g. for tests)
# without executing anything; it runs only when invoked directly.
main(){
# --status: just run the compiler's budget/drift doctor and exit.
if [ "$STATUS" = 1 ]; then
  if command -v python3 >/dev/null 2>&1 && [ -x "$REPO_DIR/bin/compile" ]; then
    exec python3 "$REPO_DIR/bin/compile" --check
  fi
  echo "python3 not found — cannot run compiler status."; exit 1
fi

# ── PROJECT branch (repo-local; never touches ~) ────────────────────────────
# `--project` writes THIS repo's config into the repo. Nothing global happens here.
if [ -n "$PROJECT" ]; then
  [ -d "$PROJECT" ] || { echo "coder-ai-os: no such directory: $PROJECT" >&2; exit 2; }
  PROJECT="$(cd "$PROJECT" && pwd -P)"; ACTIVE_PROJECT="$PROJECT"
  if [ "$DRY_RUN" = 1 ]; then echo "coder-ai-os: setting up $PROJECT (dry-run)"; else echo "coder-ai-os: setting up $PROJECT"; fi
  drop_project "$PROJECT"
  cat <<EOF

Done — $PROJECT carries its own config now (repo-local; nothing written to ~).
Open Claude / Codex / Cursor / Gemini in that repo.
  coder-ai-os verify     # run inside $PROJECT to confirm what's wired
EOF
  return 0
fi

# ── GLOBAL branch (your REUSABLE behavior only — how you work, NO project data) ──
if [ "$DRY_RUN" = 0 ] && [ "$YES" = 0 ] && [ -t 0 ] && [ ! -f "$REPO_DIR/config/local.yaml" ]; then
  printf 'Welcome to coder-ai-os. Personalize your profile now? (recommended) [Y/n]: '
  read -r _a || _a=""
  case "${_a:-y}" in n|N|no|No) echo "Using defaults…" ;; *) exec "$REPO_DIR/bin/setup" ;; esac
fi
if [ "$DRY_RUN" = 1 ]; then echo "coder-ai-os: installing your global behavior (dry-run)"; else echo "coder-ai-os: installing your global behavior — how you work (no project data)"; fi
compile_step
inject "$HOME/.claude/CLAUDE.md" "$(tool_body CLAUDE.md)" "$MD_BEGIN" "$MD_END" "$MD_BEGIN_OLD" "$MD_END_OLD"
inject "$HOME/.codex/AGENTS.md"  "$(tool_body AGENTS.md)" "$MD_BEGIN" "$MD_END" "$MD_BEGIN_OLD" "$MD_END_OLD"
inject "$HOME/.gemini/GEMINI.md" "$(tool_body GEMINI.md)" "$MD_BEGIN" "$MD_END" "$MD_BEGIN_OLD" "$MD_END_OLD"
[ "$CODEX_DEFAULT" = 1 ] && write_codex_config "$HOME/.codex/config.toml"
install_cmds claude "$HOME/.claude/commands" Claude
install_cmds gemini "$HOME/.gemini/commands"  Gemini
install_skills(){ local src="$REPO_DIR/build/skills/$1" dest="$2"; [ -d "$src" ] || return 0; if [ "$DRY_RUN" = 1 ]; then log "would install $3 skills -> $dest"; return; fi; safe_cp_tree "$src" "$dest" && log "installed $3 skills -> $dest"; }
install_skills claude "$HOME/.claude/skills" Claude
install_skills codex  "$HOME/.agents/skills"  Codex
merge_claude_settings

# 6. Make the `coder-ai-os` command runnable (the README/docs use it by name).
if [ "$DRY_RUN" = 0 ] && ! command -v coder-ai-os >/dev/null 2>&1; then
  if mkdir -p "$HOME/.local/bin" 2>/dev/null && ln -sf "$REPO_DIR/bin/coder-ai-os" "$HOME/.local/bin/coder-ai-os" 2>/dev/null; then
    log "linked the 'coder-ai-os' command -> ~/.local/bin"
    case ":$PATH:" in *":$HOME/.local/bin:"*) : ;; *) log "  add ~/.local/bin to PATH:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;; esac
  else
    log "tip: run the CLI as ./bin/coder-ai-os, or add it to PATH:  export PATH=\"$REPO_DIR/bin:\$PATH\""
  fi
fi

cat <<'EOF'

Done — your global behavior is installed (guardrails · reply format · workflow · generic skills).
This is "how you work": it carries NO project data, so it never conflicts between apps.
Next, set up each project (its languages, standards, commands stay in the repo):
    cd ~/your-app && coder-ai-os setup
EOF
}

# Run only when executed directly; a `source` for testing loads the functions only.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then main; fi
