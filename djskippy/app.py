"""The DJ-Skippy interface.

Yazi-style miller columns over the library, cmus's queue/playlist model and
transport keys, vim navigation throughout, cava along the bottom and album art
on the right.

Colours are deliberately ANSI rather than fixed RGB, so DJ-Skippy inherits
whatever theme the terminal is running instead of fighting it.
"""

from __future__ import annotations

import asyncio
from enum import IntEnum
from pathlib import Path
from typing import Any, Sequence

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Input

from . import art as art_mod
from .cava import CavaVisualiser
from .config import Config, load_state, save_state
from .library import Library, Track
from .player import Player, RepeatMode
from .maintenance import BeetsCommand, CommandResult, find_unimported_albums
from .playlists import PlaylistStore
from .tagger import Tagger, TaggerRequest


class View(IntEnum):
    LIBRARY = 1
    PLAYLIST = 2
    QUEUE = 3
    BROWSER = 4
    REVIEW = 5
    IMPORT = 6
    HELP = 7


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

        count = f" {len(self.items)}" if self.items else ""
        header = Text(
            f"{self.pane_title}{count}".ljust(width)[:width],
            style="bold blue" if self.is_active else "bold bright_black",
        )
        header.append("\n")

        body = Text()
        visible = self.items[self.scroll_top : self.scroll_top + height]
        for row, label in enumerate(visible):
            index = self.scroll_top + row
            text = label[: width - 2].ljust(width - 2)
            is_cursor = index == self.cursor
            is_playing = self.marker is not None and index == self.marker

            prefix = "▶ " if is_playing else "  "
            if is_cursor and self.is_active:
                style = "reverse bold"
            elif is_cursor:
                style = "reverse bright_black"
            elif is_playing:
                style = "bold green"
            else:
                style = ""
            body.append(prefix + text + "\n", style=style)

        # Pad so the border does not jump around on short lists.
        for _ in range(height - len(visible)):
            body.append("\n")

        header.append(body)
        return header


