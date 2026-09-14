"""Music library.

Reads the beets database when one exists - that is where the MusicBrainz tags
live - and falls back to scanning the music folder with mutagen when it does
not, so DJ-Skippy is useful before anything has been imported.

A note on beets paths, learned the hard way: beets 2.x stores item paths
*relative* to the library directory and expands them via a ContextVar. That
ContextVar is per-thread, so any thread that did not construct the Library
reads it back empty and gets relative paths. Every access here goes through
`_bound()`, which rebinds it. Forget that and you get paths resolved against
whatever the working directory happens to be.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

AUDIO_SUFFIXES = {
    ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".wv",
    ".ape", ".mpc", ".alac", ".aiff", ".aif", ".dsf", ".dff", ".wma", ".mka",
    ".m4b", ".tta", ".shn", ".caf", ".au", ".ra", ".mp2", ".mod", ".xm",
    ".s3m", ".it", ".spx", ".mp4",
}


@dataclass(frozen=True)
class Track:
    """One playable item.

    `tagged` records whether this came from beets - that is, whether it
    carries MusicBrainz metadata - or was read straight from the file. The
    distinction is shown in the interface rather than hidden: music plays
    either way, and knowing which is which is the user's business.
    """

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
    tagged: bool = False

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
    "The Pogues" files under P - cmus's smart_artist_sort."""
    key = name.strip().lower()
    if smart:
        for article in ("the ", "a ", "an "):
            if key.startswith(article):
                key = key[len(article):]
                break
    return key


