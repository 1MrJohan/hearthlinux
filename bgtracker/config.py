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
    extra: dict = field(default_factory=dict)


def load_config(path: Path | None = None) -> Config:
    path = path or CONFIG_FILE
    cfg = Config()
    if path.is_file():
        raw = tomllib.loads(path.read_text())
        if "hearthstone_dir" in raw:
            cfg.hearthstone_dir = Path(raw.pop("hearthstone_dir")).expanduser()
        for key in ("poll_active", "poll_idle", "sim_count", "sim_timeout", "overlay_scale"):
            if key in raw:
                setattr(cfg, key, raw.pop(key))
        cfg.extra = raw
    return cfg
