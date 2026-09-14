"""Playback engine.

Wraps libmpv via python-mpv, which is what buys us "plays any format" for free
- mpv decodes everything ffmpeg does, gaplessly, with ReplayGain support.

The queue/playlist split is lifted from cmus and is the single best idea in
that program: the *playlist* is what you are listening to, the *queue* is what
you have asked to hear next. Queued tracks jump ahead, and once the queue
drains the playlist carries on from where it was.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Sequence

from .library import Track


class RepeatMode(str, Enum):
    OFF = "off"
    ALL = "all"
    ONE = "one"


@dataclass
class PlayerState:
    track: Track | None = None
    playing: bool = False
    paused: bool = False
    position: float = 0.0
    duration: float = 0.0
    volume: int = 80
    muted: bool = False
    shuffle: bool = False
    repeat: RepeatMode = RepeatMode.OFF
    replaygain: str = "smart"


class Player:
    """mpv-backed player with a cmus-style queue and playlist."""

    def __init__(
        self,
        volume: int = 80,
        replaygain: str = "smart",
        rewind_offset: int = 5,
        continue_playback: bool = True,
        on_change: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        import mpv

        self.state = PlayerState(volume=volume, replaygain=replaygain)
        self.rewind_offset = rewind_offset
        self.continue_playback = continue_playback
        self._on_change = on_change or (lambda: None)
        self._on_error = on_error or (lambda msg: None)

        self._playlist: list[Track] = []
        self._queue: list[Track] = []
        self._index = -1
        self._history: list[int] = []
        self._lock = threading.RLock()
        self._advancing = False
        #: Consecutive unplayable tracks, so a playlist of broken files does
        #: not spin through itself at full speed.
        self._failures = 0
        self.failed_paths: list[str] = []

        #: What we have handed to mpv, mirroring its internal playlist.
        #: Gapless playback requires mpv to already hold the next file when
        #: the current one ends - loading it on EOF, as this used to, leaves
        #: roughly a third of a second of silence, which is ruinous on an
        #: album that segues.
        self._mpv_entries: list[Track] = []
        self._mpv_pos = 0
        #: Our playlist index corresponding to each mpv entry.
        self._entry_index: list[int] = []
        #: Set while pruning mpv's playlist, so the resulting position change
        #: is not mistaken for the music moving on.
        self._trimming = False

        self._mpv = mpv.MPV(
            video=False,
            audio_display=False,
            gapless_audio="weak",
            prefetch_playlist=True,
            keep_open="no",
            idle=True,
            terminal=False,
            input_default_bindings=False,
            osc=False,
            ytdl=False,
        )
        self._mpv.volume = volume
        self._apply_replaygain(replaygain)

        # mpv fires these on its own thread; everything they touch is guarded.
        @self._mpv.property_observer("time-pos")
        def _observe_position(_name, value):  # pragma: no cover - callback
            if value is not None:
                if value > 0.5:
                    # Real playback, so whatever came before is forgiven.
                    self._failures = 0
                self.state.position = float(value)
                self._on_change()

        @self._mpv.property_observer("duration")
        def _observe_duration(_name, value):  # pragma: no cover - callback
            if value is not None:
                self.state.duration = float(value)
                self._on_change()

        @self._mpv.property_observer("playlist-pos")
        def _observe_playlist_pos(_name, value):  # pragma: no cover - callback
            if value is not None and value >= 0:
                self._on_mpv_moved(int(value))

        @self._mpv.event_callback("end-file")
        def _on_end(event):  # pragma: no cover - callback
            # reason is an *integer* from MpvEventEndFile, not a string.
            # Matching it as text silently matched nothing, which meant a
            # track never advanced at its end and a broken file stalled the
            # playlist forever.
            data = getattr(event, "data", None)
            reason = getattr(data, "reason", None)

            if reason == mpv.MpvEventEndFile.EOF:
                self._failures = 0
                # If mpv had the next file queued it has already moved to it
                # gaplessly, and the playlist-pos observer handles the change.
                # Only step in when this was the last thing it knew about.
                with self._lock:
                    more_queued = len(self._mpv_entries) - self._mpv_pos > 1
                if not more_queued:
                    self._handle_track_end()
            elif reason == mpv.MpvEventEndFile.ERROR:
                # Unplayable: corrupt file, unsupported codec, dead mount.
                # Skipping is the only useful response.
                self._note_failure()
            # ABORTED, QUIT and REDIRECT are our own doing or mpv's, and must
            # not trigger an advance.

    # -- gapless handover -------------------------------------------------

    def _peek_after(self, index: int) -> tuple[Track | None, int]:
        """What would follow our playlist entry `index`, without changing
        anything. Returns (track, its playlist index, or -1 for a queue item).

        Queued tracks are not consulted here: the queue is meant to interrupt,
        and pre-loading from it would commit to a choice the user may still
        change. The queue is honoured at the moment of handover instead.
        """
        with self._lock:
            if not self._playlist:
                return None, -1
            if self.state.repeat is RepeatMode.ONE:
                return self._playlist[index], index
            if self.state.shuffle:
                import random

                nxt = random.randrange(len(self._playlist))
                return self._playlist[nxt], nxt
            nxt = index + 1
            if nxt >= len(self._playlist):
                if self.state.repeat is RepeatMode.ALL:
                    return self._playlist[0], 0
                return None, -1
            return self._playlist[nxt], nxt

    def _reset_mpv_playlist(self, track: Track, index: int) -> None:
        """Point mpv at one track, discarding whatever it had queued."""
        try:
            self._mpv.command("playlist-clear")
        except Exception:
            pass
        self._mpv_entries = [track]
        self._entry_index = [index]
        self._mpv_pos = 0

    def _queue_upcoming(self) -> None:
        """Hand mpv the track after the last one it knows about.

        This is what makes the handover gapless: mpv opens and buffers the
        next file while the current one is still playing.
        """
        if not self.continue_playback or self._queue:
            # With something queued, the handover is decided at the time and
            # pre-loading would play the wrong thing.
            return
        with self._lock:
            if not self._entry_index:
                return
            if len(self._mpv_entries) - self._mpv_pos > 1:
                return  # already has something lined up
            last_index = self._entry_index[-1]

        upcoming, upcoming_index = self._peek_after(last_index)
        if upcoming is None or not upcoming.exists:
            return
        try:
            self._mpv.command("loadfile", upcoming.path, "append")
        except Exception:
            return
        with self._lock:
            self._mpv_entries.append(upcoming)
            self._entry_index.append(upcoming_index)

    #: How many played entries to let accumulate before pruning mpv's
    #: playlist. Left unchecked it grows for every track of a long session.
    TRIM_AFTER = 24

    def _trim_played(self) -> None:
        """Drop entries mpv has already finished with.

        Removing them shifts playlist-pos, which would otherwise look like a
        track change to the observer, so the trimming flag suppresses that.
        """
        with self._lock:
            drop = self._mpv_pos
            if drop < self.TRIM_AFTER:
                return
            self._trimming = True

        try:
            for _ in range(drop):
                try:
                    self._mpv.command("playlist-remove", "0")
                except Exception:
                    break
            with self._lock:
                self._mpv_entries = self._mpv_entries[drop:]
                self._entry_index = self._entry_index[drop:]
                self._mpv_pos = 0
        finally:
            self._trimming = False

    def _on_mpv_moved(self, pos: int) -> None:
        """mpv advanced to the next file on its own - catch our state up."""
        if self._trimming:
            # Position moved because we removed played entries, not because
            # the music moved on.
            return
        with self._lock:
            if pos is None or not (0 <= pos < len(self._mpv_entries)):
                return
            if pos == self._mpv_pos:
                return
            self._mpv_pos = pos
            track = self._mpv_entries[pos]
            index = self._entry_index[pos]
            if self._index >= 0 and self._index != index:
                self._history.append(self._index)
            self._index = index

        self.state.track = track
        self.state.playing = True
        self.state.paused = False
        self.state.position = 0.0
        self.state.duration = track.length
        self._failures = 0
        self._apply_replaygain(self.state.replaygain)
        self._on_change()
        self._queue_upcoming()
        self._trim_played()

    def _note_failure(self) -> None:
        """A track could not be played. Skip on, unless everything is failing."""
        failed = self.state.track
        if failed is not None:
            if failed.path not in self.failed_paths:
                self.failed_paths.append(failed.path)
            self._on_error(f"cannot play {failed.title} — skipping")

        self._failures += 1
        limit = max(3, len(self._playlist) + len(self._queue))
        if self._failures > limit:
            # Everything is broken; stop rather than race through the list.
            self._failures = 0
            self._on_error("nothing in this playlist can be played")
            self.stop()
            return
        self._handle_track_end()

    # -- ReplayGain ------------------------------------------------------

    def _apply_replaygain(self, mode: str) -> None:
        """Map cmus's vocabulary onto mpv's.

        cmus "smart" means album gain for a straight-through album listen and
        track gain when shuffling or working through a queue; mpv has no such
        mode, so we resolve it ourselves whenever playback context changes.
        """
        self.state.replaygain = mode
        resolved = mode
        if mode == "smart":
            resolved = "track" if (self.state.shuffle or self._queue) else "album"
        try:
            self._mpv.replaygain = {
                "off": "no", "no": "no",
                "track": "track",
                "album": "album",
            }.get(resolved, "album")
        except Exception:
            pass

    def set_replaygain(self, mode: str) -> None:
        self._apply_replaygain(mode)
        self._on_change()

    # -- playlist / queue ------------------------------------------------

    @property
    def playlist(self) -> Sequence[Track]:
        return list(self._playlist)

    @property
    def queue(self) -> Sequence[Track]:
        return list(self._queue)

    @property
    def index(self) -> int:
        return self._index

    def set_playlist(self, tracks: Sequence[Track], start: int = 0) -> None:
        with self._lock:
            self._playlist = list(tracks)
            self._history.clear()
            self._index = -1
        if self._playlist:
            self.play_index(max(0, min(start, len(self._playlist) - 1)))

    def append(self, tracks: Sequence[Track]) -> None:
        with self._lock:
            self._playlist.extend(tracks)
        self._on_change()

    def enqueue(self, tracks: Sequence[Track]) -> None:
        """Add to the queue - these play next, ahead of the playlist."""
        with self._lock:
            self._queue.extend(tracks)
        self._apply_replaygain(self.state.replaygain)
        self._on_change()

    def clear_queue(self) -> None:
        with self._lock:
            self._queue.clear()
        self._apply_replaygain(self.state.replaygain)
        self._on_change()

    def remove_from_playlist(self, index: int) -> None:
        with self._lock:
            if 0 <= index < len(self._playlist):
                self._playlist.pop(index)
                if index < self._index:
                    self._index -= 1
        self._on_change()

    # -- transport -------------------------------------------------------

    def play_track(self, track: Track) -> None:
        """Play one track immediately without disturbing the playlist."""
        if not track.exists:
            # Missing file: treat it exactly like an unplayable one, or the
            # playlist stalls here instead of moving on.
            self.state.track = track
            self._note_failure()
            return
        self.state.track = track
        self.state.playing = True
        self.state.paused = False
        self.state.position = 0.0
        self.state.duration = track.length

        with self._lock:
            index = self._index if (
                0 <= self._index < len(self._playlist)
                and self._playlist[self._index].path == track.path
            ) else -1
        self._reset_mpv_playlist(track, index)

        try:
            self._mpv.play(track.path)
            self._mpv.pause = False
        except Exception:
            self.state.playing = False
        self._on_change()
        if index >= 0:
            self._queue_upcoming()

    def play_index(self, index: int) -> None:
        with self._lock:
            if not (0 <= index < len(self._playlist)):
                return
            if self._index >= 0:
                self._history.append(self._index)
            self._index = index
            track = self._playlist[index]
        self._apply_replaygain(self.state.replaygain)
        self.play_track(track)

    def toggle_pause(self) -> None:
        if not self.state.track:
            return
        self.state.paused = not self.state.paused
        try:
            self._mpv.pause = self.state.paused
        except Exception:
            pass
        self._on_change()

    def play(self) -> None:
        if self.state.track and self.state.paused:
            self.toggle_pause()
        elif not self.state.track and self._playlist:
            self.play_index(0)

    def pause(self) -> None:
        if self.state.track and not self.state.paused:
            self.toggle_pause()

    def stop(self) -> None:
        self.state.playing = False
        self.state.paused = False
        self.state.position = 0.0
        self.state.track = None
        try:
            self._mpv.command("stop")
        except Exception:
            pass
        self._on_change()

    def next(self) -> None:
        """Queue first, then the playlist."""
        with self._lock:
            if self._queue:
                track = self._queue.pop(0)
                self._apply_replaygain(self.state.replaygain)
                self.play_track(track)
                return

            if not self._playlist:
                return

            if self.state.repeat is RepeatMode.ONE and self._index >= 0:
                index = self._index
            elif self.state.shuffle:
                import random

                index = random.randrange(len(self._playlist))
            else:
                index = self._index + 1
                if index >= len(self._playlist):
                    if self.state.repeat is RepeatMode.ALL:
                        index = 0
                    else:
                        self.stop()
                        return
        self.play_index(index)

    def previous(self) -> None:
        """Restart the track unless we are within rewind_offset of its start,
        in which case go to the genuinely previous track. cmus's behaviour, and
        the right one."""
        if self.state.track and self.state.position > self.rewind_offset:
            self.seek_to(0)
            return

        with self._lock:
            if self._history:
                index = self._history.pop()
            elif self._index > 0:
                index = self._index - 1
            elif self.state.repeat is RepeatMode.ALL and self._playlist:
                index = len(self._playlist) - 1
            else:
                self.seek_to(0)
                return
            # play_index pushes to history; don't let "previous" grow it.
            self._index = index
            track = self._playlist[index] if 0 <= index < len(self._playlist) else None
        if track:
            self.play_track(track)

    def _handle_track_end(self) -> None:
        if self._advancing:
            return
        self._advancing = True
        try:
            if self.continue_playback:
                self.next()
            else:
                self.stop()
        finally:
            self._advancing = False

    # -- seek / volume ---------------------------------------------------

    def seek(self, seconds: float) -> None:
        if not self.state.track:
            return
        try:
            self._mpv.command("seek", str(seconds), "relative")
        except Exception:
            pass

    def seek_to(self, seconds: float) -> None:
        if not self.state.track:
            return
        try:
            self._mpv.command("seek", str(max(0.0, seconds)), "absolute")
        except Exception:
            pass

    def set_volume(self, volume: int) -> None:
        volume = max(0, min(130, volume))
        self.state.volume = volume
        try:
            self._mpv.volume = volume
        except Exception:
            pass
        self._on_change()

    def adjust_volume(self, delta: int) -> None:
        self.set_volume(self.state.volume + delta)

    def toggle_mute(self) -> None:
        self.state.muted = not self.state.muted
        try:
            self._mpv.mute = self.state.muted
        except Exception:
            pass
        self._on_change()

    # -- modes -----------------------------------------------------------

    def toggle_shuffle(self) -> None:
        self.state.shuffle = not self.state.shuffle
        self._apply_replaygain(self.state.replaygain)
        self._on_change()

    def cycle_repeat(self) -> None:
        order = [RepeatMode.OFF, RepeatMode.ALL, RepeatMode.ONE]
        self.state.repeat = order[(order.index(self.state.repeat) + 1) % len(order)]
        self._on_change()

    # -- shutdown --------------------------------------------------------

    def shutdown(self) -> None:
        try:
            self._mpv.terminate()
        except Exception:
            pass
