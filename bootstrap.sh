#!/bin/sh
# coder-ai-os — one-line installer. Clones (or updates) the repo and runs install.sh.
#
#   curl -fsSL https://raw.githubusercontent.com/ampaking/coder-ai-os/main/bootstrap.sh | sh
#
# Override the source or destination with env vars if you forked or want a custom path:
#   CODER_AI_OS_REPO=https://github.com/you/coder-ai-os.git CODER_AI_OS_HOME=~/tools/cos  ... | sh
set -eu

REPO="${CODER_AI_OS_REPO:-https://github.com/ampaking/coder-ai-os.git}"
DEST="${CODER_AI_OS_HOME:-$HOME/coder-ai-os}"

command -v git >/dev/null 2>&1 || { echo "coder-ai-os: git is required — install git and retry." >&2; exit 1; }

if [ -d "$DEST/.git" ]; then
  echo "coder-ai-os: updating $DEST"
  git -C "$DEST" pull --ff-only --quiet 2>/dev/null || echo "coder-ai-os: (using current checkout — no fast-forward)"
else
  echo "coder-ai-os: cloning into $DEST"
  git clone --depth 1 "$REPO" "$DEST" >/dev/null 2>&1 \
    || { echo "coder-ai-os: clone failed from $REPO — set CODER_AI_OS_REPO to your repo URL." >&2; exit 1; }
fi

cd "$DEST"
echo "coder-ai-os: installing sensible defaults…"
./install.sh

cat <<'EOF'

────────────────────────────────────────────────
Done — every agent is configured with defaults.
Personalize anytime (interactive):
    coder-ai-os init          (or: ~/coder-ai-os/install.sh --init)
Confirm it all loaded:
    coder-ai verify
────────────────────────────────────────────────
EOF
