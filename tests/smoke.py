"""Headless smoke test.

Boots the real application with Textual's test harness - no terminal required -
and drives it through the key bindings, checking that nothing raises and that
the panes populate. Run with:

    ./.venv/bin/python tests/smoke.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from djskippy.app import DJSkippy, View  # noqa: E402
from djskippy.config import Config  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


async def main() -> int:
    cfg = Config.load()
    # Keep the smoke test off the real web port and D-Bus name.
    cfg.web.enabled = False
    cfg.mpris.enabled = False
    cfg.visualiser.enabled = False
    cfg.playback.resume = False

    app = DJSkippy(cfg)

    print("\nlibrary")
    check("tracks found", app.library.track_count > 0,
          f"{app.library.track_count} tracks")

    async with app.run_test(size=(160, 45)) as pilot:

        print("\nboot")
        check("artists pane populated", len(app._panes[0].items) > 0,
              f"{len(app._panes[0].items)} artists")
        check("albums pane populated", len(app._panes[1].items) > 0,
              f"{len(app._panes[1].items)} albums")
        check("tracks pane populated", len(app._panes[2].items) > 0,
              f"{len(app._panes[2].items)} tracks")

        print("\nvim navigation")
        start = app._panes[0].cursor
        await pilot.press("j", "j")
        check("j moves down", app._panes[0].cursor == start + 2)
        await pilot.press("k")
        check("k moves up", app._panes[0].cursor == start + 1)
        await pilot.press("G")
        check("G jumps to end",
              app._panes[0].cursor == len(app._panes[0].items) - 1)
        await pilot.press("g", "g")
        check("gg jumps to top", app._panes[0].cursor == 0)

        print("\nmiller columns")
        await pilot.press("l")
        check("l moves right", app.focus_column == 1)
        await pilot.press("l")
        check("l again", app.focus_column == 2)
        await pilot.press("h", "h")
        check("h moves back", app.focus_column == 0)

        print("\ncolumn linkage")
        first_albums = list(app._panes[1].items)
        await pilot.press("j")
        check("albums follow artist cursor",
              list(app._panes[1].items) != first_albums or len(first_albums) == 0)

        print("\nviews")
        for key, view in (("2", View.PLAYLISTS), ("3", View.PLAYING),
                          ("4", View.BROWSER), ("5", View.HELP),
                          ("1", View.LIBRARY)):
            await pilot.press(key)
            check(f"view {key} = {view.name}", app.view is view)

        print("\nqueue and playlist")
        await pilot.press("1")
        await pilot.press("l")          # into albums
        await pilot.press("e")          # enqueue the album
        check("enqueue populated queue", len(app.player.queue) > 0,
              f"{len(app.player.queue)} queued")
        await pilot.press("a")          # append to playlist
        check("append populated playlist", len(app.player.playlist) > 0,
              f"{len(app.player.playlist)} in playlist")

        print("\ntransport (no audio device needed)")
        await pilot.press("s")
        check("shuffle toggles", app.player.state.shuffle is True)
        await pilot.press("s")
        check("shuffle toggles back", app.player.state.shuffle is False)
        await pilot.press("r")
        check("repeat cycles", app.player.state.repeat.value == "all")
        volume = app.player.state.volume
        await pilot.press("minus")
        check("volume down", app.player.state.volume == volume - 5,
              str(app.player.state.volume))

        print("\nsearch")
        # Search runs against whichever pane holds the cursor, so take the
        # needle from that same pane - and use only plain letters, since
        # pilot.press() needs key names for anything else.
        pane = app._current_pane()
        source = "".join(c for c in " ".join(pane.items) if c.isalpha())
        target = source[:4].lower()
        await pilot.press("slash")
        check("search opens input", app.mode == "search")
        for ch in target:
            await pilot.press(ch)
        await pilot.press("enter")
        check("search closed input", app.mode is None)
        check("search found matches", len(app.search_matches) > 0,
              f"{len(app.search_matches)} matches")

        print("\ncommand mode")
        await pilot.press("colon")
        check("command opens input", app.mode == "command")
        for ch in "set volume=70":
            await pilot.press("space" if ch == " " else ch)
        await pilot.press("enter")
        check("command applied", app.player.state.volume == 70,
              str(app.player.state.volume))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
