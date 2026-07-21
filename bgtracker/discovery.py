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


def _appdata_hs_dir(prefix: Path) -> Path:
    """Pick the AppData Hearthstone dir where log.config belongs.

    Prefer a user that already has Blizzard AppData; never pick Public.
    """
    users = prefix / "drive_c" / "users"
    candidates = [u for u in sorted(users.iterdir()) if u.is_dir() and u.name != "Public"]
    if not candidates:
        raise FileNotFoundError(f"no wine users under {users}")

    def score(user: Path) -> tuple:
        blizz = user / "AppData" / "Local" / "Blizzard"
        return ((blizz / "Hearthstone").is_dir(), blizz.is_dir())

    user = max(candidates, key=score)
    return user / "AppData" / "Local" / "Blizzard" / "Hearthstone"


def ensure_log_config(hs_dir: Path) -> tuple[Path, bool]:
    """Ensure log.config has the Power/Bob sections. Returns (path, changed).

    Merges into an existing file without touching other sections. A True
    `changed` result means Hearthstone must be restarted to pick it up.
    """
    target = _appdata_hs_dir(wine_prefix_of(hs_dir)) / "log.config"
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
