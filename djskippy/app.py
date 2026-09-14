"""The DJ-Skippy interface.

Yazi-style miller columns over the library, cmus's queue/playlist model and
transport keys, vim navigation throughout, and cava along the bottom.

Colours are deliberately ANSI rather than fixed RGB, so DJ-Skippy inherits
whatever theme the terminal is running instead of fighting it.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any, Sequence

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Input

from .cava import CavaVisualiser, clean_stale_configs
from .config import Config, load_state, save_state
from .library import AUDIO_SUFFIXES, Library, Track
from .player import Player, RepeatMode
from .playlists import PlaylistStore


class View(IntEnum):
    LIBRARY = 1
    PLAYLISTS = 2      # your saved collections — what "playlist" actually means
    PLAYING = 3        # what is loaded and playing through right now
    BROWSER = 4
    HELP = 5


# --------------------------------------------------------------------------
# Widgets
# --------------------------------------------------------------------------


class ListPane(Widget):
    """A scrollable list with a cursor. Rolled by hand rather than using a
    stock widget so vim keys, counts and multi-pane focus behave exactly as
    they do in yazi rather than approximately."""

    DEFAULT_CSS = """
    ListPane {
        height: 1fr;
        border: round $panel-lighten-1;
        padding: 0 1;
    }
    ListPane.focused { border: round ansi_blue; }
    """

    def __init__(self, title: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.pane_title = title
        #: What the count in the header is counting. "Playlist 12" read as
        #: the name of a playlist rather than a number of tracks.
        self.count_noun = "tracks"
        self.items: list[str] = []
        self.meta: list[Any] = []
        self.cursor = 0
        self.scroll_top = 0
        self.is_active = False
        self.marker: int | None = None  # currently-playing row

    # -- data ---------------------------------------------------------

    def set_items(self, items: Sequence[str], meta: Sequence[Any] | None = None) -> None:
        self.items = list(items)
        self.meta = list(meta) if meta is not None else list(items)
        self.cursor = min(self.cursor, max(0, len(self.items) - 1))
        self.scroll_top = 0
        self._clamp()
        self.refresh()

    @property
    def selected(self) -> Any:
        if 0 <= self.cursor < len(self.meta):
            return self.meta[self.cursor]
        return None

    # -- movement ------------------------------------------------------

    def move(self, delta: int) -> None:
        if not self.items:
            return
        self.cursor = max(0, min(len(self.items) - 1, self.cursor + delta))
        self._clamp()
        self.refresh()

    def move_to(self, index: int) -> None:
        if not self.items:
            return
        self.cursor = max(0, min(len(self.items) - 1, index))
        self._clamp()
        self.refresh()

    def page(self, direction: int) -> None:
        self.move(direction * max(1, self.content_height - 1))

    @property
    def content_height(self) -> int:
        # Two rows of border, one of title.
        return max(1, self.size.height - 3)

    def _clamp(self) -> None:
        height = self.content_height
        if self.cursor < self.scroll_top:
            self.scroll_top = self.cursor
        elif self.cursor >= self.scroll_top + height:
            self.scroll_top = self.cursor - height + 1
        self.scroll_top = max(0, min(self.scroll_top, max(0, len(self.items) - height)))

    # -- rendering -----------------------------------------------------

    def render(self) -> Text:
        height = self.content_height
        width = max(4, self.size.width - 4)
        self._clamp()

        # One Text with no base style. Constructing it as Text(..., style=...)
        # and appending to it applies that style to *everything* appended
        # afterwards, which turned every row of the focused pane blue.
        out = Text()

        count = f"  ·  {len(self.items)} {self.count_noun}" if self.items else ""
        out.append(
            f"{self.pane_title}{count}".ljust(width)[:width] + "\n",
            style="bold blue" if self.is_active else "bold bright_black",
        )

        visible = self.items[self.scroll_top : self.scroll_top + height]
        for row, label in enumerate(visible):
            index = self.scroll_top + row
            text = label[: width - 2].ljust(width - 2)
            is_cursor = index == self.cursor
            is_playing = self.marker is not None and index == self.marker

            prefix = "▶ " if is_playing else "  "
            if is_cursor and self.is_active:
                # Reverse over unstyled text stays readable whatever the
                # terminal theme; reverse over a coloured foreground does not.
                style = "reverse"
            elif is_cursor:
                style = "bold"
            elif is_playing:
                style = "bold green"
            else:
                style = ""
            out.append(prefix + text + "\n", style=style)

        # Pad so the border does not jump around on short lists.
        for _ in range(height - len(visible)):
            out.append("\n")

        return out


class CavaPane(Widget):
    """The visualiser strip."""

    DEFAULT_CSS = """
    CavaPane { height: 5; display: none; }
    """

    def __init__(self, visualiser: CavaVisualiser, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.visualiser = visualiser
        self.enabled = True

    def render(self) -> Any:
        if not self.enabled:
            return Text("")
        width = max(4, self.size.width)
        height = max(1, self.size.height)
        if not self.visualiser.running:
            message = self.visualiser.error or "visualiser off"
            return Text(f" {message}", style="bright_black")

        rows = self.visualiser.render_rows(width, height)
        text = Text()
        # Colour shifts with height so peaks stand out.
        palette = ["blue", "cyan", "green", "yellow", "red"]
        for row_index, row in enumerate(rows):
            ratio = 1.0 - (row_index / max(1, len(rows)))
            style = palette[min(len(palette) - 1, int(ratio * len(palette)))]
            text.append(row + "\n", style=style)
        return text


class NowPlaying(Widget):
    """Track, progress bar and transport state."""

    DEFAULT_CSS = """
    NowPlaying {
        height: 3;
        border-top: solid $panel-lighten-1;
        padding: 0 1;
    }
    """

    def __init__(self, player: Player, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.player = player

    def render(self) -> Text:
        state = self.player.state
        width = max(20, self.size.width - 2)
        text = Text()

        if state.track is None:
            text.append("■ ", style="bright_black")
            text.append("Nothing playing", style="bright_black")
            text.append("   —   select a track and press ", style="bright_black")
            text.append("enter", style="bold cyan")
            text.append(" to play", style="bright_black")
            text.append("\n")
            text.append("─" * width, style="bright_black")
            return text

        icon = "❚❚" if state.paused else "▶"
        text.append(f"{icon} ", style="bold green")
        text.append(state.track.title, style="bold")
        text.append("  —  ", style="bright_black")
        text.append(state.track.artist, style="cyan")
        text.append(f"  ({state.track.format})", style="bright_black")
        text.append("\n")

        duration = state.duration or state.track.length or 0
        position = min(state.position, duration) if duration else state.position
        bar_width = max(10, width - 34)
        filled = int((position / duration) * bar_width) if duration else 0

        text.append(f"{_fmt(position)} ", style="bright_black")
        text.append("━" * filled, style="green")
        text.append("╸", style="bold green")
        text.append("─" * max(0, bar_width - filled), style="bright_black")
        text.append(f" {_fmt(duration)}", style="bright_black")

        flags = []
        if state.shuffle:
            flags.append("shuffle")
        if state.repeat is not RepeatMode.OFF:
            flags.append(f"repeat:{state.repeat.value}")
        vol = "muted" if state.muted else f"vol {state.volume}%"
        text.append(f"  {vol}", style="yellow")
        if flags:
            text.append(f"  {' '.join(flags)}", style="magenta")
        return text


class StatusLine(Widget):
    """Bottom line: view tabs, transient messages, service state."""

    DEFAULT_CSS = """
    StatusLine { height: 1; padding: 0 1; }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.view = View.LIBRARY
        self.message = ""
        self.services = ""

    def render(self) -> Text:
        text = Text()
        for view in View:
            label = f" {int(view)}:{view.name.title()} "
            text.append(label, style="reverse bold" if view is self.view else "bright_black")

        # A transient message replaces the key legend; otherwise the legend is
        # always on screen, because a player that does not tell you how to
        # pause it is not finished.
        if self.message:
            text.append("  " + self.message, style="yellow")
        else:
            for key, what in (
                ("enter", "play"), ("space", "pause"), ("v", "stop"),
                ("b/z", "next/prev"), ("+/-", "vol"), ("?", "help"),
                ("q", "quit"),
            ):
                text.append("  " + key, style="bold cyan")
                text.append(" " + what, style="bright_black")

        if self.services:
            text.append("   " + self.services, style="bright_black")
        return text


