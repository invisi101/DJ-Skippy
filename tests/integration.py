"""Integration test for the parts that need real devices and services.

Plays a few seconds of actual audio (quietly), starts the web server and hits
its endpoints, registers on D-Bus and drives it via MPRIS, and confirms cava is
producing data. Run with:

    ./.venv/bin/python tests/integration.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from djskippy.cava import CavaVisualiser  # noqa: E402
from djskippy.config import Config  # noqa: E402
from djskippy.library import Library  # noqa: E402
from djskippy.player import Player  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


async def main() -> int:
    cfg = Config.load()
    lib = Library(cfg.library.music_dir, cfg.library.smart_artist_sort)
    tracks = [t for t in lib.all_tracks if t.exists]
    if not tracks:
        print("no playable tracks found")
        return 1

    track = tracks[0]

    print("\nplayback (mpv)")
    player = Player(volume=25, replaygain="smart")
    player.play_track(track)
    await asyncio.sleep(3.0)
    check("track loaded", player.state.track is not None, track.title)
    check("position advancing", player.state.position > 0.3,
          f"{player.state.position:.2f}s")
    check("duration known", player.state.duration > 0,
          f"{player.state.duration:.0f}s")

    print("\nseek and volume")
    player.seek_to(30)
    await asyncio.sleep(1.0)
    check("seek works", player.state.position > 25,
          f"{player.state.position:.1f}s")
    player.set_volume(40)
    check("volume set", player.state.volume == 40)
    player.toggle_pause()
    await asyncio.sleep(0.3)
    check("pause works", player.state.paused is True)
    player.toggle_pause()

    print("\nreplaygain")
    for mode in ("track", "album", "smart", "off"):
        player.set_replaygain(mode)
        check(f"replaygain={mode} accepted", player.state.replaygain == mode)

    print("\ncava")
    vis = CavaVisualiser(bars=24, framerate=30)
    started = await vis.start()
    check("cava started", started, vis.error or "")
    if started:
        await asyncio.sleep(2.0)
        check("cava producing frames", len(vis.levels) > 0,
              f"{len(vis.levels)} bars")
        check("cava responding to audio", any(v > 0 for v in vis.levels),
              f"peak {max(vis.levels):.2f}")
        row = vis.render_row(40)
        check("cava renders a row", len(row) == 40, repr(row[:20]))
        await vis.stop()

    print("\nweb server")
    from djskippy.web import WebServer

    web = WebServer(player, lib, "127.0.0.1", 8099)
    check("web started", web.start(), web.error or "")
    if web.running:
        await asyncio.sleep(0.6)
        import urllib.request
        import json

        with urllib.request.urlopen(f"{web.url}/") as r:
            check("index serves", r.status == 200, f"{len(r.read())} bytes")
        with urllib.request.urlopen(f"{web.url}/api/state") as r:
            state = json.loads(r.read())
            check("api/state works", "title" in state, state.get("title", ""))
        query = track.title.split()[0]
        with urllib.request.urlopen(
            f"{web.url}/api/search?q={urllib.parse.quote(query)}"
        ) as r:
            results = json.loads(r.read())
            check("api/search works", len(results) > 0, f"{len(results)} hits")
        with urllib.request.urlopen(f"{web.url}/stream/{track.id}") as r:
            head = r.read(4)
            check("audio streams", r.status == 200 and len(head) == 4,
                  f"{r.headers.get('Content-Type')}")
        web.stop()
        check("web stopped", not web.running)

    print("\nmpris")
    from djskippy.mpris import MprisService

    mpris = MprisService(player, on_quit=lambda: None)
    ok = await mpris.start()
    check("mpris registered", ok, mpris.error or "")
    if ok:
        proc = await asyncio.create_subprocess_exec(
            "playerctl", "--player=DJSkippy", "metadata", "title",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        title = out.decode().strip()
        check("playerctl reads metadata", title == track.title,
              title or err.decode().strip())

        proc = await asyncio.create_subprocess_exec(
            "playerctl", "--player=DJSkippy", "play-pause",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        await asyncio.sleep(0.4)
        check("playerctl controls playback", player.state.paused is True,
              f"paused={player.state.paused}")
        await mpris.stop()

    player.shutdown()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    import urllib.parse  # noqa: F401  (used above)

    raise SystemExit(asyncio.run(main()))
