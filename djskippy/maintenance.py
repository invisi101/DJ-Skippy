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

import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence, TYPE_CHECKING

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


def scan_album_folders(music_dir: Path) -> list[AlbumFolder]:
    """Every directory under music_dir that directly contains audio files.

    Directly, not recursively: an artist folder holding album subfolders is not
    itself an album, but a multi-disc release's CD1/CD2 folders are each
    treated as one, which is what beets expects.
    """
    found: list[AlbumFolder] = []
    music_dir = Path(music_dir)
    if not music_dir.is_dir():
        return found

    for directory in sorted(
        p for p in music_dir.rglob("*") if p.is_dir()
    ):
        try:
            count = sum(
                1
                for entry in directory.iterdir()
                if entry.is_file() and entry.suffix.lower() in AUDIO_SUFFIXES
            )
        except OSError:
            continue
        if count:
            found.append(AlbumFolder(path=directory, audio_count=count))

    # The music folder itself may hold loose files.
    try:
        loose = sum(
            1
            for entry in music_dir.iterdir()
            if entry.is_file() and entry.suffix.lower() in AUDIO_SUFFIXES
        )
        if loose:
            found.append(AlbumFolder(path=music_dir, audio_count=loose))
    except OSError:
        pass

    return found


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
        try:
            files = [
                str(entry)
                for entry in folder.path.iterdir()
                if entry.is_file() and entry.suffix.lower() in AUDIO_SUFFIXES
            ]
        except OSError:
            continue
        folder.imported = sum(1 for f in files if f in known)
        if folder.fully_imported:
            continue
        if folder.partially_imported and not include_partial:
            continue
        out.append(folder)
    return out


def check_musicbrainz(timeout: float = 12.0) -> tuple[bool, str]:
    """Is MusicBrainz answering right now?

    Returns (healthy, message). Their server returns HTTP 503 with a "currently
    busy" body under load, and beets surfaces that as "no matching release
    found" - indistinguishable, from the outside, from an album that genuinely
    is not in the database. Checking directly lets us tell the user the truth
    instead of quietly filing half their library under "needs review".
    """
    import json
    import urllib.error
    import urllib.request

    url = (
        "https://musicbrainz.org/ws/2/release/"
        "?query=release:%22Abbey%20Road%22%20AND%20artist:%22The%20Beatles%22"
        "&fmt=json&limit=1"
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": "DJ-Skippy/1.0 (health check)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
        data = json.loads(body)
        if "error" in data:
            return False, str(data["error"])
        if data.get("releases"):
            return True, "MusicBrainz is responding"
        return False, "MusicBrainz returned no results for a known album"
    except urllib.error.HTTPError as exc:
        if exc.code == 503:
            return False, "MusicBrainz is rate-limiting or overloaded (503)"
        return False, f"MusicBrainz returned HTTP {exc.code}"
    except Exception as exc:
        return False, f"cannot reach MusicBrainz: {type(exc).__name__}"


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
