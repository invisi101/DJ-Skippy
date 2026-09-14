"""Named playlists, stored as M3U8.

M3U rather than a private format on purpose: every other music player on the
system can read these, so a playlist built in DJ-Skippy opens in mpv, VLC or
anything else without conversion.

Paths are written absolute. Relative paths are more portable in principle, but
they break the moment a playlist is opened from a different directory, and a
playlist that silently loses half its tracks is worse than one that is not
portable.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from .library import Library, Track

PLAYLIST_DIR = Path(
    os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
) / "dj-skippy" / "playlists"

SUFFIX = ".m3u8"


@dataclass
class PlaylistInfo:
    name: str
    path: Path
    track_count: int

    @property
    def label(self) -> str:
        plural = "track" if self.track_count == 1 else "tracks"
        return f"{self.name}   ({self.track_count} {plural})"


def safe_filename(name: str) -> str:
    """Turn a playlist name into a filename without surprises.

    Keeps unicode (so Japanese playlist names work) but strips path separators
    and control characters, which are the parts that actually cause harm.
    """
    cleaned = unicodedata.normalize("NFC", name.strip())
    cleaned = re.sub(r"[/\\\x00-\x1f]", "_", cleaned)
    cleaned = cleaned.strip(". ")
    return cleaned or "untitled"


class PlaylistStore:
    """Reads and writes the playlist folder."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory) if directory else PLAYLIST_DIR

    def _path_for(self, name: str) -> Path:
        return self.directory / (safe_filename(name) + SUFFIX)

    def list(self) -> list[PlaylistInfo]:
        """Every saved playlist, newest name-sorted."""
        if not self.directory.is_dir():
            return []
        out: list[PlaylistInfo] = []
        for path in sorted(self.directory.glob(f"*{SUFFIX}")):
            try:
                count = sum(
                    1
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")
                )
            except OSError:
                continue
            out.append(PlaylistInfo(name=path.stem, path=path, track_count=count))
        return out

    def exists(self, name: str) -> bool:
        return self._path_for(name).exists()

    def save(self, name: str, tracks: Sequence["Track"]) -> Path:
        """Write a playlist. Returns the file written."""
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path_for(name)

        lines = ["#EXTM3U", f"#PLAYLIST:{name}"]
        for track in tracks:
            duration = int(track.length) if track.length else -1
            lines.append(f"#EXTINF:{duration},{track.artist} - {track.title}")
            lines.append(track.path)

        # Write to a temporary file and move it into place, so an interrupted
        # save cannot truncate an existing playlist.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(path)
        return path

    def load(self, name: str, library: "Library") -> list["Track"]:
        """Read a playlist back, resolving paths against the library.

        Tracks in the file but no longer in the library are read from disk
        directly, so a playlist still works for music outside the music
        folder. Anything that no longer exists is dropped.
        """
        path = self._path_for(name)
        if not path.exists():
            return []

        by_path = {t.path: t for t in library.all_tracks}
        tracks: list[Track] = []

        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            track = by_path.get(line)
            if track is None:
                track = _track_from_path(line)
            if track is not None:
                tracks.append(track)
        return tracks

    def delete(self, name: str) -> bool:
        path = self._path_for(name)
        try:
            path.unlink()
            return True
        except OSError:
            return False

    def rename(self, old: str, new: str) -> bool:
        source = self._path_for(old)
        target = self._path_for(new)
        if not source.exists() or target.exists():
            return False
        try:
            source.rename(target)
            return True
        except OSError:
            return False


def _track_from_path(path_str: str) -> "Track | None":
    """Build a Track for a file that is not in the library."""
    from .library import Track

    path = Path(path_str)
    if not path.is_file():
        return None

    title, artist, album = path.stem, "Unknown Artist", "Unknown Album"
    length = 0.0
    try:
        import mutagen

        meta = mutagen.File(path, easy=True)
        if meta is not None:
            def first(key: str, default: str) -> str:
                value = meta.get(key)
                return str(value[0]) if value else default

            title = first("title", path.stem)
            artist = first("artist", "Unknown Artist")
            album = first("album", "Unknown Album")
            if meta.info is not None:
                length = float(getattr(meta.info, "length", 0.0) or 0.0)
    except Exception:
        pass

    return Track(
        id=-1,
        path=str(path),
        title=title,
        artist=artist,
        albumartist=artist,
        album=album,
        track_no=0,
        disc_no=0,
        length=length,
        year=0,
        genre="",
        format=path.suffix.lstrip(".").upper(),
    )