class ArtPane(Widget):
    """Album art. Uses textual-image (kitty graphics / sixel) when available,
    and falls back to true-colour half-blocks, which work anywhere."""

    DEFAULT_CSS = """
    ArtPane {
        border: round $panel-lighten-1;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.track_path: str = ""
        self._pil_cache: tuple[str, Any] | None = None

    def set_track(self, path: str) -> None:
        if path != self.track_path:
            self.track_path = path
            self._pil_cache = None
            self.refresh()

    def render(self) -> Any:
        width = max(2, self.size.width - 4)
        height = max(2, self.size.height - 2)

        if not self.track_path:
            return Text("\n  no track", style="bright_black")

        # Preferred path: real terminal graphics.
        try:
            from textual_image.renderable import Image as ImageRenderable

            if self._pil_cache is None or self._pil_cache[0] != self.track_path:
                image = art_mod.load_pil_image(self.track_path)
                self._pil_cache = (self.track_path, image)
            image = self._pil_cache[1]
            if image is not None:
                return ImageRenderable(image, width=width, height=height)
        except Exception:
            pass

        # Fallback: half-blocks.
        rows = art_mod.render_segments(self.track_path, width, height)
        if rows:
            return _SegmentGrid(rows)
        return Text("\n  no album art", style="bright_black")


class _SegmentGrid:
    """Minimal rich renderable for a grid of pre-styled segments."""

    def __init__(self, rows: list[list[Segment]]) -> None:
        self.rows = rows

    def __rich_console__(self, console, options):  # pragma: no cover - visual
        for row in self.rows:
            yield from row
            yield Segment.line()


class CavaPane(Widget):
    """The visualiser strip."""

    DEFAULT_CSS = """
    CavaPane { height: 5; }
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
        if self.message:
            text.append("  " + self.message, style="yellow")
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
    1 Library   2 Playlist   3 Queue   4 Browser
    5 Review    6 Import     7 Help

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

  PLAYLISTS
    a             append selection to the current playlist
    S             name and save the current playlist
    L             browse saved playlists (d delete, r rename)
    p             add selection straight to a named playlist
    :save <name>  :load <name>  :addto <name>  :rename <old> <new>

  LIBRARY
    a             append selection to playlist
    e             enqueue selection (plays next, ahead of playlist)
    t             tag this album from MusicBrainz
    d             remove from playlist / queue

  TOGGLES
    V             visualiser      A   album art      w   web player
    ?             this help       q   quit

  IMPORT & MAINTENANCE  (view 6 — you never need a beets command)
    I             import every album on disk that is not in the library yet
    enter         tag just the highlighted folder, interactively
    D             find duplicates          M   albums with missing tracks
    R             re-sync tags from MusicBrainz
    F             download missing album art
    esc           stop a running import
    :dup  :missing  :mbsync  :fetchart  :stats  :import [path]

  AUTOMATIC IMPORT
    New albums dropped into your music folder are detected, allowed to
    finish copying, then looked up on MusicBrainz and tagged unattended.
    Matches at or above 90% are applied; anything weaker, and anything
    that duplicates an existing album, is parked in Review (5) instead
    of being guessed at. Nothing is ever deleted or overwritten.

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
    #artists { width: 24; }
    #albums  { width: 34; }
    #tracks  { width: 1fr; }
    #art     { width: 34; }
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
        )
        self.visualiser = CavaVisualiser(
            bars=40 if self.cfg.visualiser.bars == "auto" else int(self.cfg.visualiser.bars),
            framerate=self.cfg.visualiser.framerate,
        )
        self.tagger = Tagger(on_request=self._on_tag_request)
        self.playlists = PlaylistStore()

        self.view = View.LIBRARY
        self.focus_column = 0
        self.pending_key: str | None = None
        self.search_matches: list[int] = []
        self.search_index = 0
        self.mode: str | None = None  # "search" | "command"
        self.web = None
        self.mpris = None
        self.watcher = None
        self._tag_request: TaggerRequest | None = None
        # When the watcher triggers an import we answer the tagger ourselves
        # unless the match is too weak to trust.
        self._auto_tagging = False
        self.review_queue: list[Path] = []
        self._picking_playlist = False
        self.beets_cmd = BeetsCommand(on_done=self._on_beets_done)
        self._unimported: list = []
        self._bulk_running = False

    # -- layout ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="columns"):
                yield ListPane("Artists", id="artists")
                yield ListPane("Albums", id="albums")
                yield ListPane("Tracks", id="tracks")
                yield ArtPane(id="art")
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
        self._art = self.query_one("#art", ArtPane)
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

        self._refresh_library()
        self._update_focus()

        if self.library.track_count == 0:
            self.notify_status(
                f"No music found in {self.cfg.library.music_dir} — press 4 to browse and t to tag"
            )
        else:
            self.notify_status(
                f"{self.library.track_count} tracks via {self.library.backend}"
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
        if self.cfg.watcher.enabled:
            self._start_watcher()
        if self.cfg.playback.resume:
            self._restore_state()

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
        width = max(8, self.size.width)
        self.visualiser.bars = width
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
        else:
            self.notify_status(f"web player failed: {self.web.error}")

    async def _start_mpris(self) -> None:
        from .mpris import MprisService

        self.mpris = MprisService(self.player, on_quit=lambda: self.exit())
        if await self.mpris.start():
            self._update_services()
        else:
            self.notify_status(f"mpris unavailable: {self.mpris.error}")

    def _start_watcher(self) -> None:
        from .watcher import MusicWatcher

        self.watcher = MusicWatcher(
            self.cfg.library.music_dir,
            known_paths=lambda: {t.path for t in self.library.all_tracks},
            on_ready=self._on_new_album,
            on_status=self._watcher_status,
            settle_seconds=self.cfg.watcher.settle_seconds,
        )
        if self.watcher.start():
            self._update_services()
        else:
            self.notify_status(f"watcher unavailable: {self.watcher.error}")

    def _watcher_status(self, message: str) -> None:
        """Called from the watcher thread."""
        try:
            self.call_from_thread(self.notify_status, message)
        except Exception:
            pass

    def _on_new_album(self, path: Path) -> None:
        """A new album finished copying. Import it unattended."""
        if self.tagger.running:
            # Do not stack imports; the watcher will offer it again.
            return
        self._auto_tagging = True
        try:
            self.call_from_thread(
                self.notify_status, f"auto-importing {path.name}…"
            )
        except Exception:
            pass
        self.tagger.start([str(path)])

    def _update_services(self) -> None:
        parts = []
        if self.web is not None and self.web.running:
            parts.append(self.web.url)
        if self.mpris is not None and self.mpris.active:
            parts.append("mpris")
        if self.watcher is not None and self.watcher.running:
            parts.append(self.watcher.status_line())
        if self.review_queue:
            parts.append(f"{len(self.review_queue)} to review")
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
        width = max(20, self._panes[2].size.width - 12)
        labels = [
            f"{t.display_title[: width - 8].ljust(width - 8)} {t.length_str:>6}"
            for t in tracks
        ]
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

        selected = self._panes[2].selected
        return [selected] if isinstance(selected, Track) else []

    # -- ticking ---------------------------------------------------------

    def _tick(self) -> None:
        self._now.refresh()
        self._sync_marker()
        if self.player.state.track is not None:
            self._art.set_track(self.player.state.track.path)

    def _tick_cava(self) -> None:
        if self._cava.enabled and self.visualiser.running:
            self._cava.refresh()

    def _on_player_change(self) -> None:
        """Called from mpv's thread - must hop back to the UI thread."""
        try:
            self.call_from_thread(self._on_player_change_ui)
        except Exception:
            pass

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
        if view is not View.PLAYLIST:
            self._picking_playlist = False
        self.view = view
        self._status.view = view
        self._status.refresh()

        columns = self.query_one("#columns")
        if view is View.LIBRARY:
            for pane in self._panes:
                pane.display = True
            self._panes[0].pane_title = "Artists"
            self._panes[1].pane_title = "Albums"
            self._panes[2].pane_title = "Tracks"
            self._refresh_library()
        elif view is View.PLAYLIST:
            self._panes[0].display = False
            self._panes[1].display = False
            self._panes[2].display = True
            self._panes[2].pane_title = "Playlist"
            tracks = list(self.player.playlist)
            self._panes[2].set_items(
                [f"{t.title}  —  {t.artist}" for t in tracks], tracks
            )
        elif view is View.QUEUE:
            self._panes[0].display = False
            self._panes[1].display = False
            self._panes[2].display = True
            self._panes[2].pane_title = "Queue (plays next)"
            tracks = list(self.player.queue)
            self._panes[2].set_items(
                [f"{t.title}  —  {t.artist}" for t in tracks], tracks
            )
        elif view is View.BROWSER:
            self._panes[0].display = False
            self._panes[1].display = False
            self._panes[2].display = True
            self._panes[2].pane_title = f"Browser — {self.cfg.library.music_dir}"
            self._load_browser(self.cfg.library.music_dir)
        elif view is View.REVIEW:
            self._panes[0].display = False
            self._panes[1].display = False
            self._panes[2].display = True
            self._panes[2].pane_title = "Review — albums the watcher could not tag alone"
            if self.review_queue:
                labels = [f"{p.name}   ({p.parent.name}/)" for p in self.review_queue]
                self._panes[2].set_items(labels, list(self.review_queue))
            else:
                empty = ["", "  Nothing waiting.", "",
                         "  New albums dropped into your music folder are",
                         "  tagged automatically. Anything ambiguous lands",
                         "  here instead of being guessed at.", ""]
                self._panes[2].set_items(empty, empty)
        elif view is View.IMPORT:
            self._panes[0].display = False
            self._panes[1].display = False
            self._panes[2].display = True
            self._panes[2].pane_title = "Import & maintenance"
            self._refresh_import_view()
        elif view is View.HELP:
            self._panes[0].display = False
            self._panes[1].display = False
            self._panes[2].display = True
            self._panes[2].pane_title = "Help"
            lines = HELP_TEXT.splitlines()
            self._panes[2].set_items(lines, lines)
        columns.refresh()

    def _load_browser(self, directory: Path) -> None:
        try:
            entries = sorted(
                [p for p in Path(directory).iterdir() if p.is_dir()],
                key=lambda p: p.name.lower(),
            )
        except OSError as exc:
            self.notify_status(str(exc))
            return
        labels = [".."] + [f"{p.name}/" for p in entries]
        meta = [Path(directory).parent] + entries
        self._panes[2].set_items(labels, meta)
        self._browser_dir = Path(directory)

    # -- key handling ----------------------------------------------------

    async def on_key(self, event) -> None:
        if self.mode is not None:
            return  # the Input owns the keyboard

        key = event.key
        event.stop()
        event.prevent_default()

        # A pending tagging decision owns the keyboard until it is answered.
        if self._tag_request is not None and self._handle_tagging_key(key):
            return

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
        elif key in "1234567":
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
            if self._picking_playlist:
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
            if self._picking_playlist:
                self._delete_selected_playlist()
            elif self.view is View.QUEUE:
                self.player.clear_queue()
                self._set_view(View.QUEUE)
                self.notify_status("queue cleared")
            elif self.view is View.PLAYLIST:
                self.player.remove_from_playlist(pane.cursor)
                self._set_view(View.PLAYLIST)
        elif key == "t":
            self._start_tagging()
        elif key == "I":
            self._import_everything()
        elif key == "D" and self.view is View.IMPORT:
            self._run_beets_op("duplicates")
        elif key == "M" and self.view is View.IMPORT:
            self._run_beets_op("missing")
        elif key == "R" and self.view is View.IMPORT:
            self._run_beets_op("mbsync")
        elif key == "F" and self.view is View.IMPORT:
            self._run_beets_op("fetchart")
        elif key == "escape" and self.tagger.running:
            self.tagger.abort()
            self.notify_status("import aborted")
        elif key == "S":
            self._save_playlist_prompt()
        elif key == "p":
            self._add_to_playlist_prompt()
        elif key == "L":
            self._show_playlist_picker()

        # -- toggles
        elif key == "V":
            await self._toggle_visualiser()
        elif key == "A":
            self._art.display = not self._art.display
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
            elif self.focus_column == 2:
                selected = self._panes[2].selected
                if isinstance(selected, Track):
                    self._art.set_track(selected.path)

    async def _activate(self) -> None:
        """Enter: play, or descend."""
        if self.view is View.BROWSER:
            selected = self._panes[2].selected
            if isinstance(selected, Path):
                self._load_browser(selected)
            return

        if self._picking_playlist:
            selected = self._panes[2].selected
            from .playlists import PlaylistInfo

            if isinstance(selected, PlaylistInfo):
                self._picking_playlist = False
                self._load_playlist(selected.name)
            return

        if self.view is View.IMPORT:
            selected = self._panes[2].selected
            if isinstance(selected, Path):
                self._start_tagging(str(selected))
            return

        if self.view is View.REVIEW:
            selected = self._panes[2].selected
            if isinstance(selected, Path):
                self.review_queue = [p for p in self.review_queue if p != selected]
                self._update_services()
                self._start_tagging(str(selected))
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
            self._art.set_track(selected.path)

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
        elif cmd == "import":
            if arg:
                self._start_tagging(arg)
            else:
                self._import_everything()
        elif cmd in ("dup", "duplicates"):
            self._run_beets_op("duplicates")
        elif cmd == "missing":
            self._run_beets_op("missing")
        elif cmd == "mbsync":
            self._run_beets_op("mbsync")
        elif cmd == "fetchart":
            self._run_beets_op("fetchart")
        elif cmd == "stats":
            self._run_beets_op("stats")
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
                    self._show_playlist_picker()
            else:
                self.notify_status("usage: :rename <old> <new>")
        elif cmd in ("playlists", "ls"):
            self._show_playlist_picker()
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

    # -- bulk import and beets operations --------------------------------

    def _refresh_import_view(self) -> None:
        """Populate the Import view with what is and is not in the library."""
        self._unimported = find_unimported_albums(
            self.cfg.library.music_dir, self.library
        )
        on_disk_tracks = sum(f.audio_count for f in self._unimported)

        lines = [
            f"  Library:   {self.library.track_count} tracks tagged "
            f"({self.library.backend})",
            f"  Waiting:   {len(self._unimported)} album folders, "
            f"{on_disk_tracks} tracks not yet in the library",
            "",
        ]

        if self.tagger.running:
            lines.append(f"  RUNNING — {self.tagger.current_album}")
            lines.append(f"  {self.tagger.progress}")
            lines.append("  press Esc to stop")
        elif self._unimported:
            lines.append("  press I to import all of it")
            lines.append(
                f"  matches at or above {self.cfg.watcher.auto_threshold:.0f}% are "
                "applied automatically;"
            )
            lines.append("  anything weaker goes to Review (5) for you to decide")
        else:
            lines.append("  Everything on disk is in the library.")

        lines.extend([
            "",
            "  MAINTENANCE  (no terminal required)",
            "    D  find duplicates        M  albums with missing tracks",
            "    R  re-sync tags from MusicBrainz   F  fetch missing art",
            "",
            "  ALBUM FOLDERS NOT YET IMPORTED",
        ])

        meta: list[Any] = [None] * len(lines)
        for folder in self._unimported:
            lines.append(f"    {folder.label}")
            meta.append(folder.path)

        self._panes[2].set_items(lines, meta)

    def _import_everything(self) -> None:
        """I — tag every album folder that is not yet in the library."""
        if self.tagger.running:
            self.notify_status("an import is already running")
            return

        self._unimported = find_unimported_albums(
            self.cfg.library.music_dir, self.library
        )
        if not self._unimported:
            self.notify_status("everything on disk is already in the library")
            return

        paths = [str(f.path) for f in self._unimported]
        tracks = sum(f.audio_count for f in self._unimported)

        # Unattended: apply confident matches, park the rest in Review.
        self._auto_tagging = True
        self._bulk_running = True
        self.tagger.start(paths)
        self._set_view(View.IMPORT)
        self.notify_status(
            f"importing {len(paths)} albums ({tracks} tracks) — "
            f"≥{self.cfg.watcher.auto_threshold:.0f}% applied, rest to Review"
        )
        self.set_interval(1.0, self._tick_import, name="import-progress")

    def _tick_import(self) -> None:
        if self.view is View.IMPORT:
            self._refresh_import_view()
        if self._bulk_running and not self.tagger.running:
            self._bulk_running = False
            self._finish_tagging()

    def _run_beets_op(self, name: str) -> None:
        """Run one of beets' maintenance commands and show its output."""
        if self.beets_cmd.running:
            self.notify_status(f"{self.beets_cmd.current} is already running")
            return
        if self.tagger.running:
            self.notify_status("wait for the import to finish first")
            return
        if self.beets_cmd.start(name):
            _, description = BeetsCommand.OPERATIONS[name]
            self.notify_status(f"running {name} — {description}…")
            self._set_view(View.IMPORT)
            self._panes[2].pane_title = f"{name} — working…"
            self._panes[2].set_items([f"  running {name}…"], [None])
        else:
            self.notify_status(f"could not run {name}")

    def _on_beets_done(self, result: CommandResult) -> None:
        """Called from the command thread."""
        try:
            self.call_from_thread(self._show_beets_result, result)
        except Exception:
            pass

    def _show_beets_result(self, result: CommandResult) -> None:
        self._set_view(View.IMPORT)
        self._panes[2].pane_title = f"{result.name} — result"
        if result.error:
            lines = [f"  {result.name} failed: {result.error}"]
        elif not result.lines:
            lines = [f"  {result.name}: nothing to report."]
        else:
            lines = [f"  {result.name} — {len(result.lines)} line(s)", ""]
            lines += ["  " + line for line in result.lines]
        self._panes[2].set_items(lines, [None] * len(lines))
        self.notify_status(f"{result.name} finished")
        if result.name in ("fetchart", "mbsync"):
            self.library.load()

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
        self._picking_playlist = False
        self.beets_cmd = BeetsCommand(on_done=self._on_beets_done)
        self._unimported: list = []
        self._bulk_running = False
        self._set_view(View.PLAYLIST)
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

        if not existing:
            self.notify_status(f"created “{name}” with {len(added)} track(s)")
        elif added:
            self.notify_status(f"added {len(added)} track(s) to “{name}”")
        else:
            self.notify_status(f"already in “{name}”")

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
        """L — browse saved playlists."""
        infos = self.playlists.list()
        self._set_view(View.PLAYLIST)
        self._picking_playlist = True
        self._panes[2].pane_title = "Playlists — enter to play, d delete, r rename"
        if infos:
            self._panes[2].set_items([i.label for i in infos], infos)
            self.notify_status(f"{len(infos)} saved playlist(s)")
        else:
            empty = ["", "  No playlists yet.", "",
                     "  Build one: select albums or tracks and press a to",
                     "  add them to the playlist, then S to name and save it.",
                     "",
                     "  Or press p on any selection to add it straight to a",
                     "  named playlist, creating it if it does not exist.", ""]
            self._panes[2].set_items(empty, empty)
            self.notify_status("no playlists yet — press a then S to make one")

    def _delete_selected_playlist(self) -> None:
        from .playlists import PlaylistInfo

        selected = self._panes[2].selected
        if not isinstance(selected, PlaylistInfo):
            return
        if self.playlists.delete(selected.name):
            self.notify_status(f"deleted “{selected.name}”")
            self._show_playlist_picker()

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
            self.visualiser.bars = max(8, self.size.width)
            await self.visualiser.start()
        else:
            await self.visualiser.stop()
        self._cava.refresh()
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

    def _start_tagging(self, path: str | None = None) -> None:
        if self.tagger.running:
            self.notify_status("tagger already running")
            return

        target = path
        if target is None:
            if self.view is View.BROWSER:
                selected = self._panes[2].selected
                target = str(selected) if isinstance(selected, Path) else None
            elif self.view is View.LIBRARY:
                tracks = self._selected_tracks()
                if tracks:
                    target = str(Path(tracks[0].path).parent)
        if not target:
            self.notify_status("select an album or folder first (4 = browser)")
            return

        self._auto_tagging = False
        self.notify_status(f"tagging {Path(target).name} — looking up MusicBrainz…")
        self.tagger.start([target])

    def _on_tag_request(self, request: TaggerRequest) -> None:
        """Called from the tagger thread."""
        if self._auto_tagging and self._auto_answer(request):
            return
        try:
            self.call_from_thread(self._show_tag_request, request)
        except Exception:
            self.tagger.respond("skip")

    def _auto_answer(self, request: TaggerRequest) -> bool:
        """Decide an unattended import, or defer it to the review queue.

        Returns True if the request was answered here. The rule is
        deliberately conservative: apply only clearly-correct matches, never
        destroy an existing copy, and park everything else for a human.
        """
        if request.is_duplicate:
            # KEEP is the only safe unattended answer - never remove or
            # overwrite an existing copy on a guess.
            self.tagger.respond("keep")
            self._defer_for_review(request.path, "duplicate")
            return True

        best = request.candidates[0] if request.candidates else None
        if best is not None and best.similarity >= self.cfg.watcher.auto_threshold:
            self.tagger.respond("apply")
            try:
                self.call_from_thread(
                    self.notify_status,
                    f"auto-tagged {best.artist} — {best.album} ({best.similarity:.0f}%)",
                )
            except Exception:
                pass
            return True

        reason = (
            f"best match only {best.similarity:.0f}%" if best else "no MusicBrainz match"
        )
        self.tagger.respond("skip")
        self._defer_for_review(request.path, reason)
        return True

    def _defer_for_review(self, path: str, reason: str) -> None:
        target = Path(path)
        if target not in self.review_queue:
            self.review_queue.append(target)
        try:
            self.call_from_thread(
                self.notify_status,
                f"{target.name}: {reason} — press 5 to review",
            )
            self.call_from_thread(self._update_services)
        except Exception:
            pass

    def _show_tag_request(self, request: TaggerRequest) -> None:
        self._tag_request = request
        lines: list[str] = []
        if request.is_duplicate:
            lines.append(f"DUPLICATE: {request.duplicate_info}")
            lines.append("")
            lines.append("  k  keep both (safe)")
            lines.append("  u  upgrade — replace the old copy")
            lines.append("  m  merge")
            lines.append("  s  skip")
        elif not request.candidates:
            lines.append(f"No MusicBrainz match for {Path(request.path).name}")
            lines.append(f"{request.item_count} tracks")
            lines.append("")
            lines.append("  u  use existing tags as-is")
            lines.append("  s  skip")
        else:
            best = request.candidates[0]
            lines.append(f"{best.similarity:.1f}%   {best.artist} — {best.album}")
            lines.append(best.info_line)
            if best.url:
                lines.append(best.url)
            lines.append("")
            for change in best.changes[:40]:
                mark = "≠" if change.changed else " "
                if change.changed:
                    lines.append(f" {mark} {change.old}  →  {change.new}")
                else:
                    lines.append(f" {mark} {change.new}")
            if best.missing:
                lines.append("")
                lines.append(f" missing {len(best.missing)} track(s): " + ", ".join(best.missing[:3]))
            lines.append("")
            lines.append("  a  apply    s  skip    u  use as-is")
            if len(request.candidates) > 1:
                lines.append(f"  1-{min(9, len(request.candidates))}  pick another candidate")

        self._set_view(View.HELP)
        self._panes[2].pane_title = "Tagging"
        self._panes[2].set_items(lines, lines)
        self.notify_status("tagging: a=apply  s=skip  u=as-is")

    def _handle_tagging_key(self, key: str) -> bool:
        """Answer a pending tagging decision. Returns True if the key was
        consumed, so normal bindings do not also fire."""
        request = self._tag_request
        if request is None:
            return False

        if request.is_duplicate:
            mapping = {"k": "keep", "u": "upgrade", "m": "merge", "s": "skip"}
        else:
            mapping = {"a": "apply", "s": "skip", "u": "asis"}

        if key in mapping:
            self.tagger.respond(mapping[key])
            self._tag_request = None
            self.notify_status(f"tagging: {mapping[key]}")
            if not self.tagger.running:
                self._finish_tagging()
            return True

        # Digits pick an alternative candidate.
        if key.isdigit() and key != "0" and request.candidates:
            index = int(key) - 1
            if index < len(request.candidates):
                self.tagger.respond(index)
                self._tag_request = None
                self.notify_status(f"tagging: candidate {key}")
                return True

        if key == "escape":
            self.tagger.abort()
            self._tag_request = None
            self.notify_status("tagging aborted")
            self._finish_tagging()
            return True

        return False

    def _finish_tagging(self) -> None:
        """Reload the library once an import run completes."""
        self._auto_tagging = False
        stats = self.tagger.stats
        self.library.load()
        self._set_view(View.LIBRARY)
        self.notify_status(
            f"tagged {stats['imported']} · as-is {stats['asis']} · skipped {stats['skipped']}"
        )
