"""The settings schema: one declarative entry per config key.

This is the single source of truth for everything *about* a setting — its type,
bounds, label, help text, and which subsystem has to be told when it changes.
It drives the settings window's widgets, validation, reset-to-default, and the
generated `config.example.toml`.

`Config` in `config.py` holds the *values*; this holds the metadata. The two are
kept honest by `tests/test_settings_schema.py`, which fails if a field gains a
setting without an entry here (or an entry disagrees about the default).

**Channels** are how a change reaches the running process. Every setting applies
live, but "live" means something different per subsystem — some are a bare
attribute assignment, one has to respawn a 350MB-per-worker Node process. The
channel names which applier does that work; `SettingsService.subscribe` wires
them up.
"""

from __future__ import annotations

import asyncio
import logging
import textwrap
from dataclasses import dataclass
from pathlib import Path

from bgtracker.config import (
    Config,
    _toml_scalar,
    config_fields,
    remove_config_keys,
    update_config_values,
)

log = logging.getLogger(__name__)

# Sidebar order in the settings window.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("overlay", "Overlay"),
    ("simulator", "Simulator"),
    ("game", "Game & Logs"),
    ("calibration", "Hover Calibration"),
    ("debug", "Debug"),
)

# Channel a change is dispatched on. NONE means nothing has to be told — the
# value is read fresh from the shared Config every time it is used.
NONE = "none"
SIM = "sim"                      # attribute assignment on the live SimClient
SIM_RESPAWN = "sim_respawn"      # sidecar restart, gated on the request lock
TAILER = "tailer"                # re-resolve the install dir / re-prune logs
OVERLAY = "overlay"              # live toggle the overlay handles in place
OVERLAY_REBUILD = "overlay_rebuild"  # destroy and rebuild the overlay window
HOVER = "hover"                  # rebuild the hover-strip window
LOGGING = "logging"              # swap handlers/level on the root logger


@dataclass(frozen=True)
class Setting:
    key: str
    section: str
    kind: str          # bool | int | float | choice | path | monitor
    default: object
    label: str
    help: str
    channel: str = NONE
    minimum: float | None = None
    maximum: float | None = None
    step: float = 1
    choices: tuple[str, ...] = ()
    # A None value is meaningful for some settings ("auto"), and is stored by
    # *removing* the key rather than writing a null TOML has no syntax for.
    nullable: bool = False
    null_label: str = "Auto"
    # Shown in the generated example file where `default` is None and so has no
    # printable form.
    example: str = ""
    unit: str = ""

    def coerce(self, value):
        """Validate and normalise a value from a widget or a config file.

        Raises ValueError on something the schema cannot represent; clamps
        anything merely out of range, so a hand-edited `sim_count = 99999999`
        loads as the maximum instead of refusing to start.
        """
        if value is None:
            if not self.nullable:
                raise ValueError(f"{self.key} cannot be unset")
            return None
        if self.kind == "bool":
            return bool(value)
        if self.kind in ("int", "float"):
            number = int(value) if self.kind == "int" else float(value)
            if self.minimum is not None:
                number = max(self.minimum, number)
            if self.maximum is not None:
                number = min(self.maximum, number)
            return int(number) if self.kind == "int" else float(number)
        if self.kind == "choice":
            text = str(value)
            if text not in self.choices:
                raise ValueError(f"{self.key}: {text!r} not in {self.choices}")
            return text
        if self.kind == "path":
            return Path(value).expanduser()
        return str(value)