def _fmt(seconds: float) -> str:
    seconds = max(0, int(seconds or 0))
    minutes, secs = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------


HELP_TEXT = """\
DJ-Skippy — keys

  NAVIGATION (vim / yazi)
    j k           down / up
    h l           left / right between columns
    g g           jump to top
    G             jump to bottom
    ctrl+d ctrl+u half page down / up
    /             search      n / N   next / previous match
    :             command mode

  VIEWS
    1 Library    2 Playlists    3 Now playing    4 Browser    5 Help

  TRANSPORT (cmus)
    space         play / pause
    enter         play selection
    x c v         play / pause / stop
    b z           next / previous track
    - = or + -    volume down / up
    m             mute
    s             shuffle
    r             repeat: off → all → one
    left right    seek 5s        [ ]   seek 30s

  PLAYLISTS  (view 2)
    A playlist is a collection you name and keep: favourites, a mood,
    a road trip. Build one from anywhere in the Library.

    p             add what the cursor is on to a playlist, by name.
                  Creates it if it does not exist. Works on a single
                  track, a whole album, or everything by an artist.
    2             your playlists.  enter plays one
                  d deletes,  r renames
    S             save what is currently loaded as a new playlist

  NOW PLAYING  (view 3)
    What is loaded and playing through, with anything you have queued
    shown at the top.

    a             add to what is loaded
    e             play next, ahead of everything already loaded
    d             remove the highlighted track

  BROWSER (4)
    enter         open a folder, or play a file straight away
    h             up one folder
    a  e          add a file, or a whole folder, to playlist / queue

  LIBRARY
    a             append selection to playlist
    e             enqueue selection (plays next, ahead of playlist)
    d             remove from playlist / queue

  TOGGLES
    V             visualiser      w   web player
    ?             this help       q   quit

  REVIEW  (view 5 — anything the importer would not decide alone)
    Every entry says why it is there and what to do about it.
    enter         open it and decide

  IMPORT & MAINTENANCE  (view 6)
    i             import every album on disk that is not in the library yet
    enter         tag just the highlighted folder, interactively
    d             find duplicates          M   albums with missing tracks
    (M and R stay uppercase: m is mute and r is repeat, everywhere.)
    esc           stop a running import
    :dup  :missing  :mbsync  :stats  :import [path]

  COMMANDS
    :q  :quit                     :web / :noweb
    :add <path>                   :import <path>
    :set volume=80                :set replaygain=smart|track|album|off
    :theme <name>                 :reload
"""


