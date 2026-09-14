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
# found". Before believing that, retry this many times with a growing delay
# (3s, 6s, 12s, 24s). Set to 0 to give up on the first empty answer.
lookup_retries = 4
"""


@dataclass
class LibraryConfig:
    music_dir: Path = Path.home() / "Music"
    smart_artist_sort: bool = True


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
    lookup_retries: int = 4


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

        cfg = cls()
        lib = data.get("library", {})
        cfg.library = LibraryConfig(
            music_dir=Path(
                os.path.expanduser(lib.get("music_dir", "~/Music"))
            ).resolve(),
            smart_artist_sort=bool(lib.get("smart_artist_sort", True)),
        )

        pb = data.get("playback", {})
        cfg.playback = PlaybackConfig(
            replaygain=str(pb.get("replaygain", "smart")),
            resume=bool(pb.get("resume", True)),
            rewind_offset=int(pb.get("rewind_offset", 5)),
            volume=int(pb.get("volume", 80)),
            continue_playback=bool(pb.get("continue_playback", True)),
        )

        vis = data.get("visualiser", {})
        cfg.visualiser = VisualiserConfig(
            enabled=bool(vis.get("enabled", True)),
            bars=vis.get("bars", "auto"),
            framerate=int(vis.get("framerate", 60)),
        )

        web = data.get("web", {})
        cfg.web = WebConfig(
            enabled=bool(web.get("enabled", True)),
            host=str(web.get("host", "127.0.0.1")),
            port=int(web.get("port", 8080)),
        )

        cfg.mpris = MprisConfig(enabled=bool(data.get("mpris", {}).get("enabled", True)))

        watch = data.get("watcher", {})
        cfg.watcher = WatcherConfig(
            enabled=bool(watch.get("enabled", True)),
            settle_seconds=float(watch.get("settle_seconds", 20)),
            auto_threshold=float(watch.get("auto_threshold", 90)),
            lookup_retries=int(watch.get("lookup_retries", 4)),
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
