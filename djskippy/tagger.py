"""MusicBrainz tagging, driven from inside the TUI.

This embeds beets' own importer rather than shelling out to `beet import`, by
subclassing ImportSession and overriding the decision hooks. That means
DJ-Skippy inherits the user's beets configuration exactly - including the
`data_source_mismatch_penalty` fix, the `languages: [en]` setting, and the
non-destructive `copy: no` / `move: no` defaults.

Threading: beets' importer is a synchronous pipeline, so it runs on a worker
thread. Its decision hooks block on a threading.Event until the UI answers,
which is exactly the behaviour the pipeline expects from an interactive
session.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import locking


@dataclass
class TrackChange:
    """One track's before/after, for display."""

    index: int
    old: str
    new: str
    changed: bool
    length: str = ""


@dataclass
class Candidate:
    """A proposed MusicBrainz match, flattened for the UI."""

    album: str
    artist: str
    distance: float
    url: str
    info_line: str
    changes: list[TrackChange] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)

    @property
    def similarity(self) -> float:
        return (1.0 - self.distance) * 100.0


@dataclass
class TaggerRequest:
    """A decision the importer is waiting on."""

    path: str
    item_count: int
    candidates: list[Candidate]
    is_duplicate: bool = False
    duplicate_info: str = ""


class Tagger:
    """Runs a beets import and surfaces decisions to the UI."""

    def __init__(self, on_request: Callable[[TaggerRequest], None],
                 on_log: Callable[[str], None] | None = None) -> None:
        self._on_request = on_request
        self._on_log = on_log or (lambda msg: None)

        self._answer: Any = None
        self._answered = threading.Event()
        self._thread: threading.Thread | None = None
        self._abort = False

        self.running = False
        self.current: TaggerRequest | None = None
        self.error: str | None = None
        self.stats = {"imported": 0, "skipped": 0, "asis": 0, "retried": 0}

        # Progress across a bulk run.
        self.total_paths = 0
        self.decisions = 0
        self.current_album = ""

    # -- public API ------------------------------------------------------

    def start(self, paths: list[str]) -> bool:
        """Begin importing the given paths. Returns False if already running."""
        if self.running:
            return False
        self._abort = False
        self.error = None
        self.stats = {"imported": 0, "skipped": 0, "asis": 0, "retried": 0}
        self.total_paths = len(paths)
        self.decisions = 0
        self.current_album = ""
        self._thread = threading.Thread(
            target=self._run, args=(paths,), name="dj-skippy-tagger", daemon=True
        )
        self.running = True
        self._thread.start()
        return True

    def respond(self, decision: Any) -> None:
        """Answer the pending decision. `decision` is "apply"/"skip"/"asis",
        or an integer index selecting one of the candidates."""
        self._answer = decision
        self._answered.set()

    def abort(self) -> None:
        self._abort = True
        self.respond("skip")

    @property
    def progress(self) -> str:
        """Human-readable progress for a bulk run."""
        if not self.running and not self.decisions:
            return ""
        stats = self.stats
        head = f"{self.decisions}/{self.total_paths}" if self.total_paths > 1 else ""
        return (
            f"{head}  tagged {stats['imported']}  "
            f"as-is {stats['asis']}  skipped {stats['skipped']}"
        ).strip()

    # -- importer plumbing -----------------------------------------------

    def _run(self, paths: list[str]) -> None:
        if not locking.acquire("import"):
            self.error = (
                f"another import is already running: {locking.describe_holder()}"
            )
            self._on_log(self.error)
            self.running = False
            return
        try:
            from beets import config as beets_config
            from beets import importer, plugins
            from beets.library import Library as BeetsLibrary
            from beets.util import bytestring_path

            beets_config.read()
            plugins.load_plugins()
            plugins.find_plugins()

            # beets' incremental mode records every directory it has *seen*,
            # skipped ones included, and never offers them again. That is
            # wrong here: DJ-Skippy decides what needs importing by checking
            # actual library membership, which is both more accurate and
            # re-checkable. Left on, an album skipped once during a
            # MusicBrainz outage could never be retried - 46 folders were
            # stuck exactly that way.
            beets_config["import"]["incremental"] = False
            beets_config["import"]["incremental_skip_later"] = True

            lib = BeetsLibrary(
                beets_config["library"].as_filename(),
                beets_config["directory"].as_filename(),
            )

            session = _TuiImportSession(
                lib,
                None,
                [bytestring_path(str(Path(p).expanduser())) for p in paths],
                None,
                tagger=self,
            )
            session.run()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self._on_log(f"tagger error: {self.error}")
        finally:
            locking.release()
            self.running = False
            self.current = None

    def _ask(self, request: TaggerRequest) -> Any:
        """Publish a decision request and block until the UI answers."""
        if self._abort:
            return "skip"
        self.decisions += 1
        self.current_album = Path(request.path).name
        self.current = request
        self._answered.clear()
        self._on_request(request)
        # No timeout: a human may reasonably take a while. abort() unblocks it.
        self._answered.wait()
        self.current = None
        return self._answer


