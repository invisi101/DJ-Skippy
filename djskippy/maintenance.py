"""Library maintenance — everything you would otherwise open a terminal for.

Two kinds of operation live here:

`find_unimported_albums` walks the music folder and works out which album
directories beets does not know about yet. That is what makes a one-key "import
my whole library" possible, instead of tagging folders one at a time.

`BeetsCommand` runs beets' own plugin subcommands (duplicates, missing,
mbsync, fetchart, write) and captures their output for display. These are
plugin commands rather than library calls, so a subprocess is the honest way to
invoke them - but the user never types one.
"""

from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence, TYPE_CHECKING

from . import locking
from .library import AUDIO_SUFFIXES

if TYPE_CHECKING:
    from .library import Library


@dataclass
class AlbumFolder:
    """A directory on disk that looks like an album."""

    path: Path
    audio_count: int
    imported: int = 0

    @property
    def fully_imported(self) -> bool:
        return self.audio_count > 0 and self.imported >= self.audio_count

    @property
    def partially_imported(self) -> bool:
        return 0 < self.imported < self.audio_count

    @property
    def label(self) -> str:
        try:
            name = f"{self.path.parent.name}/{self.path.name}"
        except Exception:
            name = str(self.path)
        if self.partially_imported:
            state = f"{self.imported}/{self.audio_count} tagged"
        else:
            state = f"{self.audio_count} tracks"
        return f"{name}   ({state})"


#: Folder names that denote a disc within a release rather than a release.
#: "CD1", "Disc 2", "CD 2 (320)", "CD 1 (L)", "Vol. 3", or a bare number.
DISC_PATTERN = re.compile(
    # A bare number is a disc only at 1-2 digits. Longer ones are years,
    # and "1999" is an album rather than disc one thousand nine hundred
    # and ninety-nine.
    r"^(?:(?:cd|disc|disk|vol(?:ume)?)[\s._-]*\d+|\d{1,2})"
    # Optional trailing detail, but only when clearly separated:
    # "CD 2 (320)", "CD 1 [L]", "CD2 - Live In Madrid", "Disc 1 — Bonus".
    r"\s*(?:\(.*\)|\[.*\]|[-–—:]\s*.+)?$",
    re.IGNORECASE,
)


def is_disc_folder(path: Path) -> bool:
    """Is this folder one disc of a set, rather than an album in itself?"""
    return bool(DISC_PATTERN.match(Path(path).name.strip()))


def _audio_count(directory: Path) -> int:
    try:
        return sum(
            1
            for entry in directory.iterdir()
            if entry.is_file() and entry.suffix.lower() in AUDIO_SUFFIXES
        )
    except OSError:
        return 0


def audio_files_for(folder: Path) -> list[str]:
    """The audio belonging to one album folder.

    Usually the files directly inside it. For a collapsed multi-disc parent
    there are none there, so the disc subdirectories are used instead -
    without that, such an album could never be recognised as imported and
    would be offered again on every scan.
    """
    folder = Path(folder)
    try:
        direct = [
            str(entry)
            for entry in folder.iterdir()
            if entry.is_file() and entry.suffix.lower() in AUDIO_SUFFIXES
        ]
    except OSError:
        return []
    # Disc subfolders belong to this album too, however deeply they nest -
    # one real rip has "Greatest Hits/Disc 1" holding both its own tracks and
    # Disc 2, 3 and 4 beneath it.
    nested: list[str] = []

    def walk(node: Path) -> None:
        try:
            children = sorted(node.iterdir())
        except OSError:
            return
        for child in children:
            if not child.is_dir() or not is_disc_folder(child):
                continue
            try:
                nested.extend(
                    str(entry)
                    for entry in sorted(child.iterdir())
                    if entry.is_file()
                    and entry.suffix.lower() in AUDIO_SUFFIXES
                )
            except OSError:
                continue
            walk(child)

    walk(folder)
    return direct + nested


def scan_album_folders(music_dir: Path) -> list[AlbumFolder]:
    """Every directory that represents one album.

    Directories holding audio directly are albums - except when they are the
    discs of a set. `Sigh No More (2CD)/CD 1` and `.../CD 2` are two halves of
    one release, and handing them to beets separately is actively harmful:
    each disc is matched against the *complete* release, so half the tracks
    look missing, the score lands near 70%, and the album ends up in review
    or, worse, imported as two unrelated albums.

    So a parent whose audio-bearing children are all disc folders is returned
    in their place. beets understands multi-disc directories natively when
    given the parent.
    """
    music_dir = Path(music_dir)
    if not music_dir.is_dir():
        return []

    with_audio: dict[Path, int] = {}
    try:
        candidates = [p for p in music_dir.rglob("*") if p.is_dir()]
    except OSError:
        candidates = []

    for directory in candidates:
        count = _audio_count(directory)
        if count:
            with_audio[directory] = count

    # Collapse disc folders into the album they belong to. For each one, walk
    # up past any further disc-named ancestors until a real album folder is
    # reached - that is the unit to hand beets.
    handled: set[Path] = set()
    anchors: set[Path] = set()

    for directory in sorted(with_audio):
        if not is_disc_folder(directory):
            continue

        anchor = directory.parent
        while anchor != music_dir and is_disc_folder(anchor):
            anchor = anchor.parent
        if anchor == music_dir:
            # A disc folder sitting loose at the top level has no album to
            # belong to; leave it as its own unit rather than swallowing the
            # entire library into one import.
            continue

        anchors.add(anchor)
        handled.add(directory)

    # The anchor itself may hold audio directly (an album with a bonus disc),
    # in which case it is replaced rather than duplicated.
    handled |= anchors

    found = [
        AlbumFolder(path=path, audio_count=count)
        for path, count in with_audio.items()
        if path not in handled
    ]
    for anchor in anchors:
        found.append(
            AlbumFolder(path=anchor, audio_count=len(audio_files_for(anchor)))
        )

    # The music folder itself may hold loose files.
    loose = _audio_count(music_dir)
    if loose:
        found.append(AlbumFolder(path=music_dir, audio_count=loose))

    return sorted(found, key=lambda f: str(f.path).lower())


