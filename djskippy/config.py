"""Configuration and persistent state.

Config lives at ~/.config/dj-skippy/config.toml and is written with defaults on
first run so there is always a commented file to edit. Playback state (for
resume) lives under ~/.local/state and is rewritten constantly, so it is kept
well away from the config the user actually edits.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


CONFIG_DIR = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "dj-skippy"
STATE_DIR = Path(
    os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
) / "dj-skippy"

CONFIG_FILE = CONFIG_DIR / "config.toml"
STATE_FILE = STATE_DIR / "state.json"

DEFAULT_CONFIG = """\
# DJ-Skippy configuration
#
# Unlike a certain other player, this file supports trailing comments, blank
# lines, and anything else TOML allows. Delete it and it regenerates.

[library]
# MusicBrainz enrichment. DJ-Skippy is a music player first: with this off it
# plays everything in your folder using the tags already in the files, and
# never contacts MusicBrainz, imports anything, or maintains a beets database.
#
# With it on, albums can additionally be looked up and tagged properly, and
# the interface marks which tracks carry MusicBrainz data (no mark) and which
# were read from the files themselves (·).
musicbrainz = true

# Where your music lives. DJ-Skippy reads the beets database when one exists
# (that is where the MusicBrainz tags are) and falls back to scanning this
# folder directly when it does not.
music_dir = "~/Music"

# Sort "The Pogues" under P rather than T.
smart_artist_sort = true

[playback]
# ReplayGain: "smart", "track", "album", or "off".
#   smart = album gain when playing an album straight through, track gain when
#           shuffling or playing a queue. Matches cmus's behaviour.
replaygain = "smart"

# Reopen on the track and position you left off.
resume = true

# Pressing "previous" restarts the current track unless you are within this
# many seconds of its start, in which case it goes to the actual previous
# track. Lifted wholesale from cmus, because it is correct.
rewind_offset = 5

# Start-up volume, 0-130. Above 100 is software amplification.
volume = 80

# Play the next track automatically when one ends.
continue_playback = true

[visualiser]
# Cava bars along the bottom.
enabled = true

# Number of bars. "auto" fits them to the terminal width.
bars = "auto"

# Cava reads your speaker output, so it visualises whatever is playing -
# including audio from other applications.
framerate = 60

[web]
# Browser player. Bound to localhost only; nothing on your network can reach it.
enabled = true
host = "127.0.0.1"
port = 8080

[mpris]
# Register on D-Bus so media keys, playerctl and your Quickshell bar can drive
# DJ-Skippy with no further setup.
enabled = true

[watcher]
# Watch the music folder and import new albums automatically.
enabled = true

# How long a folder must go without changes before it is treated as a finished
# copy. Albums arrive one file at a time; importing a half-copied folder gives
# you a half-tagged album. Raise this if you copy over a slow network.
settle_seconds = 20

# Similarity at or above which an album is tagged with no questions asked.
# Below it, the album is parked in the review queue (view 6) for you to decide.
# 90 is about right: above that, differences are cosmetic capitalisation;
# below, they tend to be genuinely different releases.
auto_threshold = 90

# MusicBrainz returns 503 under load, which looks exactly like "album not
# found". Before believing that, re-run the whole lookup this many times.
#
# Note beets already retries each HTTP request 6 times internally with
# backoff, and rate-limits itself to one request every 4 seconds. These are
# *additional* full lookups on top of that, so 2 means up to 18 attempts per
# album. Raising it makes a bad MusicBrainz day survivable but very slow.
lookup_retries = 2
"""


def _as_int(value: Any, default: int) -> int:
    """Read a setting as a whole number, falling back rather than raising.

    A typo in the config file must not stop the music. `volume = "loud"` used
    to raise ValueError out of Config.load() and prevent DJ-Skippy starting at
    all, which is a poor response to a mistyped line.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_choice(value: Any, allowed: tuple[str, ...], default: str) -> str:
    text = str(value).strip().lower()
    return text if text in allowed else default


@dataclass
class LibraryConfig:
    music_dir: Path = Path.home() / "Music"
    smart_artist_sort: bool = True
    musicbrainz: bool = True


