"""Playlist tests: create, add to, load, rename, delete.

Exercises PlaylistStore against a temporary directory and the real library, and
then drives the same operations through the running application's key
bindings, to confirm the UI is wired to the store.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from djskippy.config import Config  # noqa: E402
from djskippy.library import Library  # noqa: E402
from djskippy.playlists import PlaylistStore, safe_filename  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


async def main() -> int:
    cfg = Config.load()
    lib = Library(cfg.library.music_dir, cfg.library.smart_artist_sort)
    tracks = [t for t in lib.all_tracks if t.exists][:8]
    if len(tracks) < 4:
        print("not enough playable tracks to test")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="dj-skippy-pl-"))
    store = PlaylistStore(tmp)

    try:
        print("\nname handling")
        check("slashes neutralised", "/" not in safe_filename("AC/DC Mix"),
              safe_filename("AC/DC Mix"))
        check("unicode preserved", safe_filename("神楽坂の夜") == "神楽坂の夜",
              safe_filename("神楽坂の夜"))
        check("empty name falls back", safe_filename("   ") == "untitled")

        print("\ncreate and list")
        store.save("Friday Night", tracks[:4])
        store.save("Quiet Sunday", tracks[4:])
        infos = store.list()
        check("two playlists listed", len(infos) == 2,
              ", ".join(i.name for i in infos))
        check("track counts correct",
              sorted(i.track_count for i in infos) == [4, 4],
              str(sorted(i.track_count for i in infos)))

        print("\nload")
        loaded = store.load("Friday Night", lib)
        check("loads the right number", len(loaded) == 4, str(len(loaded)))
        check("paths round-trip",
              [t.path for t in loaded] == [t.path for t in tracks[:4]])

        print("\nadd to existing (Apple Music style)")
        existing = store.load("Friday Night", lib)
        store.save("Friday Night", existing + tracks[4:6])
        check("grew to six", store.load("Friday Night", lib).__len__() == 6,
              str(len(store.load("Friday Night", lib))))

        print("\nrename and delete")
        check("rename works", store.rename("Quiet Sunday", "Sunday Morning"))
        check("old name gone", not store.exists("Quiet Sunday"))
        check("new name present", store.exists("Sunday Morning"))
        check("rename onto existing refused",
              not store.rename("Sunday Morning", "Friday Night"))
        check("delete works", store.delete("Sunday Morning"))
        check("deleted is gone", not store.exists("Sunday Morning"))

        print("\nm3u is portable")
        text = (tmp / "Friday Night.m3u8").read_text(encoding="utf-8")
        check("has M3U header", text.startswith("#EXTM3U"))
        check("has EXTINF lines", "#EXTINF:" in text)
        check("paths are absolute",
              all(line.startswith("/") for line in text.splitlines()
                  if line and not line.startswith("#")))

        print("\nvia the application")
        cfg.web.enabled = cfg.mpris.enabled = False
        cfg.visualiser.enabled = cfg.watcher.enabled = False
        cfg.playback.resume = False
        from djskippy.app import DJSkippy, View

        app = DJSkippy(cfg)
        app.playlists = store
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("1", "l")      # into albums
            await pilot.press("a")           # append album to playlist
            check("playlist populated", len(app.player.playlist) > 0,
                  f"{len(app.player.playlist)} tracks")

            await pilot.press("S")           # save prompt
            check("save prompt opened", app.mode == "command")
            for ch in "MyMix":
                await pilot.press(ch)
            await pilot.press("enter")
            check("playlist saved as MyMix", store.exists("MyMix"))

            await pilot.press("L")           # picker
            check("picker showing", app._picking_playlist is True)
            check("picker lists playlists", len(app._panes[2].items) >= 2,
                  f"{len(app._panes[2].items)} rows")

            await pilot.press("1")           # back to library
            check("picker mode cleared", app._picking_playlist is False)

            await pilot.press("p")           # add-to prompt
            check("addto prompt opened", app.mode == "command")
            for ch in "Later":
                await pilot.press(ch)
            await pilot.press("enter")
            check("new playlist created by p", store.exists("Later"))

        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        print()
        if FAILURES:
            print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        else:
            print("all checks passed")


if __name__ == "__main__":
    code = asyncio.run(main())
    raise SystemExit(1 if FAILURES else (code or 0))