def find_unimported_albums(
    music_dir: Path, library: "Library", include_partial: bool = True
) -> list[AlbumFolder]:
    """Album folders beets has not fully taken in.

    `include_partial` keeps folders that are only half-imported, which is the
    usual state after an interrupted run.
    """
    known = {t.path for t in library.all_tracks}
    out: list[AlbumFolder] = []

    for folder in scan_album_folders(music_dir):
        files = audio_files_for(folder.path)
        if not files:
            continue
        folder.imported = sum(1 for f in files if f in known)
        if folder.fully_imported:
            continue
        if folder.partially_imported and not include_partial:
            continue
        out.append(folder)
    return out


#: MusicBrainz asks that clients identify themselves with contact details.
#: https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting
USER_AGENT = "DJ-Skippy/1.0 ( https://github.com/invisi101/DJ-Skippy )"


def _probe_musicbrainz(timeout: float) -> tuple[bool, str]:
    """One request. True if MusicBrainz answered with real data."""
    import json
    import urllib.error
    import urllib.request

    url = (
        "https://musicbrainz.org/ws/2/release/"
        "?query=release:%22Abbey%20Road%22%20AND%20artist:%22The%20Beatles%22"
        "&fmt=json&limit=1"
    )
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        if data.get("releases"):
            return True, "ok"
        if "error" in data:
            return False, str(data["error"])
        return False, "no results for a known album"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}"


def check_musicbrainz(
    timeout: float = 12.0, samples: int = 4, required: int = 1
) -> tuple[bool, str]:
    """Is MusicBrainz usable right now?

    Returns (healthy, message).

    Sampling several times matters. Under load MusicBrainz returns HTTP 503
    for a large fraction of requests - measured at ~50% during development -
    while still being perfectly usable if you retry. A single failed request
    therefore proves nothing, and treating it as "down" would refuse to start
    imports on a service that is merely busy.

    So: healthy means *at least `required` of `samples` succeeded*, not "the
    first one worked". Requests are spaced to respect their 1/second limit.
    """
    import time as _time

    successes = 0
    last_error = "no response"
    for attempt in range(samples):
        ok, detail = _probe_musicbrainz(timeout)
        if ok:
            successes += 1
            if successes >= required:
                if attempt + 1 == successes:
                    return True, "MusicBrainz is responding"
                return True, (
                    f"MusicBrainz is busy but usable "
                    f"({successes}/{attempt + 1} succeeded)"
                )
        else:
            last_error = detail
        if attempt < samples - 1:
            _time.sleep(1.2)

    if "503" in last_error or "busy" in last_error.lower():
        return False, (
            f"MusicBrainz is overloaded — 0/{samples} requests answered. "
            "Their server, not your library"
        )
    return False, f"cannot reach MusicBrainz: {last_error}"


@dataclass
class CommandResult:
    name: str
    lines: list[str] = field(default_factory=list)
    returncode: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.returncode == 0


class BeetsCommand:
    """Runs a beets subcommand off the UI thread and returns its output."""

    #: Operations exposed in the interface, and what they do.
    OPERATIONS: dict[str, tuple[list[str], str]] = {
        "duplicates": (["dup"], "find duplicate tracks and albums"),
        "missing": (["missing"], "albums with tracks missing"),
        "mbsync": (["mbsync"], "re-fetch tags from MusicBrainz for the library"),
        "fetchart": (["fetchart"], "download any missing album art"),
        "write": (["write", "-p"], "preview tag changes that would be written"),
        "stats": (["stats"], "library totals"),
    }

    def __init__(self, on_done: Callable[[CommandResult], None]) -> None:
        self._on_done = on_done
        self.running = False
        self.current: str | None = None
        self._process: subprocess.Popen | None = None

    def start(self, name: str, extra: Sequence[str] = ()) -> bool:
        if self.running:
            return False
        spec = self.OPERATIONS.get(name)
        if spec is None:
            return False

        args, _ = spec
        self.running = True
        self.current = name
        threading.Thread(
            target=self._run,
            args=(name, list(args) + list(extra)),
            name=f"dj-skippy-beet-{name}",
            daemon=True,
        ).start()
        return True

    def _run(self, name: str, args: list[str]) -> None:
        result = CommandResult(name=name)
        # mbsync, fetchart and write all modify the library; dup, missing and
        # stats only read it, but sharing the lock keeps the rule simple.
        if not locking.acquire(name):
            result.error = (
                f"another operation is running: {locking.describe_holder()}"
            )
            self.running = False
            self.current = None
            try:
                self._on_done(result)
            except Exception:
                pass
            return
        try:
            self._process = subprocess.Popen(
                ["beet", *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
            )
            assert self._process.stdout is not None
            for line in self._process.stdout:
                result.lines.append(line.rstrip("\n"))
                # Keep memory bounded on a pathological run.
                if len(result.lines) > 5000:
                    result.lines.append("… output truncated")
                    break
            self._process.wait()
            result.returncode = self._process.returncode or 0
        except FileNotFoundError:
            result.error = "beets is not installed"
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
        finally:
            locking.release()
            self.running = False
            self.current = None
            self._process = None
            try:
                self._on_done(result)
            except Exception:
                pass

    def abort(self) -> None:
        if self._process is not None:
            try:
                self._process.terminate()
            except Exception:
                pass
