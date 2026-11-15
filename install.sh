#!/usr/bin/env bash
# Link `weather` into ~/.local/bin and install dev dependencies. Safe to re-run.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v bun >/dev/null || { echo "bun is required: https://bun.sh" >&2; exit 1; }
mkdir -p "$HOME/.local/bin"
ln -sf "$ROOT/bin/weather" "$HOME/.local/bin/weather"
echo "linked $HOME/.local/bin/weather -> $ROOT/bin/weather"