class Library:
    """Artist -> album -> track view over whichever backend is available."""

    def __init__(self, music_dir: Path, smart_sort: bool = True,
                 scan_disk: bool = True, use_beets: bool = True) -> None:
        self.music_dir = Path(music_dir)
        self.smart_sort = smart_sort
        #: When False, the beets database is ignored entirely and everything
        #: comes from the files. DJ-Skippy is a player first; MusicBrainz is
        #: an enhancement you can decline.
        self.use_beets = use_beets
        self._tracks: list[Track] = []
        self._beets_lib = None
        self.backend = "empty"
        self.error: str | None = None
        #: How much of the library carries MusicBrainz metadata.
        self.tagged_count = 0
        self.untagged_count = 0
        self.load(scan_disk=scan_disk)

    # -- loading ---------------------------------------------------------

    def load(self, scan_disk: bool = True) -> None:
        """(Re)load the library.

        Everything on disk is included, whether or not beets knows about it.
        MusicBrainz data is layered over the files that have it rather than
        replacing the rest - the previous behaviour meant an unimported track
        was simply invisible, which is the wrong way round for a music player:
        the music you own is the library, and tagging is an enhancement.
        """
        tagged = {t.path: t for t in self._load_from_beets()}

        untagged: list[Track] = []
        if scan_disk:
            untagged = [
                t for t in self._load_from_filesystem() if t.path not in tagged
            ]

        self._tracks = list(tagged.values()) + untagged
        self.tagged_count = len(tagged)
        self.untagged_count = len(untagged)

        if tagged and untagged:
            self.backend = "beets + disk"
        elif tagged:
            self.backend = "beets"
        elif untagged:
            self.backend = "disk"
        else:
            self.backend = "empty"
        self._index()

    def merge_from_disk(self) -> int:
        """Add anything on disk that is not already known. Returns how many.

        Split out so the interface can show the tagged library immediately -
        which loads in a fraction of a second - and fill in the rest without
        making the user wait for it.
        """
        known = {t.path for t in self._tracks}
        found = [t for t in self._load_from_filesystem() if t.path not in known]
        if not found:
            return 0
        self._tracks.extend(found)
        self.untagged_count += len(found)
        self.backend = "beets + disk" if self.tagged_count else "disk"
        self._index()
        return len(found)

    @contextmanager
    def _bound(self) -> Iterator[None]:
        """Bind the beets music-dir ContextVar for the current thread.

        See the module docstring - without this, item paths come back relative
        and nothing plays.
        """
        try:
            from beets import context

            if self._beets_lib is not None:
                context.set_music_dir(self._beets_lib.directory)
        except Exception:
            pass
        yield

    def _load_from_beets(self) -> list[Track]:
        if not self.use_beets:
            return []
        try:
            from beets import config as beets_config
            from beets.library import Library as BeetsLibrary

            beets_config.read()
            db_path = beets_config["library"].as_filename()
            if not os.path.exists(db_path):
                return []

            directory = beets_config["directory"].as_filename()

            # The beets database describes *its* directory and nothing else.
            # Using it for any other folder means --music-dir is silently
            # ignored and pointing DJ-Skippy at a USB drive shows the wrong
            # library entirely.
            try:
                same = os.path.samefile(directory, self.music_dir)
            except OSError:
                same = os.path.normpath(directory) == os.path.normpath(
                    str(self.music_dir)
                )
            if not same:
                self.error = (
                    f"beets manages {directory}, not {self.music_dir} — "
                    "scanning the folder directly"
                )
                return []

            self._beets_lib = BeetsLibrary(db_path, directory)

            tracks: list[Track] = []
            with self._bound():
                for item in self._beets_lib.items():
                    path = os.fsdecode(item.path)
                    if not os.path.isabs(path):
                        # Belt and braces: expand manually if the ContextVar
                        # did not take for any reason.
                        path = os.path.join(directory, path)

                    # item.get() rather than attribute access throughout:
                    # beets 2.14 turned `genre` into a multi-value field, and
                    # item.genre now raises AttributeError on items that have
                    # none. Attribute access makes every field a landmine on a
                    # version bump; .get() with a default does not.
                    artist = _text(item, "artist") or "Unknown Artist"
                    tracks.append(
                        Track(
                            id=int(item.id),
                            path=path,
                            title=_text(item, "title") or Path(path).stem,
                            artist=artist,
                            albumartist=_text(item, "albumartist") or artist,
                            album=_text(item, "album") or "Unknown Album",
                            track_no=_number(item, "track"),
                            disc_no=_number(item, "disc"),
                            length=float(item.get("length", 0.0) or 0.0),
                            year=_number(item, "year"),
                            genre=_text(item, "genre") or _text(item, "genres"),
                            format=(
                                _text(item, "format") or Path(path).suffix.lstrip(".")
                            ).upper(),
                            tagged=True,
                        )
                    )
            return tracks
        except Exception as exc:
            self.error = f"beets backend unavailable: {type(exc).__name__}: {exc}"
            self._beets_lib = None
            return []

    def _load_from_filesystem(self) -> list[Track]:
        """Fallback scan. Slower and dumber, but means DJ-Skippy works on a
        folder of untagged files."""
        if not self.music_dir.exists():
            return []
        try:
            import mutagen
        except ImportError:
            mutagen = None  # type: ignore[assignment]

        tracks: list[Track] = []
        next_id = 1
        for path in sorted(self.music_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
                continue

            title, artist, album = path.stem, "Unknown Artist", "Unknown Album"
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
                        artist = first("artist", "Unknown Artist")
                        album = first("album", "Unknown Album")
                        albumartist = first("albumartist", artist)
                        genre = first("genre")
                        track_no = _first_int(first("tracknumber"))
                        disc_no = _first_int(first("discnumber"))
                        year = _first_int(first("date"))
                        if meta.info is not None:
                            length = float(getattr(meta.info, "length", 0.0) or 0.0)
                except Exception:
                    pass

            # Untagged files are common outside a managed library - a USB
            # stick, a download. Artist/Album/Track is the near-universal
            # layout, so read the structure rather than filing everything
            # under "Unknown Artist" and making the browser useless.
            if artist == "Unknown Artist" or album == "Unknown Album":
                inferred_artist, inferred_album = self._infer_from_path(path)
                if artist == "Unknown Artist" and inferred_artist:
                    artist = inferred_artist
                if album == "Unknown Album" and inferred_album:
                    album = inferred_album

            tracks.append(
                Track(
                    id=next_id,
                    path=str(path),
                    title=title,
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

        Expects .../Artist/Album/track. A disc folder is stepped over, so
        .../Artist/Album/CD1/track still reports the album rather than "CD1".
        """
        try:
            relative = path.relative_to(self.music_dir)
        except ValueError:
            return "", ""

        parts = list(relative.parts[:-1])  # drop the filename
        if not parts:
            return "", ""

        from .maintenance import is_disc_folder

        while len(parts) > 1 and is_disc_folder(Path(parts[-1])):
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

    def tagged_state(self, artist: str) -> str:
        """"all", "none" or "some" of this artist carries MusicBrainz data."""
        tracks = self._by_artist.get(artist, [])
        if not tracks:
            return "none"
        tagged = sum(1 for t in tracks if t.tagged)
        if tagged == len(tracks):
            return "all"
        return "none" if tagged == 0 else "some"

    def albums(self, artist: str) -> list[str]:
        """Albums by an artist, oldest first - the order a listener thinks in."""
        tracks = self._by_artist.get(artist, [])
        seen: dict[str, int] = {}
        for track in tracks:
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
        """cmus-style field filter, e.g. filter("genre", "Celtic Folk")."""
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


def _text(item: Any, field: str) -> str:
    """Read a possibly-absent, possibly-multi-valued beets field as a string."""
    try:
        value = item.get(field, "")
    except Exception:
        return ""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v) for v in value if v)
    return str(value)


def _number(item: Any, field: str) -> int:
    try:
        return int(item.get(field, 0) or 0)
    except (TypeError, ValueError):
        return 0


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
