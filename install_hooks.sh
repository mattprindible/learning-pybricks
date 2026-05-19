#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK_SRC="$REPO_ROOT/hooks/post-commit"
HOOK_DST="$REPO_ROOT/.git/hooks/post-commit"

chmod +x "$HOOK_SRC"
ln -sf "$HOOK_SRC" "$HOOK_DST"
echo "Installed: .git/hooks/post-commit -> hooks/post-commit"
