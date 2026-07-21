"""`bgtracker doctor` — diagnose the tracking pipeline end to end."""

from __future__ import annotations

import asyncio
import shutil

from bgtracker import discovery
from bgtracker.config import CONFIG_FILE, load_config
from bgtracker.logwatch.session import newest_session_dir, power_log_path
from bgtracker.sim.client import SIDECAR_DIR, SimClient

OK = "  [ok] "
BAD = "  [!!] "
INFO = "       "


def _check_install(cfg) -> object | None:
    print("install:")
    installs = discovery.find_hearthstone_dirs()
    for hs in installs:
        print(f"{INFO}found: {hs}")
    if cfg.hearthstone_dir:
        print(f"{INFO}config override: {cfg.hearthstone_dir}")
    try:
        hs_dir = discovery.find_hearthstone_dir(cfg.hearthstone_dir)
    except FileNotFoundError as e:
        print(f"{BAD}{e}")
        return None
    print(f"{OK}using: {hs_dir}")
    return hs_dir


def _check_log_config(hs_dir) -> None:
    print("logging config:")
    candidates = discovery._candidate_appdata_dirs(hs_dir)
    used = [c for c in candidates if (c / "options.txt").is_file()]
    for c in used:
        print(f"{INFO}game config home (has options.txt): {c}")
    if not used:
        print(f"{BAD}no game-created AppData dir found (game never run?) — "
              "log.config target is a guess until first launch")
    target = discovery._appdata_hs_dir(hs_dir) / "log.config"
    if not target.is_file():
        print(f"{BAD}log.config missing at {target} — run `python -m bgtracker` once to create it")
        return
    data = target.read_bytes()
    problems = []
    if b"[Power]" not in data:
        problems.append("missing [Power] section")
    if b"FilePrinting=True" not in data:
        problems.append("FilePrinting=True not set")
    if b"\r\n" not in data:
        problems.append("LF-only line endings (client wants CRLF)")
    if problems:
        print(f"{BAD}log.config at {target}: " + "; ".join(problems))
    else:
        print(f"{OK}log.config good: {target}")


def _check_sessions(hs_dir) -> None:
    print("log sessions:")
    session = newest_session_dir(hs_dir / "Logs")
    if session is None:
        print(f"{BAD}no session dirs under {hs_dir / 'Logs'} — has the game ever run?")
        return
    print(f"{INFO}newest session: {session.name}")
    power = power_log_path(session)
    if power.is_file():
        print(f"{OK}Power.log present ({power.stat().st_size} bytes)")
    else:
        print(f"{BAD}no Power.log in newest session — if the game is running, "
              "logging is not active (config missing/misplaced at client startup, "
              "or client not restarted since it was written)")


async def _check_sim(cfg) -> None:
    print("combat simulator:")
    if shutil.which("node") is None:
        print(f"{BAD}node not found on PATH")
        return
    if not (SIDECAR_DIR / "node_modules").is_dir():
        print(f"{BAD}sidecar deps missing — run: cd {SIDECAR_DIR} && npm install")
        return
    sim = SimClient(timeout=cfg.sim_timeout)
    try:
        if await sim.ping():
            print(f"{OK}sidecar responds")
        else:
            print(f"{BAD}sidecar did not respond to ping")
    finally:
        await sim.close()


def _check_overlay() -> None:
    print("overlay:")
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        print(f"{OK}GTK4 + gtk4-layer-shell available")
    except Exception as exc:
        print(f"{BAD}overlay deps unavailable ({exc}) — "
              "install: sudo pacman -S python-gobject gtk4 gtk4-layer-shell")


def run() -> None:
    cfg = load_config()
    print(f"config file: {CONFIG_FILE} ({'present' if CONFIG_FILE.is_file() else 'defaults'})")
    hs_dir = _check_install(cfg)
    if hs_dir is not None:
        _check_log_config(hs_dir)
        _check_sessions(hs_dir)
    asyncio.run(_check_sim(cfg))
    _check_overlay()
