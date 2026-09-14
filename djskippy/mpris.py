"""MPRIS2 D-Bus interface.

Registers DJ-Skippy on the session bus so media keys, playerctl, and desktop
bars (Quickshell, Waybar, Plasma's media applet) can all drive it without
knowing anything about DJ-Skippy specifically.

dbus-next is used rather than dbus-python because it is asyncio-native and so
shares Textual's event loop instead of needing a GLib main loop bolted on.
"""

import asyncio
from typing import TYPE_CHECKING

from dbus_next import BusType, PropertyAccess, Variant
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, dbus_property, method

if TYPE_CHECKING:
    from .player import Player

BUS_NAME = "org.mpris.MediaPlayer2.DJSkippy"
OBJECT_PATH = "/org/mpris/MediaPlayer2"


class MprisRoot(ServiceInterface):
    """org.mpris.MediaPlayer2 - the application-level interface."""

    def __init__(self, on_quit) -> "":
        super().__init__("org.mpris.MediaPlayer2")
        self._on_quit = on_quit

    @method()
    def Raise(self) -> "":
        """No-op: we are a terminal application and cannot raise a window."""

    @method()
    def Quit(self) -> "":
        self._on_quit()

    @dbus_property(access=PropertyAccess.READ)
    def CanQuit(self) -> "b":  # noqa: F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanRaise(self) -> "b":  # noqa: F821
        return False

    @dbus_property(access=PropertyAccess.READ)
    def HasTrackList(self) -> "b":  # noqa: F821
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> "s":  # noqa: F821
        return "DJ-Skippy"

    @dbus_property(access=PropertyAccess.READ)
    def DesktopEntry(self) -> "s":  # noqa: F821
        return "dj-skippy"

    @dbus_property(access=PropertyAccess.READ)
    def SupportedUriSchemes(self) -> "as":  # noqa: F821
        return ["file"]

    @dbus_property(access=PropertyAccess.READ)
    def SupportedMimeTypes(self) -> "as":  # noqa: F821
        return [
            "audio/flac", "audio/mpeg", "audio/mp4", "audio/ogg",
            "audio/opus", "audio/x-wav", "audio/x-wavpack", "audio/x-ape",
        ]


