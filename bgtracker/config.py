"""Configuration loading and XDG paths."""

from __future__ import annotations

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
    # Explicit Hearthstone install dir (the folder containing Logs/); None = auto-discover
    hearthstone_dir: Path | None = None
    # Log poll intervals in seconds
    poll_active: float = 0.25
    poll_idle: float = 2.0
    # Combat simulator
    sim_count: int = 8000
    sim_timeout: float = 6.0
    # Overlay
    overlay_scale: float = 1.0
    # Log-session cleanup: prune Logs/Hearthstone_* dirs older than this,
    # but always keep at least log_keep_min newest. 0 days disables pruning.
    log_keep_days: int = 14
    log_keep_min: int = 10
    extra: dict = field(default_factory=dict)


def load_config(path: Path | None = None) -> Config:
    path = path or CONFIG_FILE
    cfg = Config()
    if path.is_file():
        raw = tomllib.loads(path.read_text())
        if "hearthstone_dir" in raw:
            cfg.hearthstone_dir = Path(raw.pop("hearthstone_dir")).expanduser()
        for key in (
            "poll_active", "poll_idle", "sim_count", "sim_timeout",
            "overlay_scale", "log_keep_days", "log_keep_min",
        ):
            if key in raw:
                setattr(cfg, key, raw.pop(key))
        cfg.extra = raw
    return cfg


def _toml_scalar(v) -> str:
    if isinstance(v, bool):  # bool is a subclass of int — test it first
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


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
        stripped = line.lstrip()
        key = stripped.split("=", 1)[0].strip() if ("=" in stripped and not stripped.startswith("#")) else None
        if key in remaining:
            out.append(f"{key} = {_toml_scalar(remaining.pop(key))}")
        else:
            out.append(line)
    out.extend(f"{k} = {_toml_scalar(v)}" for k, v in remaining.items())
    path.write_text("\n".join(out) + "\n")
