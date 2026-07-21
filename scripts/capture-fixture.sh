#!/usr/bin/env bash
# Snapshot the current Hearthstone session's Power.log into tests/fixtures/.
# Usage: scripts/capture-fixture.sh <name>   (e.g. normal-game, reconnect)
set -euo pipefail

name="${1:?usage: capture-fixture.sh <fixture-name>}"
repo="$(cd "$(dirname "$0")/.." && pwd)"

logs_dirs=(
    "$HOME"/.local/share/Steam/steamapps/compatdata/*/pfx/"drive_c/Program Files (x86)/Hearthstone/Logs"
    "$HOME"/Games/*/"drive_c/Program Files (x86)/Hearthstone/Logs"
)

newest=""
for d in "${logs_dirs[@]}"; do
    [ -d "$d" ] || continue
    for s in "$d"/Hearthstone_*/; do
        [ -f "$s/Power.log" ] || continue
        [ -z "$newest" ] || [ "$s" -nt "$newest" ] && newest="$s"
    done
done

[ -n "$newest" ] || { echo "no session dir with Power.log found — is logging enabled?" >&2; exit 1; }

dest="$repo/tests/fixtures/$name.power.log"
cp "$newest/Power.log" "$dest"
echo "captured $(du -h "$dest" | cut -f1) from $newest -> $dest"
