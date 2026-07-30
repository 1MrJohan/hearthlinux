"""Configuration loading and XDG paths.

`Config`'s fields *are* the set of recognised config keys — `load_config` walks
`dataclasses.fields` rather than a hand-kept list, so adding a setting means
adding one field here and one `Setting` in `settings.py` (a test enforces that
the two agree). `extra` collects whatever is left, which in practice is only the
machine-written `pos_<panel>_x/y` panel positions.
"""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_cache_path, user_config_path, user_data_path

APP_NAME = "hs-bg-tracker"

CONFIG_DIR = user_config_path(APP_NAME)
DATA_DIR = user_data_path(APP_NAME)
CACHE_DIR = user_cache_path(APP_NAME)
CONFIG_FILE = CONFIG_DIR / "config.toml"


@dataclass
class Config:
    # -- game & logs ----------------------------------------------------
    # Explicit Hearthstone install dir (the folder containing Logs/); None = auto-discover
    hearthstone_dir: Path | None = None
    # Log poll intervals in seconds
    poll_active: float = 0.25
    poll_idle: float = 2.0
    # Log-session cleanup: prune Logs/Hearthstone_* dirs older than this,
    # but always keep at least log_keep_min newest. 0 days disables pruning.
    log_keep_days: int = 14
    log_keep_min: int = 10

    # -- combat simulator -----------------------------------------------
    sim_count: int = 8000
    sim_timeout: float = 6.0
    # Worker threads the sidecar shards trials across. 0 = the sidecar's own
    # default (4). Each holds its own ~350MB card DB, and throughput flattens
    # past 4, so raising this trades a lot of memory for very little speed.
    sim_workers: int = 0
    # Keep the simulator off the cores the game is drawing on. "auto" runs the
    # sidecar at SCHED_IDLE / nice 19 and, on a hybrid CPU, pins it to the
    # efficiency cores. "off" is the old behaviour, for bisecting.
    sim_cpu_policy: str = "auto"
    # Workers set aside for the recruit-phase shop forecast, so a guide the
    # player is shuffling minions against cannot take the pool the real fight
    # needs. 0 = no reservation (both share the whole pool).
    sim_shop_workers: int = 1

    # -- overlay ---------------------------------------------------------
    # None = derive from the monitor height so the HUD occupies the same share
    # of the screen as the design mock (1080p ~1.53, 1440p ~2.04).
    overlay_scale: float | None = None
    # Pin the overlay to a connector name (e.g. "DP-2"); None = default monitor.
    overlay_monitor: str | None = None
    # Layout mode: panels grow drag handles and save their position on drop.
    overlay_edit: bool = False
    # The leaderboard hover column that drives the scout popout.
    hover_strips: bool = True

    # -- hover calibration (written by dragging in layout mode) ----------
    leaderboard_top_frac: float = 0.16
    leaderboard_bottom_frac: float = 0.85
    leaderboard_width_px: int = 96
    leaderboard_left_px: int = 0
    leaderboard_skew_px: int = 0

    # -- debugging -------------------------------------------------------
    log_level: str = "info"
    log_to_file: bool = True
    # Draw the hover boxes faintly and log slot hits.
    hover_debug: bool = False

    # Unrecognised keys, kept so a rewrite never drops them. In practice the
    # `pos_<panel>_x/y` panel positions, which are per-panel and so cannot be
    # fields.
    extra: dict = field(default_factory=dict)


# Fields whose TOML string is a filesystem path, expanded on load.
_PATH_FIELDS = frozenset({"hearthstone_dir"})


def config_fields() -> tuple[str, ...]:
    """Every recognised top-level config key, in declaration order."""
    return tuple(f.name for f in dataclasses.fields(Config) if f.name != "extra")


def load_config(path: Path | None = None) -> Config:
    path = path or CONFIG_FILE
    cfg = Config()
    if not path.is_file():
        return cfg
    raw = tomllib.loads(path.read_text())
    for name in config_fields():
        if name not in raw:
            continue
        value = raw.pop(name)
        setattr(cfg, name, Path(value).expanduser() if name in _PATH_FIELDS else value)
    # Whatever is left is either a panel position or a key from a newer version;
    # either way it survives a rewrite untouched.
    cfg.extra = raw
    return cfg


def _toml_scalar(v) -> str:
    if isinstance(v, bool):  # bool is a subclass of int — test it first
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _line_key(line: str) -> str | None:
    """The top-level key a config line assigns, or None for comments/blanks."""
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    return stripped.split("=", 1)[0].strip()


def update_config_values(values: dict, path: Path | None = None) -> None:
    """Update-or-append flat top-level keys in config.toml, preserving every
    other line (comments, unrelated keys). Values must be str/int/float/bool.

    Deliberately line-based rather than a full TOML round-trip: stdlib has no
    TOML writer, and this keeps the user's hand-written file intact.
    """
    path = path or CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.is_file() else []
    remaining = dict(values)
    out: list[str] = []
    for line in lines:
        key = _line_key(line)
        if key in remaining:
            out.append(f"{key} = {_toml_scalar(remaining.pop(key))}")
        else:
            out.append(line)
    out.extend(f"{k} = {_toml_scalar(v)}" for k, v in remaining.items())
    path.write_text("\n".join(out) + "\n")


def remove_config_keys(keys, path: Path | None = None) -> list[str]:
    """Delete flat top-level keys from config.toml, returning those removed.

    The counterpart to `update_config_values`, and line-based for the same
    reason: resetting a setting means the key is *gone*, so the default is
    picked up again — which update-or-append cannot express, and which a TOML
    round-trip would buy at the cost of the user's comments.
    """
    path = path or CONFIG_FILE
    if not path.is_file():
        return []
    wanted = set(keys)
    removed: list[str] = []
    out: list[str] = []
    for line in path.read_text().splitlines():
        key = _line_key(line)
        if key is not None and key in wanted:
            removed.append(key)
        else:
            out.append(line)
    if removed:
        path.write_text(("\n".join(out) + "\n") if out else "")
    return removed
