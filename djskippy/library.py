"""Music library: whatever is in your music folder.

One source of truth — the filesystem. Tags are read from the files with
mutagen, and where a file has none worth having the folder layout is used
instead: Artist/Album/track is the near-universal convention, and reading it
gives a browsable library from files that carry nothing at all.

No database, no external service, nothing to import. Put music in the folder
and it is in the library.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

#: Where the tag cache lives. Walking the folder takes hundredths of a second;
#: reading tags out of a few thousand files takes seconds. Caching the tags
#: against each file's size and mtime makes startup instant, and a file that
#: changes is re-read automatically.
CACHE_FILE = Path(
    os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
) / "dj-skippy" / "library-cache.json"
CACHE_VERSION = 1

AUDIO_SUFFIXES = {
    ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".wv",
    ".ape", ".mpc", ".alac", ".aiff", ".aif", ".dsf", ".dff", ".wma", ".mka",
    ".m4b", ".tta", ".shn", ".caf", ".au", ".ra", ".mp2", ".mod", ".xm",
    ".s3m", ".it", ".spx", ".mp4",
}

#: Folder names denoting a disc within a release rather than a release:
#: "CD1", "Disc 2", "CD 2 (320)", "CD2 - Live In Madrid", "Vol. 3". A bare
#: number counts only at one or two digits — "1999" is an album.
DISC_PATTERN = re.compile(
    r"^(?:(?:cd|disc|disk|vol(?:ume)?)[\s._-]*\d+|\d{1,2})"
    r"\s*(?:\(.*\)|\[.*\]|[-–—:]\s*.+)?$",
    re.IGNORECASE,
)


def is_disc_folder(path: Path | str) -> bool:
    """Is this folder one disc of a set, rather than an album in itself?"""
    return bool(DISC_PATTERN.match(Path(path).name.strip()))


@dataclass(frozen=True)
class Track:
    """One playable file."""

    id: int
    path: str
    title: str
    artist: str
    albumartist: str
    album: str
    track_no: int
    disc_no: int
    length: float
    year: int
    genre: str
    format: str

    @property
    def display_title(self) -> str:
        if self.track_no:
            return f"{self.track_no:02d}  {self.title}"
        return self.title

    @property
    def length_str(self) -> str:
        if not self.length:
            return "--:--"
        m, s = divmod(int(self.length), 60)
        if m >= 60:
            h, m = divmod(m, 60)
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @property
    def exists(self) -> bool:
        return os.path.exists(self.path)


def sort_key(name: str, smart: bool = True) -> str:
    """Sort key for artist names. With `smart`, ignore a leading article so
    "The Pogues" files under P."""
    key = name.strip().lower()
    if smart:
        for article in ("the ", "a ", "an "):
            if key.startswith(article):
                key = key[len(article):]
                break
    return key


class Library:
    """Artist -> album -> track, read from the music folder."""

    def __init__(self, music_dir: Path, smart_sort: bool = True) -> None:
        self.music_dir = Path(music_dir)
        self.smart_sort = smart_sort
        self._tracks: list[Track] = []
        self._cache: dict = {}
        self._cache_hits = 0
        self.error: str | None = None
        self.load()

    # -- loading ---------------------------------------------------------

    def load(self, use_cache: bool = True) -> None:
        """(Re)scan the music folder."""
        self._cache = self._read_cache() if use_cache else {}
        self._cache_hits = 0
        self._tracks = self._scan()
        self._index()
        self._write_cache()

    # -- tag cache -------------------------------------------------------

    @staticmethod
    def _stamp(path: Path) -> str:
        """Cheap identity for a file: size and mtime."""
        try:
            st = path.stat()
            return f"{st.st_size}:{int(st.st_mtime)}"
        except OSError:
            return ""

    def _read_cache(self) -> dict:
        try:
            data = json.loads(CACHE_FILE.read_text())
        except (OSError, ValueError):
            return {}
        if data.get("version") != CACHE_VERSION:
            return {}
        if data.get("music_dir") != str(self.music_dir):
            return {}
        entries = data.get("tracks")
        return entries if isinstance(entries, dict) else {}

    def _write_cache(self) -> None:
        """Never raises: a cache that cannot be written is not a failure."""
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": CACHE_VERSION,
                "music_dir": str(self.music_dir),
                "tracks": {
                    t.path: {
                        "stamp": self._stamp(Path(t.path)),
                        "title": t.title, "artist": t.artist,
                        "albumartist": t.albumartist, "album": t.album,
                        "track_no": t.track_no, "disc_no": t.disc_no,
                        "length": t.length, "year": t.year,
                        "genre": t.genre, "format": t.format,
                    }
                    for t in self._tracks
                },
            }
            tmp = CACHE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload))
            tmp.replace(CACHE_FILE)
        except Exception:
            pass

    def _scan(self) -> list[Track]:
        if not self.music_dir.is_dir():
            return []

        try:
            import mutagen
        except ImportError:
            mutagen = None  # type: ignore[assignment]

        try:
            paths = sorted(self.music_dir.rglob("*"))
        except OSError as exc:
            self.error = str(exc)
            return []

        tracks: list[Track] = []
        next_id = 1
        for path in paths:
            try:
                if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
                    continue
            except OSError:
                continue

            # Cached, if the file has not changed since we read it.
            cached = self._cache.get(str(path))
            if cached and cached.get("stamp") == self._stamp(path):
                self._cache_hits += 1
                tracks.append(
                    Track(
                        id=next_id, path=str(path),
                        title=cached["title"], artist=cached["artist"],
                        albumartist=cached["albumartist"],
                        album=cached["album"],
                        track_no=cached["track_no"], disc_no=cached["disc_no"],
                        length=cached["length"], year=cached["year"],
                        genre=cached["genre"], format=cached["format"],
                    )
                )
                next_id += 1
                continue

            title, artist, album = path.stem, "", ""
            albumartist, genre = "", ""
            track_no = disc_no = year = 0
            length = 0.0

            if mutagen is not None:
                try:
                    meta = mutagen.File(path, easy=True)
                    if meta is not None:
                        def first(key: str, default: str = "") -> str:
                            value = meta.get(key)
                            return str(value[0]) if value else default

                        title = first("title", path.stem)
                        artist = first("artist")
                        album = first("album")
                        albumartist = first("albumartist", artist)
                        genre = first("genre")
                        track_no = _first_int(first("tracknumber"))
                        disc_no = _first_int(first("discnumber"))
                        year = _first_int(first("date"))
                        if meta.info is not None:
                            length = float(getattr(meta.info, "length", 0.0) or 0.0)
                except Exception:
                    pass

            # The folder layout is usually more accurate than a file with half
            # its tags missing.
            if not artist or not album:
                folder_artist, folder_album = self._infer_from_path(path)
                artist = artist or folder_artist or "Unknown Artist"
                album = album or folder_album or "Unknown Album"

            tracks.append(
                Track(
                    id=next_id,
                    path=str(path),
                    title=title or path.stem,
                    artist=artist,
                    albumartist=albumartist or artist,
                    album=album,
                    track_no=track_no,
                    disc_no=disc_no,
                    length=length,
                    year=year,
                    genre=genre,
                    format=path.suffix.lstrip(".").upper(),
                )
            )
            next_id += 1
        return tracks

    def _infer_from_path(self, path: Path) -> tuple[str, str]:
        """Guess (artist, album) from where a file sits.

        Expects .../Artist/Album/track. Disc folders are stepped over, so
        .../Artist/Album/CD1/track still reports the album rather than "CD1".
        """
        try:
            relative = path.relative_to(self.music_dir)
        except ValueError:
            return "", ""

        parts = list(relative.parts[:-1])
        if not parts:
            return "", ""
        while len(parts) > 1 and is_disc_folder(parts[-1]):
            parts.pop()
        if len(parts) >= 2:
            return parts[-2], parts[-1]
        return "", parts[-1]

    # -- indexing --------------------------------------------------------

    def _index(self) -> None:
        self._by_artist: dict[str, list[Track]] = {}
        for track in self._tracks:
            self._by_artist.setdefault(track.albumartist, []).append(track)
        self._artists = sorted(
            self._by_artist, key=lambda a: sort_key(a, self.smart_sort)
        )

    # -- queries ---------------------------------------------------------

    @property
    def track_count(self) -> int:
        return len(self._tracks)

    @property
    def all_tracks(self) -> Sequence[Track]:
        return self._tracks

    def artists(self) -> list[str]:
        return self._artists

    def albums(self, artist: str) -> list[str]:
        """Albums by an artist, oldest first — the order a listener thinks in."""
        seen: dict[str, int] = {}
        for track in self._by_artist.get(artist, []):
            seen.setdefault(track.album, track.year or 0)
        return sorted(seen, key=lambda album: (seen[album], album.lower()))

    def tracks(self, artist: str, album: str) -> list[Track]:
        found = [t for t in self._by_artist.get(artist, []) if t.album == album]
        return sorted(found, key=lambda t: (t.disc_no, t.track_no, t.title.lower()))

    def album_year(self, artist: str, album: str) -> int:
        for track in self._by_artist.get(artist, []):
            if track.album == album and track.year:
                return track.year
        return 0

    def search(self, query: str) -> list[Track]:
        """Substring match across title, artist, album and genre."""
        needle = query.strip().lower()
        if not needle:
            return []
        return [
            t
            for t in self._tracks
            if needle in t.title.lower()
            or needle in t.artist.lower()
            or needle in t.albumartist.lower()
            or needle in t.album.lower()
            or needle in t.genre.lower()
        ]

    def filter(self, field: str, value: str) -> list[Track]:
        """Field filter, e.g. filter("genre", "Celtic Folk")."""
        needle = value.strip().lower()
        return [
            t for t in self._tracks
            if needle in str(getattr(t, field, "")).lower()
        ]

    def by_id(self, track_id: int) -> Track | None:
        for track in self._tracks:
            if track.id == track_id:
                return track
        return None


def _first_int(value: str) -> int:
    """Parse the leading integer out of a tag like "3/12" or "1984-01-01"."""
    digits = ""
    for char in str(value):
        if char.isdigit():
            digits += char
        elif digits:
            break
    try:
        return int(digits)
    except ValueError:
        return 0
