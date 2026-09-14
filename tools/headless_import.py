"""Run DJ-Skippy's import without a terminal.

Textual can drive the real application headlessly, so this is the same code
path as pressing 6 then i — the same confidence gate, the same multi-disc
grouping, the same review queue — just without a screen to show it on.

Useful for importing a large library unattended, and for letting the thing be
exercised for hours while nobody is watching.

    ./.venv/bin/python tools/headless_import.py [--minutes N]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def stamp() -> str:
    return time.strftime("%H:%M:%S")


async def main(minutes: float) -> int:
    from djskippy.app import DJSkippy
    from djskippy.config import Config
    from djskippy.library import Library
    from djskippy.maintenance import find_unimported_albums

    cfg = Config.load()
    # No visualiser, no web server, no D-Bus: nothing here needs them, and
    # leaving them off avoids colliding with a real session.
    cfg.visualiser.enabled = False
    cfg.web.enabled = False
    cfg.mpris.enabled = False
    cfg.watcher.enabled = False
    cfg.playback.resume = False

    app = DJSkippy(cfg)
    before = app.library.track_count
    todo = find_unimported_albums(cfg.library.music_dir, app.library)
    print(f"[{stamp()}] library: {before} tracks")
    print(f"[{stamp()}] to import: {len(todo)} albums, "
          f"{sum(f.audio_count for f in todo)} tracks")
    if not todo:
        print(f"[{stamp()}] nothing to do")
        return 0

    deadline = time.time() + minutes * 60
    last_report = 0

    async with app.run_test(size=(200, 50)) as pilot:
        await pilot.press("6")
        app._import_everything()
        await asyncio.sleep(2)

        if not app.tagger.running:
            print(f"[{stamp()}] import did not start: {app._status.message}")
            return 1

        while app.tagger.running and time.time() < deadline:
            await asyncio.sleep(5)
            done = app.tagger.decisions
            if done != last_report:
                last_report = done
                stats = app.tagger.stats
                print(
                    f"[{stamp()}] {done}/{app.tagger.total_paths}  "
                    f"tagged {stats['imported']}  as-is {stats['asis']}  "
                    f"skipped {stats['skipped']}  retried {stats['retried']}  "
                    f"— {app.tagger.current_album[:48]}",
                    flush=True,
                )

        if app.tagger.running:
            print(f"[{stamp()}] time limit reached, stopping cleanly")
            app.tagger.abort()
            await asyncio.sleep(3)

        stats = app.tagger.stats
        print(f"[{stamp()}] finished: tagged {stats['imported']}, "
              f"as-is {stats['asis']}, skipped {stats['skipped']}, "
              f"retried {stats['retried']}")
        print(f"[{stamp()}] review queue: {len(app.review_queue)}")
        for item in app.review_queue[:40]:
            print(f"           {item.kind:<9} {item.display_name}  — {item.reason}")

    after = Library(cfg.library.music_dir, cfg.library.smart_artist_sort)
    print(f"[{stamp()}] library: {before} -> {after.track_count} tracks "
          f"(+{after.track_count - before})")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=60.0,
                        help="stop cleanly after this long")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.minutes)))
