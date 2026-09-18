#!/usr/bin/env bash
# Cursor has no plugin system, so the hook is registered by hand. Claude Code
# and Codex both install this repo as a plugin instead; see the README.
#
#   scripts/install-cursor.sh            -> ~/.cursor/hooks.json
#   scripts/install-cursor.sh <project>  -> <project>/.cursor/hooks.json
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
dest="${1:+$1/.cursor}"
dest="${dest:-$HOME/.cursor}"
cfg="$dest/hooks.json"

block="$(sed "s|REDPEN_DIR|$root|" "$root/hooks/cursor-hooks.json")"

if [ -e "$cfg" ]; then
  echo "$cfg already exists, so nothing was written. Merge this in:"
  echo
  echo "$block"
  exit 0
fi

mkdir -p "$dest"
printf '%s\n' "$block" > "$cfg"
echo "Wrote $cfg. Restart Cursor."
