#!/usr/bin/env bash
# Run the BG tracker+overlay for exactly as long as Hearthstone is up.
#
# Steam shortcut > Properties > Launch Options:
#   /home/mrjohan/hs-bg-tracker/scripts/steam-launch.sh %command%
#
# Also works as a plain watcher with no arguments (Lutris, Bottles, or a
# hand-launched game):
#   scripts/steam-launch.sh &
#
# Why a wrapper and not a systemd user service: the overlay needs
# WAYLAND_DISPLAY, XDG_RUNTIME_DIR and DISPLAY (the hover poll talks to
# XWayland), plus the gtk4-layer-shell LD_PRELOAD. A child of the game launch
# inherits the whole graphical environment for free; a user unit does not.
#
# The tracker follows Hearthstone.exe rather than this wrapper, because the
# wrapper's lifetime is the Battle.net session — which spans time spent at the
# launcher and outlives closing Hearthstone. Quitting and reopening the game
# within one session works: the watcher keeps running.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/hs-bg-tracker"
PY="$REPO/.venv/bin/python"
PIDFILE="$LOG_DIR/tracker.pid"
POLL="${BGTRACKER_POLL:-2}"
mkdir -p "$LOG_DIR"

log() { echo "$(date -Is) $*" >>"$LOG_DIR/launch.log"; }

# Match the process NAME, never the command line: Wine sets comm to the exe
# name (steam.exe, Battle.net.exe, …), and "Hearthstone.exe" is exactly the
# 15 characters comm allows. A cmdline match would also hit any shell, launcher
# or editor that merely mentions the game, and the watcher would believe it was
# running forever. Override for a different build/name if ever needed.
GAME_PROC="${BGTRACKER_GAME_PROC:-Hearthstone.exe}"

hearthstone_running() {
    pgrep -x "$GAME_PROC" >/dev/null 2>&1
}

tracker_pid() { [ -f "$PIDFILE" ] && cat "$PIDFILE" 2>/dev/null || true; }

tracker_alive() {
    local p
    p="$(tracker_pid)"
    [ -n "$p" ] && kill -0 "$p" 2>/dev/null
}

start_tracker() {
    # --replace guarantees this game gets a tracker running current code, and
    # that two overlays never end up stacked on screen.
    "$PY" -m bgtracker --overlay --replace >>"$LOG_DIR/tracker.log" 2>&1 &
    echo $! >"$PIDFILE"
    log "started tracker (pid $(tracker_pid)) — Hearthstone up"
}

stop_tracker() {
    local p
    p="$(tracker_pid)"
    if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then
        # The tracker execve()s itself for the layer-shell preload, which keeps
        # the pid, so this reaches the real process.
        kill -TERM "$p" 2>/dev/null
        log "stopped tracker (pid $p) — Hearthstone gone"
    fi
    rm -f "$PIDFILE"
}

watch_game() {
    while true; do
        if hearthstone_running; then
            tracker_alive || start_tracker   # also recovers from a crash
        else
            tracker_alive && stop_tracker
        fi
        sleep "$POLL"
    done
}

cleanup() {
    [ -n "${WATCHER:-}" ] && kill "$WATCHER" 2>/dev/null
    stop_tracker
}
trap cleanup EXIT INT TERM

watch_game &
WATCHER=$!

if [ "$#" -gt 0 ]; then
    "$@"              # Steam's %command%; the wrapper lives as long as it does
else
    wait "$WATCHER"   # standalone watcher: run until interrupted
fi
