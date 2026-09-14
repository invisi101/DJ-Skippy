"""Bulk import tests.

Checks that DJ-Skippy can find everything on disk that beets has not taken in,
and that the confidence gate routes weak matches to Review rather than applying
them. Uses the real music folder read-only - it never imports anything.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from djskippy.config import Config  # noqa: E402
from djskippy.library import Library  # noqa: E402
from djskippy.maintenance import (  # noqa: E402
    BeetsCommand,
    find_unimported_albums,
    scan_album_folders,
)
from djskippy.tagger import Candidate, TaggerRequest  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


async def main() -> int:
    cfg = Config.load()
    lib = Library(cfg.library.music_dir, cfg.library.smart_artist_sort)

    print("\nscanning the music folder")
    folders = scan_album_folders(cfg.library.music_dir)
    total_on_disk = sum(f.audio_count for f in folders)
    check("album folders found", len(folders) > 0, f"{len(folders)} folders")
    check("tracks counted", total_on_disk > 0, f"{total_on_disk} tracks on disk")

    print("\nworking out what is missing from the library")
    missing = find_unimported_albums(cfg.library.music_dir, lib)
    missing_tracks = sum(f.audio_count for f in missing)
    check("unimported albums identified", len(missing) > 0,
          f"{len(missing)} albums, {missing_tracks} tracks")
    check("library + missing accounts for the disk",
          lib.track_count + missing_tracks >= total_on_disk * 0.9,
          f"{lib.track_count} tagged + {missing_tracks} missing vs {total_on_disk}")
    check("fully-imported albums excluded",
          all(not f.fully_imported for f in missing))

    print("\nbeets operations are wired")
    for name in ("duplicates", "missing", "mbsync", "fetchart", "stats"):
        check(f"{name} defined", name in BeetsCommand.OPERATIONS)

    print("\nconfidence gate")
    cfg.web.enabled = cfg.mpris.enabled = False
    cfg.visualiser.enabled = cfg.watcher.enabled = False
    cfg.playback.resume = False
    from djskippy.app import DJSkippy, View

    app = DJSkippy(cfg)

    def make_request(similarity: float) -> TaggerRequest:
        return TaggerRequest(
            path="/home/neil/Music/Test/Album",
            item_count=10,
            candidates=[Candidate(
                album="Album", artist="Artist",
                distance=1.0 - similarity / 100.0,
                url="", info_line="",
            )],
        )

    async with app.run_test(size=(160, 45)) as pilot:
        answers: list = []
        app.tagger.respond = answers.append   # capture instead of answering
        app._auto_tagging = True

        app._auto_answer(make_request(96.0))
        check("96% is applied unattended", answers[-1] == "apply", str(answers[-1]))

        before = len(app.review_queue)
        app._auto_answer(make_request(72.0))
        check("72% is skipped", answers[-1] == "skip", str(answers[-1]))
        check("72% goes to review", len(app.review_queue) > before,
              f"{len(app.review_queue)} queued")

        dup = TaggerRequest(path="/home/neil/Music/Test/Dup", item_count=5,
                            candidates=[], is_duplicate=True,
                            duplicate_info="Existing Album")
        app._auto_answer(dup)
        check("duplicates are kept, never replaced", answers[-1] == "keep",
              str(answers[-1]))

        none = TaggerRequest(path="/home/neil/Music/Test/None",
                             item_count=3, candidates=[])
        app._auto_answer(none)
        check("no-match is skipped not guessed", answers[-1] == "skip",
              str(answers[-1]))

        print("\nimport view")
        await pilot.press("6")
        check("view 6 is Import", app.view is View.IMPORT)
        check("import view lists work", len(app._panes[2].items) > 5,
              f"{len(app._panes[2].items)} rows")
        check("unimported albums shown", len(app._unimported) > 0,
              f"{len(app._unimported)} albums")
        await pilot.press("7")
        check("view 7 is Help", app.view is View.HELP)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
