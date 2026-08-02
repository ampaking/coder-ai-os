#!/usr/bin/env bash
# coder-ai-os status line — model · dir · live context % (harness-measured, never model-guessed).
# Claude Code pipes session JSON on stdin; whatever this prints becomes the status line.
# Runs on every status refresh, so it spawns exactly one jq. Installed to ~/.claude/statusline.sh
# only if absent; your own script is never overwritten.
input=$(cat)
command -v jq >/dev/null 2>&1 || { echo "coder-ai-os"; exit 0; }
line=$(printf '%s' "$input" | jq -r \
  '[.model.display_name // "model?", .workspace.current_dir // ".",
    ((.context_window.used_percentage // 0) | floor)] | @tsv' 2>/dev/null) || line=''
IFS=$'\t' read -r MODEL DIR PCT <<< "$line"
echo "[${MODEL:-model?}] ${DIR##*/} | ctx ${PCT:-0}%"