SETTINGS: tuple[Setting, ...] = (
    # -- overlay --------------------------------------------------------
    Setting(
        "overlay_scale", "overlay", "float", None,
        "Overlay scale",
        "Size of every panel, font and gem. Auto matches the design mock's "
        "share of the screen for your monitor height (1080p ~1.53, 1440p "
        "~2.04, 4K capped at 3.0).",
        channel=OVERLAY_REBUILD,
        minimum=0.5, maximum=3.0, step=0.05, nullable=True, example="2.0",
    ),
    Setting(
        "overlay_monitor", "overlay", "monitor", None,
        "Monitor",
        "Pin the overlay to one display by connector name. Auto uses the "
        "compositor's default monitor.",
        channel=OVERLAY_REBUILD, nullable=True, example='"DP-2"',
    ),
    Setting(
        "overlay_edit", "overlay", "bool", False,
        "Layout mode",
        "Panels grow a drag handle and a dashed border, and save their new "
        "position when you drop them. The overlay stops being click-through "
        "over the panels while this is on.",
        channel=OVERLAY,
    ),
    Setting(
        "hover_strips", "overlay", "bool", True,
        "Leaderboard hover column",
        "Hovering a hero portrait in the game's leaderboard pops out that "
        "player's last-seen board. Does not capture the pointer, so the "
        "game's own preview keeps working alongside it.",
        channel=HOVER,
    ),
    Setting(
        "mmr_prompt", "overlay", "bool", True,
        "Ask for your MMR after a game",
        "A small 'Record MMR' button appears under the HUD when a game ends "
        "and opens the match-history window with the rating box focused. "
        "Ratings are never in the log, so they are only ever as complete as "
        "you type them in. It disappears by itself after two minutes.",
        channel=OVERLAY,
    ),

    # -- simulator ------------------------------------------------------
    Setting(
        "sim_count", "simulator", "int", 8000,
        "Simulations per combat",
        "More trials narrow the confidence interval, but at 8000 the 95% "
        "margin is already about a point — past here you are buying latency, "
        "not accuracy.",
        channel=SIM, minimum=500, maximum=50000, step=500,
    ),
    Setting(
        "sim_timeout", "simulator", "float", 6.0,
        "Simulation timeout",
        "Seconds before giving up on a combat's odds. The sidecar is given "
        "80% of this as its own budget so it returns a partial result rather "
        "than nothing.",
        channel=SIM, minimum=1.0, maximum=30.0, step=0.5, unit="s",
    ),
    Setting(
        "sim_workers", "simulator", "int", 0,
        "Worker threads",
        "Threads the trials are split across; 0 leaves the choice to the "
        "sidecar (4). Each worker holds its own ~350MB card DB and throughput "
        "flattens past 4, so raising this costs a lot of memory for very "
        "little speed. Changing it restarts the sidecar, which takes a few "
        "seconds and waits for any combat in flight.",
        channel=SIM_RESPAWN, minimum=0, maximum=16, step=1,
    ),
    Setting(
        "sim_cpu_policy", "simulator", "choice", "auto",
        "Keep the simulator off the game's cores",
        "Runs the sidecar at idle priority, and on a hybrid CPU pins it to the "
        "efficiency cores so it cannot compete with the game for the fast ones. "
        "Odds take longer to firm up; a first number still arrives in about a "
        "tenth of a second. Turn off to bisect a performance problem. Changing "
        "it restarts the sidecar.",
        channel=SIM_RESPAWN, choices=("auto", "off"),
    ),
    Setting(
        "sim_shop_workers", "simulator", "int", 1,
        "Workers reserved for shop odds",
        "The recruit-phase forecast is a guide against a board of stated age, "
        "re-run every time you buy, sell or reposition. Giving it its own "
        "worker keeps it from taking the whole pool the real combat forecast "
        "needs, at the cost of it firming up more slowly. 0 shares the pool, "
        "which is the older behaviour. With sim_workers=1 the reservation is "
        "always clamped to 0: the real fight keeps the only worker there is.",
        channel=SIM, minimum=0, maximum=4, step=1,
    ),

    # -- game & logs ----------------------------------------------------
    Setting(
        "hearthstone_dir", "game", "path", None,
        "Hearthstone install",
        "The folder containing Logs/. Auto-discovers across Steam compatdata "
        "and ~/Games (Lutris) prefixes.",
        channel=TAILER, nullable=True, null_label="Auto-discover",
        example='"/home/you/Games/battlenet/drive_c/Program Files (x86)/Hearthstone"',
    ),
    Setting(
        "poll_active", "game", "float", 0.25,
        "Active poll interval",
        "How often the log is checked for new lines while a game is running.",
        minimum=0.05, maximum=5.0, step=0.05, unit="s",
    ),
    Setting(
        "poll_idle", "game", "float", 2.0,
        "Idle poll interval",
        "How often the log is checked when nothing is happening.",
        minimum=0.1, maximum=30.0, step=0.1, unit="s",
    ),
    Setting(
        "log_keep_days", "game", "int", 14,
        "Prune logs older than",
        "Hearthstone writes a new Logs/Hearthstone_* dir per session and never "
        "cleans up. 0 disables pruning entirely.",
        channel=TAILER, minimum=0, maximum=365, step=1, unit=" days",
    ),
    Setting(
        "log_keep_min", "game", "int", 10,
        "Always keep at least",
        "Newest session dirs kept regardless of age, so pruning can never "
        "leave you with nothing to replay.",
        channel=TAILER, minimum=1, maximum=200, step=1, unit=" sessions",
    ),

    # -- hover calibration ----------------------------------------------
    # Normally set by dragging the boxes in layout mode; exposed here so they
    # can be nudged precisely and reset.
    Setting(
        "leaderboard_top_frac", "calibration", "float", 0.16,
        "Top of the column",
        "Where the first portrait box starts, as a fraction of monitor height.",
        channel=HOVER, minimum=0.0, maximum=1.0, step=0.005,
    ),
    Setting(
        "leaderboard_bottom_frac", "calibration", "float", 0.85,
        "Bottom of the column",
        "Where the last portrait box ends, as a fraction of monitor height.",
        channel=HOVER, minimum=0.0, maximum=1.0, step=0.005,
    ),
    Setting(
        "leaderboard_width_px", "calibration", "int", 96,
        "Box width",
        "Width of each portrait box in pixels.",
        channel=HOVER, minimum=20, maximum=600, step=1, unit="px",
    ),
    Setting(
        "leaderboard_left_px", "calibration", "int", 0,
        "Left offset",
        "X position of the topmost box in pixels from the left screen edge.",
        channel=HOVER, minimum=0, maximum=8000, step=1, unit="px",
    ),
    Setting(
        "leaderboard_skew_px", "calibration", "int", 0,
        "Tilt",
        "The leaderboard leans, so each box's x is interpolated from the top "
        "box to the bottom one. This is that offset, and may be negative.",
        channel=HOVER, minimum=-1000, maximum=1000, step=1, unit="px",
    ),

    # -- debug ----------------------------------------------------------
    Setting(
        "log_level", "debug", "choice", "info",
        "Log level",
        "Debug is very loud — it includes every input-region update — but it "
        "is what you want when a panel is not appearing.",
        channel=LOGGING, choices=("warning", "info", "debug"),
    ),
    Setting(
        "log_to_file", "debug", "bool", True,
        "Write a log file",
        "Keeps a rotating log so a game that went wrong is still inspectable "
        "afterwards, which it is not when the tracker was launched from the "
        "game rather than a terminal.",
        channel=LOGGING,
    ),
    Setting(
        "hover_debug", "debug", "bool", False,
        "Draw hover boxes",
        "Outlines the leaderboard hover boxes faintly and logs which slot the "
        "pointer resolves to. Use this to check calibration without entering "
        "layout mode.",
        channel=HOVER,
    ),
)