class DJSkippy(App):
    """The one-stop shop."""

    color = True  # inherit the terminal palette rather than override it

    CSS = """
    Screen { layers: base overlay; }
    #columns { height: 1fr; }
    #artists { width: 26; }
    #albums  { width: 48; }
    #tracks  { width: 1fr; }
    #cmdline { dock: bottom; display: none; }
    #cmdline.visible { display: block; }
    """

    def __init__(self, config: Config | None = None) -> None:
        super().__init__()
        self.cfg = config or Config.load()
        self.library = Library(
            self.cfg.library.music_dir, self.cfg.library.smart_artist_sort
        )
        self.player = Player(
            volume=self.cfg.playback.volume,
            replaygain=self.cfg.playback.replaygain,
            rewind_offset=self.cfg.playback.rewind_offset,
            continue_playback=self.cfg.playback.continue_playback,
            on_change=self._on_player_change,
            on_error=self._on_player_error,
        )
        self.visualiser = CavaVisualiser(
            bars=40 if self.cfg.visualiser.bars == "auto" else int(self.cfg.visualiser.bars),
            framerate=self.cfg.visualiser.framerate,
        )
        self.playlists = PlaylistStore()

        self.view = View.LIBRARY
        self.focus_column = 0
        self.pending_key: str | None = None
        self.search_matches: list[int] = []
        self.search_index = 0
        self.mode: str | None = None  # "search" | "command"
        self.web = None
        self.mpris = None
        self._browser_dir = self.cfg.library.music_dir

    # -- layout ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="columns"):
                yield ListPane("Artists", id="artists")
                yield ListPane("Albums", id="albums")
                yield ListPane("Tracks", id="tracks")
            yield CavaPane(self.visualiser, id="cava")
            yield NowPlaying(self.player, id="now")
            yield StatusLine(id="status")
        yield Input(placeholder="", id="cmdline")

    # -- lifecycle -------------------------------------------------------

    async def on_mount(self) -> None:
        self.title = "DJ-Skippy"
        self._panes = [
            self.query_one("#artists", ListPane),
            self.query_one("#albums", ListPane),
            self.query_one("#tracks", ListPane),
        ]
        self._cava = self.query_one("#cava", CavaPane)
        self._status = self.query_one("#status", StatusLine)
        self._now = self.query_one("#now", NowPlaying)

        # The command line is the only focusable widget, so Textual hands it
        # focus on mount and it silently swallows every keystroke. Keep it
        # unfocusable until it is actually opened; the App handles keys.
        self._cmdline = self.query_one("#cmdline", Input)
        self._cmdline.can_focus = False
        # Input selects its whole value when focused, which would make the
        # first keystroke wipe any prefix we prefill (":save ", ":addto ").
        self._cmdline.select_on_focus = False
        self.set_focus(None)

        self._install_signal_handlers()
        clean_stale_configs()

        self._refresh_library()
        self._update_focus()

        if self.library.track_count == 0:
            self.notify_status(
                f"No music found in {self.cfg.library.music_dir} — press 4 to browse and t to tag"
            )
        else:
            self.notify_status(
                f"{self.library.track_count} tracks"
            )

        # Repaint the transport twice a second; mpv pushes position updates but
        # a steady tick keeps the bar smooth without hammering the UI.
        self.set_interval(0.5, self._tick)
        if self.cfg.visualiser.enabled:
            self.set_interval(1 / 30, self._tick_cava)
            await self._start_visualiser()
        if self.cfg.web.enabled:
            self._start_web()
        if self.cfg.mpris.enabled:
            await self._start_mpris()
        if self.cfg.playback.resume:
            self._restore_state()
        # Always describe the library, even when every optional service is
        # switched off - otherwise the status line is simply blank.
        self._update_services()

    def _install_signal_handlers(self) -> None:
        """Exit cleanly when the terminal goes away.

        Closing a kitty window (or any terminal) sends SIGHUP to the
        foreground process. Without a handler, Textual keeps running detached:
        the process survives until logout still holding the MPRIS bus name,
        the web port, and a cava process burning CPU. A music player should
        stop when you close its window.

        SIGTERM is handled the same way so `pkill` and a desktop session
        ending are also clean, and both save playback position on the way out.
        """
        import signal

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._on_signal, sig)
            except (NotImplementedError, RuntimeError, ValueError):
                # Not every platform or loop supports this; the atexit
                # backstop in cava.py still prevents orphaned processes.
                pass

    def _on_signal(self, sig) -> None:
        """Shut down on a terminating signal.

        Everything is torn down *here*, synchronously, rather than trusting
        Textual's normal unmount path. When the signal is SIGHUP the terminal
        has already gone, and Textual's shutdown writes to that terminal - so
        it blocks, the app never exits, and you are left with an orphaned
        process holding the MPRIS name and the web port until logout. Which is
        exactly what happened.
        """
        import os
        import threading

        from .cava import _kill_stragglers

        for step in (
            self._save_state,
            _kill_stragglers,
            lambda: self.web.stop() if self.web is not None else None,
            self.player.shutdown,
        ):
            try:
                step()
            except Exception:
                pass

        # Ask Textual to leave nicely, in case the terminal is still there.
        try:
            self.exit()
        except Exception:
            pass

        # ...but do not wait forever for a screen that may not exist. State is
        # saved and children are dead by this point, so leaving hard is safe.
        def _force() -> None:
            os._exit(0)

        watchdog = threading.Timer(1.5, _force)
        watchdog.daemon = True
        watchdog.start()

    async def on_unmount(self) -> None:
        self._save_state()
        try:
            await self.visualiser.stop()
        except Exception:
            pass
        if self.web is not None:
            self.web.stop()
        if self.mpris is not None:
            await self.mpris.stop()
        self.player.shutdown()

    # -- services --------------------------------------------------------

    async def _start_visualiser(self) -> None:
        self.visualiser.bars = self.visualiser._valid_bars(self.size.width)
        ok = await self.visualiser.start()
        if not ok:
            self.notify_status(f"visualiser: {self.visualiser.error}")

    def _start_web(self) -> None:
        from .web import WebServer

        self.web = WebServer(
            self.player, self.library, self.cfg.web.host, self.cfg.web.port
        )
        if self.web.start():
            self._update_services()
            if self.web.moved_from is not None:
                self.notify_status(
                    f"port {self.web.moved_from} was taken — web player is on "
                    f"{self.web.url}"
                )
        else:
            self.notify_status(f"web player failed: {self.web.error}")

    async def _start_mpris(self) -> None:
        from .mpris import MprisService

        self.mpris = MprisService(self.player, on_quit=lambda: self.exit())
        if await self.mpris.start():
            self._update_services()
        else:
            self.notify_status(f"mpris unavailable: {self.mpris.error}")






    def _update_services(self) -> None:
        parts = [f"{self.library.track_count} tracks"]
        if self.web is not None and self.web.running:
            parts.append(self.web.url)
        if self.mpris is not None and self.mpris.active:
            parts.append("mpris")
        self._status.services = "  ".join(parts)
        self._status.refresh()

    # -- state persistence -----------------------------------------------

    def _save_state(self) -> None:
        state = self.player.state
        save_state(
            {
                "track_path": state.track.path if state.track else "",
                "position": state.position,
                "volume": state.volume,
                "shuffle": state.shuffle,
                "repeat": state.repeat.value,
            }
        )

    def _restore_state(self) -> None:
        state = load_state()
        path = state.get("track_path")
        if not path:
            return
        for track in self.library.all_tracks:
            if track.path == path:
                self.player.set_volume(int(state.get("volume", self.cfg.playback.volume)))
                self.player.play_track(track)
                self.player.pause()
                position = float(state.get("position", 0) or 0)
                if position > 1:
                    self.call_later(lambda: self.player.seek_to(position))
                self.notify_status(f"resumed: {track.title}")
                break

    # -- library panes ---------------------------------------------------

    def _refresh_library(self) -> None:
        # Rebuilt lazily by _track_for_path; drop it so the browser picks up
        # anything that has just been imported.
        if hasattr(self, "_browser_index"):
            del self._browser_index
        artists = self.library.artists()
        self._panes[0].set_items(artists, artists)
        self._refresh_albums()

    def _refresh_albums(self) -> None:
        artist = self._panes[0].selected
        if not artist:
            self._panes[1].set_items([], [])
            self._panes[2].set_items([], [])
            return
        albums = self.library.albums(artist)
        labels = []
        for album in albums:
            year = self.library.album_year(artist, album)
            labels.append(f"{year}  {album}" if year else album)
        self._panes[1].set_items(labels, albums)
        self._refresh_tracks()

    def _refresh_tracks(self) -> None:
        artist = self._panes[0].selected
        album = self._panes[1].selected
        if not artist or not album:
            self._panes[2].set_items([], [])
            return
        tracks = self.library.tracks(artist, album)
        width = max(20, self._panes[2].size.width - 14)
        labels = []
        for t in tracks:
            labels.append(
                f"{t.display_title[: width - 8].ljust(width - 8)} "
                f"{t.length_str:>6}"
            )
        self._panes[2].set_items(labels, tracks)
        self._sync_marker()

    def _sync_marker(self) -> None:
        """Highlight the playing track if it is on screen."""
        current = self.player.state.track
        pane = self._panes[2]
        pane.marker = None
        if current is not None:
            for index, track in enumerate(pane.meta):
                if isinstance(track, Track) and track.path == current.path:
                    pane.marker = index
                    break
        pane.refresh()

    def _preserve_selection(self, rebuild) -> None:
        """Rebuild the library panes without moving the user.

        A background refresh that resets the cursor is indistinguishable from
        the interface jumping about on its own, which is precisely what it
        should never do. Selections are restored by name, not by index, since
        new entries shift the indices.
        """
        artist = self._panes[0].selected
        album = self._panes[1].selected
        track = self._panes[2].selected
        track_path = getattr(track, "path", None)
        column = self.focus_column

        rebuild()

        if artist in self._panes[0].meta:
            self._panes[0].move_to(self._panes[0].meta.index(artist))
            self._refresh_albums()
        if album in self._panes[1].meta:
            self._panes[1].move_to(self._panes[1].meta.index(album))
            self._refresh_tracks()
        if track_path:
            for index, candidate in enumerate(self._panes[2].meta):
                if getattr(candidate, "path", None) == track_path:
                    self._panes[2].move_to(index)
                    break
        self.focus_column = column
        self._update_focus()

    def _update_focus(self) -> None:
        for index, pane in enumerate(self._panes):
            pane.is_active = index == self.focus_column
            pane.set_class(pane.is_active, "focused")
            pane.refresh()

    # -- current list abstraction ---------------------------------------

    def _current_pane(self) -> ListPane:
        if self.view is View.LIBRARY:
            return self._panes[self.focus_column]
        return self._panes[2]

    def _selected_tracks(self) -> list[Track]:
        """Whatever the cursor is on, resolved to a list of tracks."""
        if self.view is View.LIBRARY:
            if self.focus_column == 0:
                artist = self._panes[0].selected
                if not artist:
                    return []
                out: list[Track] = []
                for album in self.library.albums(artist):
                    out.extend(self.library.tracks(artist, album))
                return out
            if self.focus_column == 1:
                artist, album = self._panes[0].selected, self._panes[1].selected
                return self.library.tracks(artist, album) if artist and album else []
            selected = self._panes[2].selected
            return [selected] if isinstance(selected, Track) else []

        if self.view is View.BROWSER:
            selected = self._panes[2].selected
            if isinstance(selected, Track):
                return [selected]
            if isinstance(selected, Path) and selected.is_dir():
                return self._browser_folder_tracks(selected)
            return []

        selected = self._panes[2].selected
        return [selected] if isinstance(selected, Track) else []

    # -- ticking ---------------------------------------------------------

    def _tick(self) -> None:
        self._now.refresh()
        self._sync_marker()

    def _tick_cava(self) -> None:
        # Only occupy screen space when there is actually something to draw.
        should_show = self._cava.enabled and (
            self.visualiser.running or self.visualiser.error is not None
        )
        if self._cava.display != should_show:
            self._cava.display = should_show
        if should_show:
            self._cava.refresh()

    def _ui(self, callback, *args) -> None:
        """Run a callback on the UI thread, from either side.

        call_from_thread *raises* when called from the app's own thread, and
        that exception was being swallowed - so anything triggered by a
        keypress never refreshed the display and appeared not to have worked
        until the next half-second tick. Pausing was the obvious victim: the
        audio stopped immediately, the indicator did not.
        """
        import threading

        thread_id = getattr(self, "_thread_id", None)
        if thread_id and threading.get_ident() == thread_id:
            # Deliberately not wrapped: an exception here is a real bug in a
            # refresh path, and swallowing it presents as "the display just
            # does not update", with nothing to go on.
            callback(*args)
            return
        try:
            self.call_from_thread(callback, *args)
        except Exception:
            pass

    def _on_player_change(self) -> None:
        """Playback state changed - from mpv's thread or our own."""
        self._ui(self._on_player_change_ui)

    def _on_player_error(self, message: str) -> None:
        """Playback failure, raised from mpv's thread."""
        self._ui(self.notify_status, message)

    def _on_player_change_ui(self) -> None:
        self._now.refresh()
        if self.mpris is not None:
            self.mpris.notify()

    def notify_status(self, message: str) -> None:
        self._status.message = message
        self._status.refresh()
        self.set_timer(6, self._clear_status)

    def _clear_status(self) -> None:
        self._status.message = ""
        self._status.refresh()

    # -- views -----------------------------------------------------------

    def _set_view(self, view: View) -> None:
        self.view = view
        self._status.view = view
        self._status.refresh()

        columns = self.query_one("#columns")
        if view is View.LIBRARY:
            for pane in self._panes:
                pane.display = True
            self._panes[0].pane_title = "Artists"
            self._panes[0].count_noun = "artists"
            self._panes[1].pane_title = "Albums"
            self._panes[1].count_noun = "albums"
            self._panes[2].pane_title = "Tracks"
            self._panes[2].count_noun = "tracks"
            self._refresh_library()
        elif view is View.PLAYLISTS:
            self._panes[0].display = False
            self._panes[1].display = False
            self._panes[2].display = True
            self._panes[2].pane_title = (
                "Playlists — enter plays · d deletes · r renames"
            )
            self._panes[2].count_noun = "saved"
            self._render_playlists()
        elif view is View.PLAYING:
            self._panes[0].display = False
            self._panes[1].display = False
            self._panes[2].display = True
            self._panes[2].pane_title = "Now playing"
            self._panes[2].count_noun = "tracks"
            self._render_now_playing()
        elif view is View.BROWSER:
            self._panes[0].display = False
            self._panes[1].display = False
            self._panes[2].display = True
            start = getattr(self, "_browser_dir", self.cfg.library.music_dir)
            self._panes[2].pane_title = f"Browser — {start}"
            self._panes[2].count_noun = "items"
            self._load_browser(start)
        elif view is View.HELP:
            self._panes[0].display = False
            self._panes[1].display = False
            self._panes[2].display = True
            self._panes[2].pane_title = "Help"
            self._panes[2].count_noun = "lines"
            lines = HELP_TEXT.splitlines()
            self._panes[2].set_items(lines, lines)
        columns.refresh()

    def _render_playlists(self) -> None:
        """Your saved collections."""
        infos = self.playlists.list()
        if infos:
            self._panes[2].set_items([i.label for i in infos], infos)
            return
        empty = [
            "",
            "  You have not saved any playlists yet.",
            "",
            "  A playlist is a collection you build: favourites, a mood,",
            "  a road trip. It has a name and it stays until you delete it.",
            "",
            "  To make one:",
            "    1. press 1 for the Library",
            "    2. put the cursor on a track, album or artist you want",
            "    3. press p  and type a name for the playlist",
            "",
            "  Press p again on anything else to add it to the same one.",
            "  Everything you add is saved straight away.",
            "",
        ]
        self._panes[2].set_items(empty, [None] * len(empty))

    def _render_now_playing(self) -> None:
        """What is loaded and what jumps the queue, in one list."""
        queued = list(self.player.queue)
        playing = list(self.player.playlist)
        current = self.player.state.track

        labels: list[str] = []
        meta: list[Any] = []

        if queued:
            labels.append("  PLAYING NEXT")
            meta.append(None)
            for track in queued:
                labels.append(f"    {track.title}  —  {track.artist}")
                meta.append(track)
            labels.append("")
            meta.append(None)

        if playing:
            if queued:
                labels.append("  THEN")
                meta.append(None)
            for track in playing:
                mark = "▶ " if current and track.path == current.path else "  "
                labels.append(f"{mark}  {track.title}  —  {track.artist}")
                meta.append(track)

        if not labels:
            labels = [
                "",
                "  Nothing loaded.",
                "",
                "  Press 1 and hit enter on a track — that album loads here",
                "  and plays through.",
                "",
                "  e on anything adds it to play next, ahead of whatever is",
                "  already loaded.",
                "",
            ]
            meta = [None] * len(labels)

        self._panes[2].set_items(labels, meta)

    def _load_browser(self, directory: Path) -> None:
        """List a directory: folders first, then playable audio files.

        Files matter as much as folders here. The whole point of a browser in
        a music player is to play something that is *not* in your library yet
        - a USB stick, a download, a folder you have not imported - without
        having to import it first.
        """
        directory = Path(directory)
        try:
            entries = list(directory.iterdir())
        except OSError as exc:
            self.notify_status(str(exc))
            return

        folders = sorted(
            (p for p in entries if p.is_dir()), key=lambda p: p.name.lower()
        )
        files = sorted(
            (
                p for p in entries
                if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
            ),
            key=lambda p: p.name.lower(),
        )

        labels: list[str] = [".."]
        meta: list[Any] = [directory.parent]

        for folder in folders:
            labels.append(f"{folder.name}/")
            meta.append(folder)

        width = max(30, self._panes[2].size.width - 14)
        for path in files:
            track = self._track_for_path(path)
            if track.artist and track.artist != "Unknown Artist":
                label = f"{track.title}  —  {track.artist}"
            else:
                label = track.title
            labels.append(f"{label[:width].ljust(width)} {track.length_str:>6}")
            meta.append(track)

        self._panes[2].set_items(labels, meta)
        self._browser_dir = directory
        if self.view is View.BROWSER:
            self._panes[2].pane_title = f"Browser — {directory}"

    def _track_for_path(self, path: Path) -> Track:
        """Resolve a file to a Track, preferring the library's own entry.

        A file already in the library comes back with its known tags and
        duration; anything else is read on the spot.
        """
        if not hasattr(self, "_browser_index"):
            self._browser_index = {t.path: t for t in self.library.all_tracks}
        known = self._browser_index.get(str(path))
        if known is not None:
            return known

        from .playlists import _track_from_path

        built = _track_from_path(str(path))
        if built is not None:
            return built
        return Track(
            id=-1, path=str(path), title=path.stem, artist="", albumartist="",
            album="", track_no=0, disc_no=0, length=0.0, year=0, genre="",
            format=path.suffix.lstrip(".").upper(),
        )

    def _browser_folder_tracks(self, folder: Path) -> list[Track]:
        """Every playable file directly inside a folder."""
        try:
            return [
                self._track_for_path(p)
                for p in sorted(folder.iterdir(), key=lambda p: p.name.lower())
                if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
            ]
        except OSError:
            return []

    # -- key handling ----------------------------------------------------

    async def on_key(self, event) -> None:
        if self.mode is not None:
            return  # the Input owns the keyboard

        key = event.key
        event.stop()
        event.prevent_default()

        # two-key sequences (gg)
        if self.pending_key == "g":
            self.pending_key = None
            if key == "g":
                self._current_pane().move_to(0)
                self._after_move()
            return

        pane = self._current_pane()

        # -- navigation
        if key == "j" or key == "down":
            pane.move(1); self._after_move()
        elif key == "k" or key == "up":
            pane.move(-1); self._after_move()
        elif key == "g":
            self.pending_key = "g"
        elif key == "G":
            pane.move_to(len(pane.items) - 1); self._after_move()
        elif key == "ctrl+d":
            pane.page(1); self._after_move()
        elif key == "ctrl+u":
            pane.page(-1); self._after_move()
        elif key == "h" and self.view is View.BROWSER:
            self._load_browser(self._browser_dir.parent)
            self.notify_status(f"{self._browser_dir}")
        elif key in ("h", "left") and self.view is View.LIBRARY:
            if key == "h" or not self.player.state.track:
                self.focus_column = max(0, self.focus_column - 1)
                self._update_focus()
            else:
                self.player.seek(-5)
        elif key in ("l", "right") and self.view is View.LIBRARY:
            if key == "l" or not self.player.state.track:
                self.focus_column = min(2, self.focus_column + 1)
                self._update_focus()
            else:
                self.player.seek(5)
        elif key == "left":
            self.player.seek(-5)
        elif key == "right":
            self.player.seek(5)
        elif key == "[":
            self.player.seek(-30)
        elif key == "]":
            self.player.seek(30)

        # -- views
        elif key in "12345":
            self._set_view(View(int(key)))

        # -- transport
        elif key == "space":
            self.player.toggle_pause()
        elif key == "enter":
            await self._activate()
        elif key == "x":
            self.player.play()
        elif key == "c":
            self.player.pause()
        elif key == "v":
            self.player.stop()
        elif key == "b":
            self.player.next()
        elif key == "z":
            self.player.previous()
        elif key in ("plus", "equals_sign", "="):
            self.player.adjust_volume(5)
        elif key in ("minus", "-"):
            self.player.adjust_volume(-5)
        elif key == "m":
            self.player.toggle_mute()
        elif key == "s":
            self.player.toggle_shuffle()
            self.notify_status(f"shuffle {'on' if self.player.state.shuffle else 'off'}")
        elif key == "r":
            if self.view is View.PLAYLISTS:
                self._rename_selected_playlist()
            else:
                self.player.cycle_repeat()
                self.notify_status(f"repeat: {self.player.state.repeat.value}")

        # -- library actions
        elif key == "a":
            tracks = self._selected_tracks()
            self.player.append(tracks)
            self.notify_status(f"appended {len(tracks)} track(s) to playlist")
        elif key == "e":
            tracks = self._selected_tracks()
            self.player.enqueue(tracks)
            self.notify_status(f"queued {len(tracks)} track(s)")
        elif key == "d":
            # One key, meaning "remove/dismiss the thing in front of me" -
            # which depends entirely on the view.
            if self.view is View.PLAYLISTS:
                self._delete_selected_playlist()
            elif self.view is View.PLAYING:
                self.player.clear_queue()
                self._set_view(View.PLAYING)
                self.notify_status("queue cleared")
            elif self.view is View.PLAYLISTS:
                self.player.remove_from_playlist(pane.cursor)
                self._set_view(View.PLAYLISTS)
        # The main action in a view should not need shift held down.
        # Lowercase wherever the key is free; M and R stay uppercase only
        # because m is mute and r is repeat, which must work from every view.
        elif key == "S":
            self._save_playlist_prompt()
        elif key == "p":
            self._add_to_playlist_prompt()
        elif key == "L":
            self._set_view(View.PLAYLISTS)

        # -- toggles
        elif key == "V":
            await self._toggle_visualiser()
        elif key == "w":
            self._toggle_web()
        elif key == "?":
            self._set_view(View.HELP)
        elif key == "q":
            self.exit()

        # -- modes
        elif key == "slash":
            self._open_input("search", "/")
        elif key == "colon":
            self._open_input("command", ":")
        elif key == "n":
            self._jump_match(1)
        elif key == "N":
            self._jump_match(-1)

    def _after_move(self) -> None:
        if self.view is View.LIBRARY:
            if self.focus_column == 0:
                self._refresh_albums()
            elif self.focus_column == 1:
                self._refresh_tracks()

    async def _activate(self) -> None:
        """Enter: play, or descend."""
        if self.view is View.BROWSER:
            selected = self._panes[2].selected
            if isinstance(selected, Path):
                self._load_browser(selected)
            elif isinstance(selected, Track):
                # Play it, and queue the rest of the folder behind it so an
                # album plays through rather than stopping after one track.
                siblings = self._browser_folder_tracks(self._browser_dir)
                index = next(
                    (i for i, t in enumerate(siblings) if t.path == selected.path), 0
                )
                if siblings:
                    self.player.set_playlist(siblings, index)
                else:
                    self.player.play_track(selected)
                self.notify_status(f"playing {selected.title}")
            return

        if self.view is View.PLAYLISTS:
            from .playlists import PlaylistInfo

            selected = self._panes[2].selected
            if isinstance(selected, PlaylistInfo):
                self._load_playlist(selected.name)
            return

        if self.view is View.LIBRARY and self.focus_column < 2:
            self.focus_column += 1
            self._update_focus()
            return

        selected = self._panes[2].selected
        if isinstance(selected, Track):
            if self.view is View.LIBRARY:
                artist = self._panes[0].selected
                album = self._panes[1].selected
                tracks = self.library.tracks(artist, album)
                index = next(
                    (i for i, t in enumerate(tracks) if t.path == selected.path), 0
                )
                self.player.set_playlist(tracks, index)
            else:
                self.player.play_track(selected)

    # -- search / command ------------------------------------------------

    def _open_input(self, mode: str, prompt: str) -> None:
        self.mode = mode
        cmdline = self.query_one("#cmdline", Input)
        cmdline.placeholder = "search" if mode == "search" else "command"
        cmdline.value = ""
        cmdline.add_class("visible")
        cmdline.can_focus = True
        cmdline.focus()

    def _prefill(self, text: str) -> None:
        """Put text in the command line with the caret after it.

        Assigning to Input.value selects the whole value, so without
        collapsing the selection the next keypress would replace the prefix
        rather than continue it.
        """
        cmdline = self.query_one("#cmdline", Input)
        cmdline.value = ""
        # insert_text_at_cursor rather than assigning .value: assignment fires
        # a watcher that re-selects the whole value afterwards, so any
        # selection we set here would be clobbered and the next keypress would
        # replace the prefix.
        cmdline.insert_text_at_cursor(text.lstrip(":"))

    def _close_input(self) -> None:
        self.mode = None
        cmdline = self.query_one("#cmdline", Input)
        cmdline.remove_class("visible")
        cmdline.value = ""
        cmdline.can_focus = False
        self.set_focus(None)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        mode = self.mode
        self._close_input()
        if not value:
            return
        if mode == "search":
            self._do_search(value)
        elif mode == "command":
            await self._do_command(value)

    def _do_search(self, query: str) -> None:
        pane = self._current_pane()
        needle = query.lower()
        self.search_matches = [
            i for i, label in enumerate(pane.items) if needle in str(label).lower()
        ]
        self.search_index = -1
        if not self.search_matches:
            self.notify_status(f"no match: {query}")
            return
        self._jump_match(1)
        self.notify_status(f"{len(self.search_matches)} matches for {query}")

    def _jump_match(self, direction: int) -> None:
        if not self.search_matches:
            return
        self.search_index = (self.search_index + direction) % len(self.search_matches)
        self._current_pane().move_to(self.search_matches[self.search_index])
        self._after_move()

    async def _do_command(self, line: str) -> None:
        parts = line.split(maxsplit=1)
        cmd = parts[0].lstrip(":")
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("q", "quit"):
            self.exit()
        elif cmd == "web":
            if self.web is None or not self.web.running:
                self._start_web()
            self.notify_status(self.web.url if self.web and self.web.running else "web failed")
        elif cmd == "noweb":
            if self.web is not None:
                self.web.stop()
                self._update_services()
                self.notify_status("web player stopped")
        elif cmd == "cava":
            await self._toggle_visualiser(force=True)
        elif cmd == "nocava":
            await self._toggle_visualiser(force=False)
        elif cmd == "add":
            self._command_add(arg)
        elif cmd in ("save", "w"):
            self._save_playlist(arg)
        elif cmd in ("load", "open"):
            self._load_playlist(arg)
        elif cmd == "addto":
            self._add_to_playlist(arg)
        elif cmd == "rename":
            parts2 = arg.split(maxsplit=1)
            if len(parts2) == 2 and self.playlists.rename(parts2[0], parts2[1]):
                self.notify_status(f"renamed to “{parts2[1]}”")
                if self._picking_playlist:
                    self._render_playlists()
            else:
                self.notify_status("usage: :rename <old> <new>")
        elif cmd in ("playlists", "ls"):
            self._render_playlists()
        elif cmd in ("rm", "delete"):
            if arg and self.playlists.delete(arg):
                self.notify_status(f"deleted playlist {arg}")
            else:
                self.notify_status(f"no such playlist: {arg}")
        elif cmd == "reload":
            self.library.load()
            self._refresh_library()
            self.notify_status(f"reloaded: {self.library.track_count} tracks")
        elif cmd == "theme":
            try:
                self.theme = arg
                self.notify_status(f"theme: {arg}")
            except Exception:
                self.notify_status(f"unknown theme: {arg}")
        elif cmd == "set":
            self._command_set(arg)
        else:
            self.notify_status(f"unknown command: {cmd}")

    def _command_add(self, arg: str) -> None:
        matches = self.library.search(arg)
        if matches:
            self.player.append(matches)
            self.notify_status(f"added {len(matches)} track(s)")
        else:
            self.notify_status(f"nothing matches {arg}")

    def _command_set(self, arg: str) -> None:
        if "=" not in arg:
            self.notify_status("usage: :set key=value")
            return
        key, _, value = arg.partition("=")
        key, value = key.strip(), value.strip()
        if key == "volume":
            try:
                self.player.set_volume(int(value))
                self.notify_status(f"volume {value}")
            except ValueError:
                self.notify_status("volume must be a number")
        elif key == "replaygain":
            if value in ("smart", "track", "album", "off"):
                self.player.set_replaygain(value)
                self.notify_status(f"replaygain {value}")
            else:
                self.notify_status("replaygain: smart|track|album|off")
        else:
            self.notify_status(f"unknown setting: {key}")


















    # -- playlists -------------------------------------------------------

    def _save_playlist_prompt(self) -> None:
        """S — name and save whatever is currently in the playlist."""
        if not self.player.playlist:
            self.notify_status("playlist is empty — press a to add tracks first")
            return
        self._open_input("command", ":")
        self._prefill(":save ")
        self.notify_status(f"name this playlist ({len(self.player.playlist)} tracks)")

    def _save_playlist(self, name: str) -> None:
        if not name:
            self.notify_status("usage: :save <name>")
            return
        tracks = list(self.player.playlist)
        if not tracks:
            self.notify_status("playlist is empty")
            return
        existed = self.playlists.exists(name)
        self.playlists.save(name, tracks)
        verb = "updated" if existed else "created"
        self.notify_status(f"{verb} playlist “{name}” — {len(tracks)} tracks")

    def _load_playlist(self, name: str) -> None:
        if not name:
            self.notify_status("usage: :load <name>")
            return
        tracks = self.playlists.load(name, self.library)
        if not tracks:
            self.notify_status(f"“{name}” is empty or missing")
            return
        self.player.set_playlist(tracks, 0)
        self._last_playlist = name
        self._set_view(View.PLAYING)
        self.notify_status(f"playing “{name}” — {len(tracks)} tracks")

    def _add_to_playlist(self, name: str) -> None:
        """Add the current selection to a named playlist, creating it if it
        does not exist — Apple Music's 'Add to Playlist'."""
        if not name:
            self.notify_status("usage: :addto <playlist name>")
            return
        selection = self._selected_tracks()
        if not selection:
            self.notify_status("nothing selected")
            return

        existing = self.playlists.load(name, self.library) if self.playlists.exists(name) else []
        known = {t.path for t in existing}
        added = [t for t in selection if t.path not in known]
        self.playlists.save(name, existing + added)

        self._last_playlist = name
        if not existing:
            self.notify_status(
                f"created playlist “{name}” with {len(added)} track(s) — press 2"
            )
        elif added:
            self.notify_status(f"added {len(added)} track(s) to “{name}”")
        else:
            self.notify_status(f"already in “{name}”")
        if self.view is View.PLAYLISTS:
            self._render_playlists()

    def _add_to_playlist_prompt(self) -> None:
        """p — add selection to a playlist, choosing or naming one."""
        selection = self._selected_tracks()
        if not selection:
            self.notify_status("nothing selected")
            return
        self._open_input("command", ":")
        self._prefill(":addto ")
        names = ", ".join(p.name for p in self.playlists.list()[:5]) or "none yet"
        self.notify_status(
            f"add {len(selection)} track(s) to which playlist?  existing: {names}"
        )

    def _show_playlist_picker(self) -> None:
        self._set_view(View.PLAYLISTS)

    def _delete_selected_playlist(self) -> None:
        from .playlists import PlaylistInfo

        selected = self._panes[2].selected
        if not isinstance(selected, PlaylistInfo):
            return
        if self.playlists.delete(selected.name):
            self.notify_status(f"deleted “{selected.name}”")
            self._render_playlists()

    def _rename_selected_playlist(self) -> None:
        from .playlists import PlaylistInfo

        selected = self._panes[2].selected
        if not isinstance(selected, PlaylistInfo):
            return
        self._open_input("command", ":")
        self._prefill(f":rename {selected.name} ")
        self.notify_status(f"rename “{selected.name}” to…")

    # -- toggles ---------------------------------------------------------

    async def _toggle_visualiser(self, force: bool | None = None) -> None:
        want = (not self._cava.enabled) if force is None else force
        self._cava.enabled = want
        if want:
            self.visualiser.bars = self.visualiser._valid_bars(self.size.width)
            await self.visualiser.start()
        else:
            await self.visualiser.stop()
        self._cava.display = want and (
            self.visualiser.running or self.visualiser.error is not None
        )
        self._cava.refresh()
        if want and not self.visualiser.running:
            self.notify_status(f"visualiser: {self.visualiser.error or 'failed to start'}")
        else:
            self.notify_status(f"visualiser {'on' if want else 'off'}")

    def _toggle_web(self) -> None:
        if self.web is not None and self.web.running:
            self.web.stop()
            self.notify_status("web player stopped")
        else:
            self._start_web()
            if self.web and self.web.running:
                self.notify_status(f"web player at {self.web.url}")
        self._update_services()

    # -- tagging ---------------------------------------------------------








