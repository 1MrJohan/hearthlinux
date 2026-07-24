"""Root-logger setup that can be re-pointed while the tracker is running.

The tracker is normally launched *by the game* (`scripts/steam-launch.sh`), not
from a terminal, so console output goes nowhere anybody will ever read. A
rotating file is the only way a game that went wrong is still inspectable
afterwards — which is the whole reason `log_to_file` defaults on.

`-v` and the `log_level` setting converge here: the flag is a floor, not a
separate mechanism, so turning the level down in the settings window while
running with `-v` cannot leave you quieter than you asked for on the command
line.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from bgtracker.config import CACHE_DIR, Config

LOG_FILE = CACHE_DIR / "bgtracker.log"

# Two files of 2MB is a few hours of INFO or a long game at DEBUG — enough to
# cover "what happened last game" without quietly eating a user's disk.
MAX_BYTES = 2 * 1024 * 1024
BACKUPS = 2

LEVELS = {"warning": logging.WARNING, "info": logging.INFO, "debug": logging.DEBUG}

FORMAT = "%(levelname)s %(name)s: %(message)s"
FILE_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

_file_handler: RotatingFileHandler | None = None
_verbose = False


class DropHslogNoise(logging.Filter):
    """hslog warns on the root logger about quirks it already works around."""

    def filter(self, record):
        return "Broken option nesting" not in record.getMessage()


def level_for(cfg: Config) -> int:
    """The effective level: the configured one, or DEBUG if -v was passed."""
    if _verbose:
        return logging.DEBUG
    return LEVELS.get(str(cfg.log_level).lower(), logging.INFO)


def configure(cfg: Config, verbose: bool = False) -> None:
    """Install the console handler (and the file handler, if enabled)."""
    global _verbose
    _verbose = verbose

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(FORMAT))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(console)
    root.setLevel(level_for(cfg))
    if not verbose:
        # Only filtered off the *normal* path: -v means you asked for the noise.
        console.addFilter(DropHslogNoise())
    apply_config(cfg)


def apply_config(cfg: Config) -> None:
    """Re-apply level and file logging from `cfg`. Safe to call repeatedly."""
    global _file_handler
    root = logging.getLogger()
    root.setLevel(level_for(cfg))

    if cfg.log_to_file and _file_handler is None:
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            _file_handler = RotatingFileHandler(
                LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
            )
        except OSError as exc:
            # An unwritable cache dir must not stop the tracker tracking.
            log = logging.getLogger(__name__)
            log.warning("could not open log file %s (%s)", LOG_FILE, exc)
            return
        _file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
        _file_handler.addFilter(DropHslogNoise())
        root.addHandler(_file_handler)
    elif not cfg.log_to_file and _file_handler is not None:
        root.removeHandler(_file_handler)
        _file_handler.close()
        _file_handler = None


def attach(settings) -> None:
    """Wire the LOGGING channel so level and file changes take effect at once."""
    from bgtracker.settings import LOGGING

    settings.subscribe(LOGGING, lambda _keys: apply_config(settings.cfg))


def recent(lines: int = 200) -> str:
    """The tail of the log file, for the diagnostics bundle."""
    if not LOG_FILE.is_file():
        return "(no log file)"
    try:
        return "\n".join(LOG_FILE.read_text(errors="replace").splitlines()[-lines:])
    except OSError as exc:
        return f"(could not read {LOG_FILE}: {exc})"