class MprisPlayer(ServiceInterface):
    """org.mpris.MediaPlayer2.Player - transport and metadata."""

    def __init__(self, player: "Player") -> "":
        super().__init__("org.mpris.MediaPlayer2.Player")
        self._player = player

    # -- transport -------------------------------------------------------

    @method()
    def Next(self) -> "":
        self._player.next()

    @method()
    def Previous(self) -> "":
        self._player.previous()

    @method()
    def Pause(self) -> "":
        self._player.pause()

    @method()
    def PlayPause(self) -> "":
        self._player.toggle_pause()

    @method()
    def Stop(self) -> "":
        self._player.stop()

    @method()
    def Play(self) -> "":
        self._player.play()

    @method()
    def Seek(self, offset: "x") -> "":  # noqa: F821 - microseconds
        self._player.seek(offset / 1_000_000)

    @method()
    def SetPosition(self, track_id: "o", position: "x") -> "":  # noqa: F821
        self._player.seek_to(position / 1_000_000)

    @method()
    def OpenUri(self, uri: "s") -> "":  # noqa: F821
        """Not supported - DJ-Skippy plays from its own library."""

    # -- properties ------------------------------------------------------

    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> "s":  # noqa: F821
        state = self._player.state
        if not state.track:
            return "Stopped"
        return "Paused" if state.paused else "Playing"

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> "a{sv}":  # noqa: F821
        track = self._player.state.track
        if track is None:
            return {"mpris:trackid": Variant("o", "/org/mpris/MediaPlayer2/TrackList/NoTrack")}
        return {
            "mpris:trackid": Variant("o", f"/org/mpris/MediaPlayer2/Track/{track.id}"),
            "mpris:length": Variant("x", int(track.length * 1_000_000)),
            "xesam:title": Variant("s", track.title),
            "xesam:album": Variant("s", track.album),
            "xesam:artist": Variant("as", [track.artist]),
            "xesam:albumArtist": Variant("as", [track.albumartist]),
            "xesam:trackNumber": Variant("i", track.track_no),
            "xesam:genre": Variant("as", [track.genre] if track.genre else []),
            "xesam:url": Variant("s", f"file://{track.path}"),
        }

    @dbus_property(access=PropertyAccess.READWRITE)
    def Volume(self) -> "d":  # noqa: F821
        return self._player.state.volume / 100.0

    @Volume.setter
    def Volume(self, value: "d"):  # noqa: F821
        self._player.set_volume(int(value * 100))

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> "x":  # noqa: F821
        return int(self._player.state.position * 1_000_000)

    @dbus_property(access=PropertyAccess.READ)
    def MinimumRate(self) -> "d":  # noqa: F821
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def MaximumRate(self) -> "d":  # noqa: F821
        return 1.0

    @dbus_property(access=PropertyAccess.READWRITE)
    def Rate(self) -> "d":  # noqa: F821
        return 1.0

    @Rate.setter
    def Rate(self, value: "d"):  # noqa: F821
        return

    @dbus_property(access=PropertyAccess.READWRITE)
    def Shuffle(self) -> "b":  # noqa: F821
        return self._player.state.shuffle

    @Shuffle.setter
    def Shuffle(self, value: "b"):  # noqa: F821
        if value != self._player.state.shuffle:
            self._player.toggle_shuffle()

    @dbus_property(access=PropertyAccess.READWRITE)
    def LoopStatus(self) -> "s":  # noqa: F821
        from .player import RepeatMode

        return {
            RepeatMode.OFF: "None",
            RepeatMode.ALL: "Playlist",
            RepeatMode.ONE: "Track",
        }[self._player.state.repeat]

    @LoopStatus.setter
    def LoopStatus(self, value: "s"):  # noqa: F821
        from .player import RepeatMode

        mapping = {
            "None": RepeatMode.OFF,
            "Playlist": RepeatMode.ALL,
            "Track": RepeatMode.ONE,
        }
        if value in mapping:
            self._player.state.repeat = mapping[value]

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> "b":  # noqa: F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanGoPrevious(self) -> "b":  # noqa: F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPlay(self) -> "b":  # noqa: F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> "b":  # noqa: F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanSeek(self) -> "b":  # noqa: F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanControl(self) -> "b":  # noqa: F821
        return True


class MprisService:
    """Owns the bus connection and pushes change notifications."""

    def __init__(self, player: "Player", on_quit) -> "":
        self._player = player
        self._on_quit = on_quit
        self._bus: MessageBus | None = None
        self._player_iface: MprisPlayer | None = None
        self.active = False
        self.error: str | None = None

    async def start(self) -> bool:
        """Connect and export. Returns False rather than raising - no session
        bus is a perfectly normal condition (a TTY, a container)."""
        try:
            self._bus = await MessageBus(bus_type=BusType.SESSION).connect()
            root = MprisRoot(self._on_quit)
            self._player_iface = MprisPlayer(self._player)
            self._bus.export(OBJECT_PATH, root)
            self._bus.export(OBJECT_PATH, self._player_iface)
            await self._bus.request_name(BUS_NAME)
            self.active = True
            return True
        except Exception as exc:
            self.error = str(exc)
            self.active = False
            return False

    def notify(self) -> "":
        """Tell subscribers that playback state changed."""
        if not self.active or self._player_iface is None:
            return
        try:
            self._player_iface.emit_properties_changed(
                {
                    "PlaybackStatus": self._player_iface.PlaybackStatus,
                    "Metadata": self._player_iface.Metadata,
                    "Volume": self._player_iface.Volume,
                }
            )
        except Exception:
            pass

    async def stop(self) -> None:
        self.active = False
        if self._bus is not None:
            try:
                self._bus.disconnect()
            except Exception:
                pass
            self._bus = None
