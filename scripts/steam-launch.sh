#!/usr/bin/env bash
# Steam launch wrapper: start the BG tracker+overlay alongside the game and
# stop it when the game session ends.
#
# Steam shortcut > Properties > Launch Options:
#   /home/mrjohan/hs-bg-tracker/scripts/steam-launch.sh %command%
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/hs-bg-tracker"
mkdir -p "$LOG_DIR"

TRACKER_PID=""
if ! pgrep -f "python -m bgtracker" >/dev/null 2>&1; then
    "$REPO/.venv/bin/python" -m bgtracker --overlay >"$LOG_DIR/tracker.log" 2>&1 &
    TRACKER_PID=$!
    echo "$(date -Is) started tracker (pid $TRACKER_PID)" >>"$LOG_DIR/launch.log"
fi

cleanup() {
    if [ -n "$TRACKER_PID" ] && kill -0 "$TRACKER_PID" 2>/dev/null; then
        # the tracker re-execs itself for the overlay preload; kill the tree
        pkill -TERM -P "$TRACKER_PID" 2>/dev/null
        kill -TERM "$TRACKER_PID" 2>/dev/null
        pkill -TERM -f "python -m bgtracker" 2>/dev/null
        echo "$(date -Is) stopped tracker" >>"$LOG_DIR/launch.log"
    fi
}
trap cleanup EXIT

# Run the actual game command Steam gave us; wrapper lives as long as it does.
"$@"