BY_KEY: dict[str, Setting] = {s.key: s for s in SETTINGS}


def by_section(section: str) -> tuple[Setting, ...]:
    return tuple(s for s in SETTINGS if s.section == section)


def coerce(key: str, value):
    """Validate a value for `key`, or pass it through if the key is unknown.

    Unknown keys are the `pos_<panel>_x/y` positions, which are machine-written
    and deliberately not in the schema.
    """
    setting = BY_KEY.get(key)
    return setting.coerce(value) if setting else value


# -- generated config.example.toml --------------------------------------

_EXAMPLE_HEADER = """\
# Copy to ~/.config/hs-bg-tracker/config.toml and edit as needed.
# All keys are optional, and every one below is shown at its default.
#
# GENERATED from bgtracker/settings.py — edit the schema there, not this file.
# `bgtracker settings` edits the real config with the same options in a window.
"""


def _comment(text: str) -> list[str]:
    return [f"# {line}" for line in textwrap.wrap(text, width=74)]


def example_toml() -> str:
    """The contents of `config.example.toml`, rendered from the schema.

    Checked against the file on disk by the test suite — that is what stops the
    documented surface drifting away from the real one again.
    """
    out = [_EXAMPLE_HEADER]
    for section, title in SECTIONS:
        settings = by_section(section)
        if not settings:
            continue
        rule = "-" * max(1, 70 - len(title))
        out.append(f"# ---- {title} {rule}\n")
        for setting in settings:
            out.extend(_comment(setting.help))
            if setting.default is None:
                out.append(f"# {setting.key} = {setting.example}")
            else:
                out.append(f"# {setting.key} = {_toml_scalar(setting.default)}")
            out.append("")
    return "\n".join(out)


