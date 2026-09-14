"""Soak test: does it stay healthy over a long session?

A music player runs for hours. Leaks and slow drift do not show up in a test
that starts the application, presses a key and exits — they show up after a
thousand interactions. This drives the real application hard and watches the
things that grow.

    ./.venv/bin/python tests/soak_test.py [--rounds N]
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import os
import resource
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


def rss_mb() -> float:
    """Resident memory, in MB."""
    try:
        with open(f"/proc/{os.getpid()}/status") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


async def main(rounds: int) -> int:
    from djskippy.app import DJSkippy
    from djskippy.config import Config

    cfg = Config.load()
    cfg.web.enabled = False
    cfg.mpris.enabled = False
    cfg.watcher.enabled = False
    cfg.visualiser.enabled = False
    cfg.playback.resume = False
    cfg.playback.volume = 5

    app = DJSkippy(cfg)
    print(f"\nlibrary: {app.library.track_count} tracks "
          f"({app.library.backend})")

    # A cycle through everything a user touches, repeated.
    cycle = [
        "1", "j", "j", "l", "j", "l", "j", "k", "h", "h",
        "2", "3", "4", "j", "h", "5", "6", "7", "1",
        "G", "g", "g", "ctrl+d", "ctrl+u",
        "s", "s", "r", "r", "r", "m", "m", "plus", "minus",
        "a", "e", "l", "l", "e",
    ]

    async with app.run_test(size=(180, 50)) as pilot:
        await asyncio.sleep(1)
        gc.collect()
        start_rss = rss_mb()
        start_objects = len(gc.get_objects())
        print(f"baseline: {start_rss:.1f} MB, {start_objects} objects")

        warm_rss = None
        for round_no in range(1, rounds + 1):
            for key in cycle:
                await pilot.press(key)
            # Playback churn: start something, skip, stop.
            await pilot.press("1", "l", "l", "enter")
            await asyncio.sleep(0.3)
            app.player.next()
            await asyncio.sleep(0.2)
            app.player.stop()
            # Playlist churn.
            app.player.set_playlist([], 0)
            app.player.clear_queue()

            if round_no == 5:
                # Baseline *after* warm-up. Measuring from a cold start
                # counts one-time allocation - mpv's decoder buffers, the
                # widget tree - as though it were a leak.
                gc.collect()
                warm_rss = rss_mb()
            if round_no % 5 == 0:
                gc.collect()
                print(f"  round {round_no:3d}: {rss_mb():7.1f} MB, "
                      f"{len(gc.get_objects())} objects, "
                      f"playlist={len(app.player.playlist)} "
                      f"queue={len(app.player.queue)} "
                      f"mpv_entries={len(app.player._mpv_entries)}",
                      flush=True)

        gc.collect()
        await asyncio.sleep(0.5)
        end_rss = rss_mb()
        end_objects = len(gc.get_objects())

        print(f"\nafter {rounds} rounds ({rounds * len(cycle)} keypresses)")
        # What matters is the slope once warm, not the cold-start cost.
        warm = warm_rss if warm_rss is not None else start_rss
        growth = end_rss - warm
        check("memory is flat once warmed up", growth < 15,
              f"cold {start_rss:.1f} -> warm {warm:.1f} -> "
              f"end {end_rss:.1f} MB (+{growth:.1f} while running)")
        object_growth = end_objects - start_objects
        check("object count stayed bounded", object_growth < 60_000,
              f"{start_objects} -> {end_objects} (+{object_growth})")
        check("mpv playlist bounded",
              len(app.player._mpv_entries) <= 10,
              str(len(app.player._mpv_entries)))
        check("review queue did not accumulate rubbish",
              len(app.review_queue) < 200, str(len(app.review_queue)))
        check("still responsive", app.view is not None)
        check("library intact", app.library.track_count > 0,
              str(app.library.track_count))
        # Match on the executable name, not the command line. Anything
        # that searches command lines - pgrep -f included - also matches the
        # shell running the search, because the pattern is in its arguments.
        strays = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if (entry / "comm").read_text().strip() != "cava":
                    continue
                cmdline = (entry / "cmdline").read_bytes().decode(errors="ignore")
            except OSError:
                continue
            if "dj-skippy-cava" in cmdline:
                strays.append(entry.name)
        check("no stray cava processes", not strays, ",".join(strays))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=40)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.rounds)))
