"""Transport behaviour under the gapless handover.

Pre-loading the next track into mpv makes the handover seamless, but it means
mpv holds state of its own that must not drift from ours. Every manual action -
next, previous, shuffle, repeat, queueing, replacing the playlist - has to keep
the two in step, or the wrong track plays.

These are the checks that decide whether the pre-loading is worth its
complexity.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from djskippy.config import Config  # noqa: E402
from djskippy.library import Library  # noqa: E402
from djskippy.player import Player, RepeatMode  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


async def settle(seconds: float = 1.2) -> None:
    await asyncio.sleep(seconds)


async def main() -> int:
    cfg = Config.load()
    lib = Library(cfg.library.music_dir, cfg.library.smart_artist_sort)
    artist = next(
        (a for a in lib.artists()
         if lib.albums(a) and len(lib.tracks(a, lib.albums(a)[0])) >= 4),
        None,
    )
    if artist is None:
        print("need an album with at least four tracks")
        return 1
    album = lib.albums(artist)[0]
    tracks = [t for t in lib.tracks(artist, album) if t.exists][:5]
    if len(tracks) < 4:
        print("need four playable tracks")
        return 1

    player = Player(volume=8)
    here = lambda: (player.state.track.title if player.state.track else None)

    try:
        print(f"\nusing {artist} — {album}")

        print("\nstarting a playlist")
        player.set_playlist(tracks, 0)
        await settle()
        check("plays the first track", player.state.track.path == tracks[0].path,
              here())
        check("index is correct", player.index == 0, str(player.index))
        check("next track pre-loaded for a gapless handover",
              len(player._mpv_entries) == 2, str(len(player._mpv_entries)))

        print("\nmanual next")
        player.next()
        await settle()
        check("moved to track two", player.state.track.path == tracks[1].path,
              here())
        check("index followed", player.index == 1, str(player.index))
        check("still pre-loaded", len(player._mpv_entries) == 2,
              str(len(player._mpv_entries)))

        print("\nmanual previous")
        player.seek_to(0)
        await settle(0.6)
        player.previous()
        await settle()
        check("went back to track one",
              player.state.track.path == tracks[0].path, here())

        print("\nstarting mid-playlist")
        player.set_playlist(tracks, 2)
        await settle()
        check("starts where asked", player.state.track.path == tracks[2].path,
              here())
        check("index matches", player.index == 2, str(player.index))

        print("\nthe queue interrupts")
        player.set_playlist(tracks, 0)
        await settle()
        player.enqueue([tracks[3]])
        check("queue holds one", len(player.queue) == 1)
        player.next()
        await settle()
        check("queued track played next",
              player.state.track.path == tracks[3].path, here())
        check("queue drained", len(player.queue) == 0)
        player.next()
        await settle()
        check("playlist resumed after the queue",
              player.state.track.path == tracks[1].path, here())

        print("\nrepeat one")
        player.set_playlist(tracks, 0)
        await settle()
        player.state.repeat = RepeatMode.ONE
        player.next()
        await settle()
        check("stays on the same track",
              player.state.track.path == tracks[0].path, here())
        player.state.repeat = RepeatMode.OFF

        print("\nrepeat all wraps around")
        player.set_playlist(tracks, len(tracks) - 1)
        await settle()
        player.state.repeat = RepeatMode.ALL
        player.next()
        await settle()
        check("wrapped to the first track",
              player.state.track.path == tracks[0].path, here())
        player.state.repeat = RepeatMode.OFF

        print("\nshuffle")
        player.set_playlist(tracks, 0)
        await settle()
        player.toggle_shuffle()
        check("shuffle on", player.state.shuffle is True)
        player.next()
        await settle()
        check("still playing something",
              player.state.track is not None, here())
        check("mpv and our index agree",
              player.state.track.path
              == player._playlist[player.index].path,
              f"{here()} vs {player._playlist[player.index].title}")
        player.toggle_shuffle()

        print("\nend of playlist without repeat")
        player.set_playlist(tracks[:2], 1)
        await settle()
        player.next()
        await settle()
        check("stops at the end", player.state.track is None, here())

        print("\nreplacing the playlist mid-track")
        player.set_playlist(tracks, 0)
        await settle()
        player.set_playlist(list(reversed(tracks)), 0)
        await settle()
        check("plays the new first track",
              player.state.track.path == tracks[-1].path, here())
        check("mpv was not left holding the old queue",
              len(player._mpv_entries) == 2
              and player._mpv_entries[0].path == tracks[-1].path,
              str([t.title for t in player._mpv_entries]))

        print("\nstop clears cleanly")
        player.stop()
        await settle(0.5)
        check("stopped", player.state.track is None)

        return 0
    finally:
        player.shutdown()
        print()
        if FAILURES:
            print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        else:
            print("all checks passed")


if __name__ == "__main__":
    code = asyncio.run(main())
    raise SystemExit(1 if FAILURES else (code or 0))