def sanitize(cfg: Config) -> Config:
    """Bring a freshly loaded Config inside the schema, in place.

    `load_config` walks the dataclass fields and assigns raw TOML, so nothing
    on that path has ever met `Setting.coerce` — a hand-edited
    `sim_count = 99999999`, `poll_active = 0` (which spins the tail loop), or
    `log_level = "chatty"` all used to load verbatim. Coercion clamps what is
    merely out of range; a value the schema cannot represent at all falls back
    to the default with a warning, because **refusing to start is the one
    outcome a config typo must not have** — the tracker is normally launched by
    the game, where a traceback goes nowhere anybody reads.
    """
    for setting in SETTINGS:
        raw = getattr(cfg, setting.key)
        try:
            value = setting.coerce(raw)
        except (ValueError, TypeError):
            log.warning(
                "config: %s = %r is not valid; using the default (%r)",
                setting.key, raw, setting.default,
            )
            value = setting.default
        if value != raw:
            setattr(cfg, setting.key, value)
    return cfg


def defaults_config() -> Config:
    """A Config carrying nothing but defaults — the target of "reset all"."""
    cfg = Config()
    for setting in SETTINGS:
        setattr(cfg, setting.key, setting.default)
    return cfg


def missing_settings() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(config fields with no Setting, Settings with no config field)."""
    fields = set(config_fields())
    keys = set(BY_KEY)
    return tuple(sorted(fields - keys)), tuple(sorted(keys - fields))


# -- the live-apply spine ------------------------------------------------


def _storable(value):
    """A value in the form `update_config_values` can write."""
    return str(value) if isinstance(value, Path) else value


class SettingsService:
    """Owns *the* Config instance and pushes changes at the running process.

    There is exactly one of these per process and exactly one Config inside it.
    That matters more than it looks: settings are applied by **mutating the
    Config in place**, so everything holding a reference — the tail loop's
    `cfg.poll_active`, the overlay, the sim client — sees the new value without
    being told. Handing out copies would make half the settings silently
    inert.

    Subscribers register against a channel (see the constants above) and are
    called with the set of keys that changed. Appliers are deliberately
    synchronous; one that needs to await schedules its own task via `spawn`,
    because these run from GTK signal handlers on the shared GLib/asyncio loop
    and blocking there stalls the tailer and the frame clock alike.
    """

    def __init__(self, cfg: Config, path: Path | None = None):
        # Every entry point builds the service straight from `load_config()`,
        # which does no validation of its own — so this is where a hand-edited
        # file gets brought inside the schema, once, before anything reads it.
        self.cfg = sanitize(cfg)
        self.path = path
        self._subscribers: dict[str, list] = {}

    # -- reading ---------------------------------------------------------
    def get(self, key: str):
        if key in BY_KEY:
            return getattr(self.cfg, key)
        return self.cfg.extra.get(key)

    # -- writing ---------------------------------------------------------
    def set(self, key: str, value) -> bool:
        """Validate, persist, apply. Returns whether anything actually changed."""
        return self.set_many({key: value})

    def set_many(self, values: dict) -> bool:
        """Apply several settings as one file write and one dispatch per channel.

        The settings window uses this for resets, where writing eighteen keys
        one at a time would rewrite the file eighteen times and rebuild the
        overlay on each of them.
        """
        changed: dict[str, object] = {}
        for key, value in values.items():
            coerced = coerce(key, value)
            if self.get(key) != coerced:
                changed[key] = coerced
        if not changed:
            return False

        drop = [k for k, v in changed.items() if v is None]
        write = {k: _storable(v) for k, v in changed.items() if v is not None}
        if write:
            update_config_values(write, self.path)
        if drop:
            # None is "unset": TOML has no null, and an absent key is what makes
            # the default apply again on the next load.
            remove_config_keys(drop, self.path)

        for key, value in changed.items():
            if key in BY_KEY:
                setattr(self.cfg, key, value)
            elif value is None:
                self.cfg.extra.pop(key, None)
            else:
                self.cfg.extra[key] = value

        self._dispatch(changed)
        return True

    def reset(self, keys) -> bool:
        """Restore the schema defaults for `keys` and forget them in the file."""
        return self.set_many({k: BY_KEY[k].default for k in keys if k in BY_KEY})

    def reset_section(self, section: str) -> bool:
        return self.reset([s.key for s in by_section(section)])

    def reset_panel_positions(self) -> bool:
        """Forget every dragged panel position, so the defaults are derived again."""
        keys = [k for k in list(self.cfg.extra) if k.startswith("pos_")]
        if not keys:
            return False
        remove_config_keys(keys, self.path)
        for key in keys:
            self.cfg.extra.pop(key, None)
        self._dispatch(dict.fromkeys(keys), channel=OVERLAY_REBUILD)
        return True

    def reset_all(self) -> bool:
        """Everything back to defaults, including panel positions."""
        positions = self.reset_panel_positions()
        return self.reset(list(BY_KEY)) or positions

    # -- dispatch --------------------------------------------------------
    def subscribe(self, channel: str, applier) -> None:
        self._subscribers.setdefault(channel, []).append(applier)

    def _dispatch(self, changed: dict, channel: str | None = None) -> None:
        by_channel: dict[str, set[str]] = {}
        for key in changed:
            name = channel or (BY_KEY[key].channel if key in BY_KEY else NONE)
            by_channel.setdefault(name, set()).add(key)
        for name, keys in by_channel.items():
            for applier in self._subscribers.get(name, ()):
                try:
                    applier(keys)
                except Exception:
                    # One broken applier must not strand the others, nor take
                    # down the UI thread that called us.
                    log.exception("applier for %s failed on %s", name, sorted(keys))

    @staticmethod
    def spawn(coro):
        """Run a coroutine applier on the shared loop, if one is running.

        Standalone `bgtracker settings` has no asyncio loop and nothing live to
        apply to, so the work is simply dropped there — the value is already on
        disk for the next launch.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return None
        return loop.create_task(coro)


__all__ = [
    "BY_KEY", "SECTIONS", "SETTINGS", "Setting", "SettingsService",
    "by_section", "coerce", "defaults_config", "example_toml",
    "missing_settings", "sanitize",
]
