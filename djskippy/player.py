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
    ) -> None:
        import mpv

        self.state = PlayerState(volume=volume, replaygain=replaygain)
        self.rewind_offset = rewind_offset
        self.continue_playback = continue_playback
        self._on_change = on_change or (lambda: None)

        self._playlist: list[Track] = []
        self._queue: list[Track] = []
        self._index = -1
        self._history: list[int] = []
        self._lock = threading.RLock()
        self._advancing = False

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
                self.state.position = float(value)
                self._on_change()

        @self._mpv.property_observer("duration")
        def _observe_duration(_name, value):  # pragma: no cover - callback
            if value is not None:
                self.state.duration = float(value)
                self._on_change()

        @self._mpv.event_callback("end-file")
        def _on_end(event):  # pragma: no cover - callback
            reason = getattr(event, "data", None)
            reason = getattr(reason, "reason", None) or ""
            # "eof" means the track finished on its own; anything else means we
            # (or an error) stopped it, and we must not double-advance.
            if str(reason).endswith("eof") or str(reason) == "MPV_END_FILE_REASON_EOF":
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
            return
        self.state.track = track
        self.state.playing = True
        self.state.paused = False
        self.state.position = 0.0
        self.state.duration = track.length
        try:
            self._mpv.play(track.path)
            self._mpv.pause = False
        except Exception:
            self.state.playing = False
        self._on_change()

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
