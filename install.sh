#!/usr/bin/env bash
# Link `weather` into ~/.local/bin. Needs python3 >= 3.10 and a tz database (OS tzdata). Safe to re-run.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v python3 >/dev/null || { echo "python3 >= 3.10 is required" >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "python3 >= 3.10 is required (found $(python3 -V 2>&1))" >&2; exit 1; }
python3 -c 'from zoneinfo import ZoneInfo; ZoneInfo("UTC")' 2>/dev/null \
  || { echo "no tz database found: install your OS tz data (tzdata) or run: python3 -m pip install tzdata" >&2; exit 1; }
mkdir -p "$HOME/.local/bin"
ln -sf "$ROOT/bin/weather" "$HOME/.local/bin/weather"
echo "linked $HOME/.local/bin/weather -> $ROOT/bin/weather"
