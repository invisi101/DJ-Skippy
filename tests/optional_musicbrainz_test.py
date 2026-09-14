"""MusicBrainz is optional, and the interface says what came from where.

DJ-Skippy is a music player first. Everything in the music folder is playable
whether or not it has been tagged, the library shows all of it, and each entry
records whether its metadata came from MusicBrainz or from the file itself.
With MusicBrainz switched off entirely, nothing is looked up, imported, or
kept in a database — and the player is otherwise unchanged.
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

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


async def main() -> int:
    cfg = Config.load()
    music = cfg.library.music_dir

    print("\neverything on disk is in the library")
    from djskippy.library import AUDIO_SUFFIXES

    on_disk = sum(
        1 for p in music.rglob("*")
        if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
    )
    full = Library(music, True)
    check("every file is present", full.track_count == on_disk,
          f"{full.track_count} of {on_disk}")
    check("tagged and untagged add up",
          full.tagged_count + full.untagged_count == full.track_count,
          f"{full.tagged_count} + {full.untagged_count}")
    check("untagged tracks are included", full.untagged_count > 0,
          str(full.untagged_count))

    print("\nprovenance is recorded per track")
    tagged = [t for t in full.all_tracks if t.tagged]
    untagged = [t for t in full.all_tracks if not t.tagged]
    check("some tracks are marked as tagged", len(tagged) > 0, str(len(tagged)))
    check("some are marked as untagged", len(untagged) > 0, str(len(untagged)))
    check("artist state is reported",
          full.tagged_state(full.artists()[0]) in ("all", "none", "some"))
    states = {full.tagged_state(a) for a in full.artists()}
    check("a mixed library reports more than one state", len(states) > 1,
          str(sorted(states)))

    print("\nloading in two stages keeps startup quick")
    quick = Library(music, True, scan_disk=False)
    quick_count = quick.track_count
    added = quick.merge_from_disk()
    check("the tagged set loads first", quick_count < full.track_count,
          f"{quick_count} then +{added}")
    check("and the totals match a full load",
          quick.track_count == full.track_count,
          f"{quick.track_count} vs {full.track_count}")

    print("\nwith MusicBrainz switched off")
    plain = Library(music, True, use_beets=False)
    check("the same music is playable", plain.track_count == on_disk,
          f"{plain.track_count} of {on_disk}")
    check("nothing claims to be tagged", plain.tagged_count == 0)
    check("the backend says so", plain.backend == "disk", plain.backend)
    check("artists still resolve", len(plain.artists()) > 0,
          str(len(plain.artists())))

    print("\nthe application in both modes")
    from djskippy.app import DJSkippy, View

    for enabled in (True, False):
        cfg = Config.load()
        cfg.library.musicbrainz = enabled
        cfg.web.enabled = cfg.mpris.enabled = False
        cfg.visualiser.enabled = cfg.watcher.enabled = False
        cfg.playback.resume = False
        app = DJSkippy(cfg)
        label = "on" if enabled else "off"
        async with app.run_test(size=(180, 45)) as pilot:
            if enabled:
                for _ in range(40):
                    await asyncio.sleep(0.25)
                    if app.library.untagged_count:
                        break
            check(f"[{label}] library is complete",
                  app.library.track_count == on_disk,
                  f"{app.library.track_count} of {on_disk}")
            check(f"[{label}] artists listed",
                  len(app._panes[0].items) > 100,
                  str(len(app._panes[0].items)))
            check(f"[{label}] status describes the library",
                  bool(app._status.services), app._status.services)

            await pilot.press("6")
            text = "\n".join(app._panes[2].items)
            if enabled:
                check("[on] import view offers work",
                      "import all" in text or "Everything on disk" in text)
            else:
                check("[off] import view explains it is disabled",
                      "MusicBrainz is switched off" in text)
                before = len(app.review_queue)
                app._import_everything()
                check("[off] importing is refused",
                      not app.tagger.running
                      and len(app.review_queue) == before,
                      app._status.message)

            # Playback must work identically either way.
            await pilot.press("1", "l", "l", "enter")
            await asyncio.sleep(2.5)
            check(f"[{label}] plays music",
                  app.player.state.track is not None
                  and app.player.state.position > 0.2,
                  f"{app.player.state.position:.1f}s")
            app.player.stop()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    code = asyncio.run(main())
    raise SystemExit(1 if FAILURES else (code or 0))
