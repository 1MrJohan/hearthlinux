#!/usr/bin/env bash
# Snapshot the current Hearthstone session's Power.log into tests/fixtures/.
# Usage: scripts/capture-fixture.sh <name>   (e.g. normal-game, reconnect)
set -euo pipefail

name="${1:?usage: capture-fixture.sh <fixture-name>}"
repo="$(cd "$(dirname "$0")/.." && pwd)"

# Ask the tracker's own discovery rather than keeping a second glob list here:
# this script had already drifted from discovery._PREFIX_GLOBS once (it missed
# the Bottles and ~/.steam layouts the tracker itself supports).
py="$repo/.venv/bin/python"
[ -x "$py" ] || py=python3
mapfile -t logs_dirs < <(PYTHONPATH="$repo" "$py" - <<'PYEOF'
from bgtracker.discovery import find_hearthstone_dirs
for hs in find_hearthstone_dirs():
    print(hs / "Logs")
PYEOF
)

newest=""
for d in "${logs_dirs[@]}"; do
    [ -d "$d" ] || continue
    for s in "$d"/Hearthstone_*/; do
        [ -f "$s/Power.log" ] || continue
        # An `if`, not a `[ ] || [ ] && ...` list: under `set -e` that list
        # evaluating false as the last command of the loop body silently
        # exited the whole script whenever the newest dir didn't sort last.
        if [ -z "$newest" ] || [ "$s" -nt "$newest" ]; then
            newest="$s"
        fi
    done
done

[ -n "$newest" ] || { echo "no session dir with Power.log found — is logging enabled?" >&2; exit 1; }

dest="$repo/tests/fixtures/$name.power.log"
cp "$newest/Power.log" "$dest"
echo "captured $(du -h "$dest" | cut -f1) from $newest -> $dest"