def _format_track_changes(match: Any) -> list[TrackChange]:
    """Build the before/after list from a beets AlbumMatch."""
    changes: list[TrackChange] = []
    mapping = getattr(match, "mapping", {}) or {}
    for index, (item, track_info) in enumerate(mapping.items(), start=1):
        old = item.title or ""
        new = track_info.title or ""
        length = ""
        if getattr(track_info, "length", None):
            minutes, seconds = divmod(int(track_info.length), 60)
            length = f"{minutes}:{seconds:02d}"
        changes.append(
            TrackChange(
                index=int(getattr(track_info, "index", index) or index),
                old=old,
                new=new,
                changed=(old != new),
                length=length,
            )
        )
    changes.sort(key=lambda c: c.index)
    return changes


def _build_candidate(match: Any) -> Candidate:
    info = match.info
    parts = [
        str(getattr(info, "data_source", "") or "MusicBrainz"),
        str(getattr(info, "media", "") or ""),
        str(getattr(info, "year", "") or ""),
        str(getattr(info, "country", "") or ""),
        str(getattr(info, "label", "") or ""),
        str(getattr(info, "catalognum", "") or ""),
    ]
    album_id = getattr(info, "album_id", "") or ""
    return Candidate(
        album=str(getattr(info, "album", "") or "Unknown"),
        artist=str(getattr(info, "artist", "") or "Unknown"),
        distance=float(getattr(match.distance, "distance", 1.0)),
        url=f"https://musicbrainz.org/release/{album_id}" if album_id else "",
        info_line=", ".join(p for p in parts if p),
        changes=_format_track_changes(match),
        missing=[
            str(t.title) for t in (getattr(match, "extra_tracks", None) or [])
        ],
        extra=[
            str(i.title) for i in (getattr(match, "extra_items", None) or [])
        ],
    )


class _TuiImportSession:
    """Constructed lazily so `beets` is only imported inside the worker."""

    def __new__(cls, lib, loghandler, paths, query, tagger: Tagger):
        from beets import importer

        class Session(importer.ImportSession):
            def should_resume(self, path) -> bool:
                # Always start clean; resuming half-finished sessions from a
                # TUI is more confusing than useful.
                return False

            def choose_match(self, task):
                from beets.importer import Action

                candidates = [_build_candidate(m) for m in (task.candidates or [])]
                request = TaggerRequest(
                    path=_task_path(task),
                    item_count=len(task.items or []),
                    candidates=candidates,
                )
                answer = tagger._ask(request)

                if answer == "rescan":
                    # Re-run the lookup for this same album. This is what the
                    # R key does at beets' own prompt, and it is the correct
                    # response to MusicBrainz returning 503 - the album is
                    # usually found on a later attempt.
                    tagger.stats["retried"] += 1
                    return Action.RESCAN
                if answer == "skip" or answer is None:
                    tagger.stats["skipped"] += 1
                    return Action.SKIP
                if answer == "asis":
                    tagger.stats["asis"] += 1
                    return Action.ASIS
                index = 0 if answer == "apply" else int(answer)
                if 0 <= index < len(task.candidates or []):
                    tagger.stats["imported"] += 1
                    return task.candidates[index]
                tagger.stats["skipped"] += 1
                return Action.SKIP

            def choose_item(self, task):
                from beets.importer import Action

                # Singletons: accept the best match, or skip if there is none.
                if task.candidates:
                    return task.candidates[0]
                return Action.SKIP

            def get_duplicate_action(self, task, found_duplicates):
                from beets.importer import DuplicateAction

                names = ", ".join(
                    str(getattr(d, "album", getattr(d, "title", "?")))
                    for d in found_duplicates[:3]
                )
                request = TaggerRequest(
                    path=_task_path(task),
                    item_count=len(task.items or []),
                    candidates=[],
                    is_duplicate=True,
                    duplicate_info=names,
                )
                answer = tagger._ask(request)
                # Default to KEEP: never destroy an existing copy on a guess.
                return {
                    "skip": DuplicateAction.SKIP,
                    "keep": DuplicateAction.KEEP,
                    "merge": DuplicateAction.MERGE,
                    "upgrade": DuplicateAction.UPGRADE,
                }.get(str(answer), DuplicateAction.KEEP)

        return Session(lib, loghandler, paths, query)


def _task_path(task: Any) -> str:
    try:
        paths = getattr(task, "paths", None)
        if paths:
            import os

            return os.fsdecode(paths[0])
    except Exception:
        pass
    return "(unknown)"
