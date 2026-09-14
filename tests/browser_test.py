"""Browser tests.

The browser exists so you can play something that is *not* in your library —
a USB stick, a download, a folder you have not imported. So the important
check is that a file beets has never seen still plays.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from djskippy.config import Config  # noqa: E402
from djskippy.library import Library, Track  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


async def main() -> int:
    cfg = Config.load()
    lib = Library(cfg.library.music_dir, cfg.library.smart_artist_sort)
    real = [t for t in lib.all_tracks if t.exists]
    if not real:
        print("no playable tracks to work with")
        return 1

    # A folder the library knows nothing about, holding a real audio file.
    outside = Path(tempfile.mkdtemp(prefix="dj-skippy-browser-"))
    (outside / "subfolder").mkdir()
    copied = outside / "Not In Library.flac"
    shutil.copy2(real[0].path, copied)

    cfg.web.enabled = cfg.mpris.enabled = False
    cfg.visualiser.enabled = cfg.watcher.enabled = False
    cfg.playback.resume = False
    cfg.playback.volume = 15

    from djskippy.app import DJSkippy, View

    app = DJSkippy(cfg)

    try:
        async with app.run_test(size=(180, 45)) as pilot:
            print("\nlisting")
            await pilot.press("4")
            check("view 4 is Browser", app.view is View.BROWSER)
            app._load_browser(outside)
            rows = app._panes[2].items
            meta = app._panes[2].meta
            check("shows the parent entry", rows[0] == "..")
            check("shows folders", any(r.endswith("/") for r in rows[1:]),
                  str([r for r in rows if r.endswith("/")]))
            check("shows audio files too",
                  any(isinstance(m, Track) for m in meta),
                  f"{sum(isinstance(m, Track) for m in meta)} files")

            print("\nplaying a file the library has never seen")
            index = next(i for i, m in enumerate(meta) if isinstance(m, Track))
            app._panes[2].move_to(index)
            selected = app._panes[2].selected
            check("selected a file", isinstance(selected, Track),
                  getattr(selected, "title", ""))
            check("it really is outside the library",
                  selected.path not in {t.path for t in lib.all_tracks},
                  selected.path)

            await pilot.press("enter")
            await asyncio.sleep(3.0)
            state = app.player.state
            check("it plays", state.track is not None and state.position > 0.3,
                  f"{state.track.title if state.track else None} "
                  f"@ {state.position:.1f}s")
            app.player.stop()

            print("\nadding to playlist and queue")
            app.player.set_playlist([], 0)
            app.player.clear_queue()
            await pilot.press("a")
            check("a appends a file", len(app.player.playlist) == 1,
                  f"{len(app.player.playlist)}")
            await pilot.press("e")
            check("e queues a file", len(app.player.queue) == 1,
                  f"{len(app.player.queue)}")

            print("\na whole folder at once")
            app.player.set_playlist([], 0)
            app._load_browser(outside.parent)
            folder_row = next(
                (i for i, m in enumerate(app._panes[2].meta)
                 if isinstance(m, Path) and m == outside), None
            )
            if folder_row is not None:
                app._panes[2].move_to(folder_row)
                await pilot.press("a")
                check("a on a folder adds its files",
                      len(app.player.playlist) >= 1,
                      f"{len(app.player.playlist)} tracks")
            else:
                check("found the folder to test", False)

            print("\nnavigation")
            app._load_browser(outside)
            before = app._browser_dir
            await pilot.press("h")
            check("h goes up", app._browser_dir == before.parent,
                  str(app._browser_dir))

        return 0
    finally:
        shutil.rmtree(outside, ignore_errors=True)
        print()
        if FAILURES:
            print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        else:
            print("all checks passed")


if __name__ == "__main__":
    code = asyncio.run(main())
    raise SystemExit(1 if FAILURES else (code or 0))
