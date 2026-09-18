#!/usr/bin/env bash
# Install redpen for Codex or Cursor. Claude Code users want the plugin instead:
#   /plugin marketplace add Kakhramon/prompt-redpen
set -euo pipefail

host="${1:-}"
case "$host" in
  codex|cursor) ;;
  *) echo "usage: install.sh codex|cursor" >&2; exit 2 ;;
esac

src="$(cd "$(dirname "$0")/.." && pwd)/plugins/redpen/scripts"
dest="$HOME/.redpen/scripts"
mkdir -p "$dest"
cp "$src"/redpen.py "$src"/secret_scan.py "$dest/"

if [ "$host" = codex ]; then
  cfg="$HOME/.codex/hooks.json"
else
  cfg="$HOME/.cursor/hooks.json"
fi
mkdir -p "$(dirname "$cfg")"
if [ -e "$cfg" ]; then
  echo "$cfg already exists. Merge this in by hand:"
  echo
  cat "$(dirname "$0")/$host-hooks.json"
  exit 0
fi
cp "$(dirname "$0")/$host-hooks.json" "$cfg"
echo "Installed. Scripts in $dest, hook in $cfg."
if [ "$host" = codex ]; then
  echo "Codex will ask you to trust the hook: run /hooks and approve it."
fi
