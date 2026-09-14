"""Watch the music folder and import new albums automatically.

Drop an album into ~/Music and DJ-Skippy notices, waits for the copy to finish,
looks it up on MusicBrainz and tags it - without you asking.

Two details make this work rather than merely appear to:

Debouncing. A folder appearing is not an album arriving; files land one at a
time over seconds or minutes. The watcher therefore waits until a directory has
been *quiet* for `settle_seconds` before touching it, so a half-copied album is
never imported.

Confidence gating. Auto-applying whatever MusicBrainz returns first is how
libraries get quietly corrupted. Anything at or above `auto_threshold`
similarity is applied unattended; anything below is parked in a review queue for
a human. Nothing is ever silently guessed at.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .library import AUDIO_SUFFIXES

# Similarity at or above which an album is tagged without asking. 90% is the
# level at which matches are reliably correct once the data_source penalty is
# disabled; below it, differences are usually real rather than cosmetic.
DEFAULT_AUTO_THRESHOLD = 90.0

# How long a directory must be quiet before we consider the copy finished.
DEFAULT_SETTLE_SECONDS = 20.0


@dataclass
class PendingAlbum:
    """A directory waiting to settle, or waiting for a human."""

    path: Path
    last_change: float = field(default_factory=time.time)
    audio_count: int = 0
    reason: str = "settling"


class MusicWatcher:
    """Filesystem watcher that feeds new albums to the tagger."""

    def __init__(
        self,
        music_dir: Path,
        known_paths: Callable[[], set[str]],
        on_ready: Callable[[Path], None],
        on_status: Callable[[str], None] | None = None,
        settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    ) -> None:
        self.music_dir = Path(music_dir)
        self._known_paths = known_paths
        self._on_ready = on_ready
        self._on_status = on_status or (lambda msg: None)
        self.settle_seconds = settle_seconds

        self._pending: dict[Path, PendingAlbum] = {}
        self._lock = threading.Lock()
        self._observer = None
        self._timer: threading.Thread | None = None
        self._stop = threading.Event()
        self.running = False
        self.error: str | None = None
        self.imported_count = 0

    # -- lifecycle -------------------------------------------------------

    def start(self) -> bool:
        if self.running:
            return True
        if not self.music_dir.is_dir():
            self.error = f"{self.music_dir} is not a directory"
            return False

        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer

            watcher = self

            class Handler(FileSystemEventHandler):
                def on_created(self, event):
                    watcher._touch(event.src_path, event.is_directory)

                def on_moved(self, event):
                    watcher._touch(
                        getattr(event, "dest_path", event.src_path),
                        event.is_directory,
                    )

                def on_modified(self, event):
                    # Only interesting for files - a directory mtime bump on
                    # its own does not mean new audio arrived.
                    if not event.is_directory:
                        watcher._touch(event.src_path, False)

            self._observer = Observer()
            self._observer.schedule(Handler(), str(self.music_dir), recursive=True)
            self._observer.start()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return False

        self._stop.clear()
        self._timer = threading.Thread(
            target=self._settle_loop, name="dj-skippy-watcher", daemon=True
        )
        self._timer.start()
        self.running = True
        self.error = None
        return True

    def stop(self) -> None:
        self._stop.set()
        self.running = False
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2)
            except Exception:
                pass
            self._observer = None

    # -- event handling --------------------------------------------------

    def _touch(self, raw_path: str, is_directory: bool) -> None:
        """Record activity against the album directory containing this path."""
        try:
            path = Path(raw_path)
        except Exception:
            return

        if is_directory:
            album_dir = path
        else:
            if path.suffix.lower() not in AUDIO_SUFFIXES:
                return
            album_dir = path.parent

        # Ignore the library root itself and anything outside it.
        try:
            if album_dir == self.music_dir or self.music_dir not in album_dir.parents:
                if album_dir != self.music_dir:
                    return
        except Exception:
            return
        if album_dir == self.music_dir:
            return

        with self._lock:
            entry = self._pending.get(album_dir)
            if entry is None:
                entry = PendingAlbum(path=album_dir)
                self._pending[album_dir] = entry
            entry.last_change = time.time()
            entry.reason = "settling"

    def _settle_loop(self) -> None:
        while not self._stop.wait(2.0):
            now = time.time()
            ready: list[Path] = []

            with self._lock:
                for path, entry in list(self._pending.items()):
                    if entry.reason != "settling":
                        continue
                    if now - entry.last_change < self.settle_seconds:
                        continue
                    if not path.is_dir():
                        self._pending.pop(path, None)
                        continue

                    audio = self._audio_files(path)
                    entry.audio_count = len(audio)
                    if not audio:
                        # A directory with no audio in it is not an album -
                        # it is probably a parent folder being created.
                        self._pending.pop(path, None)
                        continue

                    known = self._known_paths()
                    if all(str(f) in known for f in audio):
                        # Already in the library; nothing to do.
                        self._pending.pop(path, None)
                        continue

                    entry.reason = "importing"
                    ready.append(path)

            for path in ready:
                self._on_status(f"new album detected: {path.name}")
                try:
                    self._on_ready(path)
                except Exception as exc:
                    self._on_status(f"auto-import failed for {path.name}: {exc}")
                finally:
                    with self._lock:
                        self._pending.pop(path, None)

    @staticmethod
    def _audio_files(directory: Path) -> list[Path]:
        try:
            return sorted(
                p for p in directory.iterdir()
                if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
            )
        except OSError:
            return []

    # -- introspection ---------------------------------------------------

    def pending(self) -> list[PendingAlbum]:
        with self._lock:
            return list(self._pending.values())

    def status_line(self) -> str:
        if not self.running:
            return "watcher off"
        count = len(self.pending())
        if count:
            return f"watching · {count} settling"
        return "watching"
