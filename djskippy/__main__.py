"""Entry point and command-line interface.

`dj-skippy` with no arguments launches the TUI. The remote subcommands let you
drive a running instance from a shell or a keybinding, the way cmus-remote
does - except the transport goes over MPRIS, so it also works with playerctl
and any desktop bar that speaks it.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__


def _remote(action: str) -> int:
    """Drive a running DJ-Skippy over MPRIS."""
    import asyncio

    from dbus_next import BusType, Variant
    from dbus_next.aio import MessageBus

    from .mpris import BUS_NAME, OBJECT_PATH

    async def run() -> int:
        try:
            bus = await MessageBus(bus_type=BusType.SESSION).connect()
            introspection = await bus.introspect(BUS_NAME, OBJECT_PATH)
            obj = bus.get_proxy_object(BUS_NAME, OBJECT_PATH, introspection)
            player = obj.get_interface("org.mpris.MediaPlayer2.Player")
        except Exception:
            print("DJ-Skippy is not running", file=sys.stderr)
            return 1

        if action == "status":
            status = await player.get_playback_status()
            metadata = await player.get_metadata()

            def field(key: str, default: str = "") -> str:
                value = metadata.get(key)
                if value is None:
                    return default
                inner = value.value if isinstance(value, Variant) else value
                if isinstance(inner, list):
                    return inner[0] if inner else default
                return str(inner)

            title = field("xesam:title")
            artist = field("xesam:artist")
            if title:
                print(f"{status}: {title} — {artist}")
            else:
                print(status)
            return 0

        handlers = {
            "play": player.call_play,
            "pause": player.call_pause,
            "playpause": player.call_play_pause,
            "stop": player.call_stop,
            "next": player.call_next,
            "prev": player.call_previous,
            "previous": player.call_previous,
        }
        handler = handlers.get(action)
        if handler is None:
            print(f"unknown action: {action}", file=sys.stderr)
            return 2
        await handler()
        return 0

    return asyncio.run(run())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dj-skippy",
        description="DJ-Skippy — the ultimate one stop shop TUI music player.",
    )
    parser.add_argument("--version", action="version", version=f"DJ-Skippy {__version__}")
    parser.add_argument(
        "--music-dir", metavar="PATH",
        help="override the music folder for this run",
    )
    parser.add_argument(
        "--no-web", action="store_true", help="do not start the browser player",
    )
    parser.add_argument(
        "--no-cava", action="store_true", help="do not start the visualiser",
    )
    parser.add_argument(
        "--no-mpris", action="store_true", help="do not register on D-Bus",
    )
    parser.add_argument(
        "--remote", metavar="ACTION",
        help="control a running instance: play, pause, playpause, stop, "
             "next, prev, status",
    )
    args = parser.parse_args(argv)

    if args.remote:
        return _remote(args.remote)

    from .app import DJSkippy
    from .config import Config

    config = Config.load()
    if args.music_dir:
        from pathlib import Path

        config.library.music_dir = Path(args.music_dir).expanduser().resolve()
    if args.no_web:
        config.web.enabled = False
    if args.no_cava:
        config.visualiser.enabled = False
    if args.no_mpris:
        config.mpris.enabled = False

    DJSkippy(config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
