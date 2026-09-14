"""Playback progression.

The end-file event carries an *integer* reason, not a string. Matching it as
text silently matched nothing, so tracks never advanced at their natural end
and an unplayable file stalled the playlist permanently. Both are covered
here, because neither was visible in a test that only checked the first track
started.
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
from djskippy.player import Player  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


def broken(folder: Path, name: str) -> Track:
    path = folder / name
    path.write_bytes(b"this is not audio")
    return Track(id=-1, path=str(path), title=name, artist="", albumartist="",
                 album="", track_no=1, disc_no=0, length=0.0, year=0,
                 genre="", format="FLAC")


async def main() -> int:
    cfg = Config.load()
    lib = Library(cfg.library.music_dir, cfg.library.smart_artist_sort)
    real = [t for t in lib.all_tracks if t.exists][:3]
    if len(real) < 3:
        print("need three playable tracks")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="dj-skippy-playback-"))
    try:
        errors: list[str] = []
        player = Player(volume=12, on_error=errors.append)

        print("\nadvancing at the end of a track")
        player.set_playlist([real[0], real[1]], 0)
        await asyncio.sleep(2)
        first = player.state.track.path if player.state.track else None
        # Jump to just before the end rather than waiting out the track.
        player.seek_to(max(0.0, (player.state.duration or 30) - 3))
        await asyncio.sleep(9)
        second = player.state.track.path if player.state.track else None
        check("moved to the next track by itself",
              second is not None and second != first,
              f"{Path(first).name if first else None} -> "
              f"{Path(second).name if second else None}")

        print("\nskipping what cannot be played")
        player.stop()
        errors.clear()
        player.set_playlist([broken(tmp, "corrupt.flac"), real[2]], 0)
        await asyncio.sleep(7)
        playing = player.state.track
        check("skipped the corrupt file",
              playing is not None and playing.path == real[2].path,
              playing.title if playing else "nothing")
        check("said so", any("cannot play" in e for e in errors),
              errors[0] if errors else "")
        check("remembered what failed", len(player.failed_paths) == 1,
              str(player.failed_paths))

        print("\na missing file behaves the same")
        player.stop()
        errors.clear()
        gone = Track(id=-1, path=str(tmp / "never-existed.flac"), title="Gone",
                     artist="", albumartist="", album="", track_no=1,
                     disc_no=0, length=0.0, year=0, genre="", format="FLAC")
        player.set_playlist([gone, real[0]], 0)
        await asyncio.sleep(6)
        playing = player.state.track
        check("skipped the missing file",
              playing is not None and playing.path == real[0].path,
              playing.title if playing else "nothing")

        print("\nan all-broken playlist stops instead of spinning")
        player.stop()
        errors.clear()
        player.set_playlist([broken(tmp, f"b{i}.flac") for i in range(4)], 0)
        await asyncio.sleep(6)
        check("stopped", player.state.track is None)
        check("did not loop forever", len(errors) <= 8, f"{len(errors)} errors")

        player.shutdown()
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
