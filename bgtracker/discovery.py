"""Locate the Hearthstone install across Wine/Proton prefix layouts and
bootstrap the log.config that enables Power/Bob logging."""

from __future__ import annotations

import glob
import logging
from pathlib import Path

log = logging.getLogger(__name__)

HS_SUBPATH = "drive_c/Program Files (x86)/Hearthstone"

# Candidate wine-prefix roots (each glob resolves to prefix dirs containing
# drive_c). Steam compatdata prefixes hold the prefix under `pfx`.
_PREFIX_GLOBS = [
    "~/.local/share/Steam/steamapps/compatdata/*/pfx",
    "~/.steam/steam/steamapps/compatdata/*/pfx",
    "~/Games/*",
    "~/.var/app/com.usebottles.bottles/data/bottles/bottles/*",
]

# Mirror HDT's generated file exactly: full key set, capitalized booleans,
# CRLF line endings. The HS client's parser is picky — a partial/LF-only file
# has been observed to break ALL logging (even the defaults).
_CHANNEL = {
    "LogLevel": "1",
    "FilePrinting": "True",
    "ConsolePrinting": "False",
    "ScreenPrinting": "False",
    "Verbose": "True",
}
LOG_CONFIG_SECTIONS = {
    "Power": dict(_CHANNEL),
    "Zone": dict(_CHANNEL),
    "Bob": dict(_CHANNEL),
}


def find_hearthstone_dirs() -> list[Path]:
    """All Hearthstone install dirs found across known prefix layouts."""
    found: list[Path] = []
    for pattern in _PREFIX_GLOBS:
        for prefix in sorted(glob.glob(str(Path(pattern).expanduser()))):
            hs = Path(prefix) / HS_SUBPATH
            if hs.is_dir() and hs not in found:
                found.append(hs)
    return found


def _logs_mtime(hs_dir: Path) -> float:
    logs = hs_dir / "Logs"
    if not logs.is_dir():
        return 0.0
    times = [d.stat().st_mtime for d in logs.glob("Hearthstone_*") if d.is_dir()]
    return max(times, default=logs.stat().st_mtime)


def find_hearthstone_dir(override: Path | None = None) -> Path:
    """Best Hearthstone install dir: config override, else most recently played."""
    if override:
        if not override.is_dir():
            raise FileNotFoundError(f"configured hearthstone_dir does not exist: {override}")
        return override
    candidates = find_hearthstone_dirs()
    if not candidates:
        raise FileNotFoundError(
            "No Hearthstone install found. Set hearthstone_dir in "
            "~/.config/hs-bg-tracker/config.toml to the folder containing Logs/."
        )
    best = max(candidates, key=_logs_mtime)
    log.info("Using Hearthstone install: %s", best)
    return best


def wine_prefix_of(hs_dir: Path) -> Path:
    """Walk up from the install dir to the prefix root (dir containing drive_c)."""
    for parent in hs_dir.parents:
        if (parent / "drive_c").is_dir():
            return parent
    raise FileNotFoundError(f"no drive_c above {hs_dir}")


def _candidate_appdata_dirs(hs_dir: Path) -> list[Path]:
    """AppData/.../Blizzard/Hearthstone candidates across ALL known prefixes.

    The game's config home is not necessarily in the install's own prefix:
    Steam shortcuts launch an external install (via Z:\\) while the game's
    AppData lives in the shortcut's compatdata prefix. The dir the game
    actually uses contains options.txt — prefer that, most recent first.
    """
    prefixes: list[Path] = []
    for pattern in _PREFIX_GLOBS:
        prefixes.extend(Path(p) for p in glob.glob(str(Path(pattern).expanduser())))
    try:
        install_prefix = wine_prefix_of(hs_dir)
        if install_prefix not in prefixes:
            prefixes.append(install_prefix)
    except FileNotFoundError:
        pass

    candidates: list[Path] = []
    for prefix in prefixes:
        users = prefix / "drive_c" / "users"
        if not users.is_dir():
            continue
        for user in sorted(users.iterdir()):
            if not user.is_dir() or user.is_symlink() or user.name == "Public":
                continue
            candidates.append(user / "AppData" / "Local" / "Blizzard" / "Hearthstone")
    return candidates


def _appdata_hs_dir(hs_dir: Path) -> Path:
    candidates = _candidate_appdata_dirs(hs_dir)
    if not candidates:
        raise FileNotFoundError(f"no wine user profiles found for {hs_dir}")
    # Game-created dirs (options.txt present) win, newest activity first.
    used = [c for c in candidates if (c / "options.txt").is_file()]
    if used:
        return max(used, key=lambda c: (c / "options.txt").stat().st_mtime)
    existing = [c for c in candidates if c.is_dir()]
    if existing:
        return existing[0]
    return candidates[0]


def ensure_log_config(hs_dir: Path) -> tuple[Path, bool]:
    """Ensure log.config has the Power/Bob sections. Returns (path, changed).

    Merges into an existing file without touching other sections. A True
    `changed` result means Hearthstone must be restarted to pick it up.
    """
    target = _appdata_hs_dir(hs_dir) / "log.config"
    sections: dict[str, dict[str, str]] = {}
    order: list[str] = []
    if target.is_file():
        current = None
        for line in target.read_text().splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1]
                sections.setdefault(current, {})
                order.append(current)
            elif "=" in line and current is not None:
                key, _, value = line.partition("=")
                sections[current][key.strip()] = value.strip()

    changed = False
    for name, wanted in LOG_CONFIG_SECTIONS.items():
        sec = sections.setdefault(name, {})
        if name not in order:
            order.append(name)
        for key, value in wanted.items():
            if sec.get(key) != value:
                sec[key] = value
                changed = True

    if changed:
        target.parent.mkdir(parents=True, exist_ok=True)
        out = []
        for name in order:
            out.append(f"[{name}]")
            out.extend(f"{k}={v}" for k, v in sections[name].items())
        out.append("")
        target.write_text("\r\n".join(out))
        log.info("Wrote %s (restart Hearthstone to enable logging)", target)
    return target, changed