@dataclass
class PlaybackConfig:
    replaygain: str = "smart"
    resume: bool = True
    rewind_offset: int = 5
    volume: int = 80
    continue_playback: bool = True


@dataclass
class VisualiserConfig:
    enabled: bool = True
    bars: str | int = "auto"
    framerate: int = 60


@dataclass
class WebConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8080


@dataclass
class MprisConfig:
    enabled: bool = True


@dataclass
class WatcherConfig:
    enabled: bool = True
    settle_seconds: float = 20.0
    auto_threshold: float = 90.0
    lookup_retries: int = 2


@dataclass
class Config:
    library: LibraryConfig = field(default_factory=LibraryConfig)
    playback: PlaybackConfig = field(default_factory=PlaybackConfig)
    visualiser: VisualiserConfig = field(default_factory=VisualiserConfig)
    web: WebConfig = field(default_factory=WebConfig)
    mpris: MprisConfig = field(default_factory=MprisConfig)
    watcher: WatcherConfig = field(default_factory=WatcherConfig)

    @classmethod
    def load(cls) -> Config:
        """Read the config file, writing defaults if it does not exist."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_FILE.exists():
            CONFIG_FILE.write_text(DEFAULT_CONFIG)

        data: dict[str, Any] = {}
        if tomllib is not None:
            try:
                data = tomllib.loads(CONFIG_FILE.read_text())
            except Exception:
                # A broken config should never stop the music. Fall back to
                # defaults rather than refusing to start.
                data = {}

        # Every value is coerced with a fallback: one mistyped line should
        # never stop DJ-Skippy starting.
        cfg = cls()
        lib = data.get("library", {})
        try:
            music_dir = Path(
                os.path.expanduser(str(lib.get("music_dir", "~/Music")))
            ).resolve()
        except (OSError, ValueError):
            music_dir = Path.home() / "Music"
        cfg.library = LibraryConfig(
            music_dir=music_dir,
            smart_artist_sort=_as_bool(lib.get("smart_artist_sort"), True),
            musicbrainz=_as_bool(lib.get("musicbrainz"), True),
        )

        pb = data.get("playback", {})
        cfg.playback = PlaybackConfig(
            replaygain=_as_choice(
                pb.get("replaygain", "smart"),
                ("smart", "track", "album", "off"), "smart",
            ),
            resume=_as_bool(pb.get("resume"), True),
            rewind_offset=max(0, _as_int(pb.get("rewind_offset"), 5)),
            volume=max(0, min(130, _as_int(pb.get("volume"), 80))),
            continue_playback=_as_bool(pb.get("continue_playback"), True),
        )

        vis = data.get("visualiser", {})
        bars = vis.get("bars", "auto")
        if not (isinstance(bars, int) or str(bars).lower() == "auto"):
            bars = "auto"
        cfg.visualiser = VisualiserConfig(
            enabled=_as_bool(vis.get("enabled"), True),
            bars=bars,
            framerate=max(5, min(144, _as_int(vis.get("framerate"), 60))),
        )

        web = data.get("web", {})
        port = _as_int(web.get("port"), 8080)
        cfg.web = WebConfig(
            enabled=_as_bool(web.get("enabled"), True),
            host=str(web.get("host", "127.0.0.1")) or "127.0.0.1",
            port=port if 1 <= port <= 65535 else 8080,
        )

        cfg.mpris = MprisConfig(
            enabled=_as_bool(data.get("mpris", {}).get("enabled"), True)
        )

        watch = data.get("watcher", {})
        cfg.watcher = WatcherConfig(
            enabled=_as_bool(watch.get("enabled"), True),
            settle_seconds=max(1.0, _as_float(watch.get("settle_seconds"), 20)),
            auto_threshold=max(
                0.0, min(101.0, _as_float(watch.get("auto_threshold"), 90))
            ),
            lookup_retries=max(0, min(10, _as_int(watch.get("lookup_retries"), 2))),
        )
        return cfg


def load_state() -> dict[str, Any]:
    """Read persisted playback state. Never raises."""
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict[str, Any]) -> None:
    """Persist playback state. Never raises - losing the resume position must
    not be able to take the application down."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(STATE_FILE)  # atomic; never leaves a half-written file
    except Exception:
        pass
