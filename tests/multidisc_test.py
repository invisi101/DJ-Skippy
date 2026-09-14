"""Multi-disc grouping.

Handing beets a single disc of a set is actively harmful: it matches that disc
against the *complete* release, so half the tracks look missing, the score
lands near 70%, and the album either ends up in review or is imported as two
unrelated albums.

These tests build the awkward folder shapes that actually exist in a real
library and check each is offered to beets as one album.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from djskippy.maintenance import (  # noqa: E402
    audio_files_for,
    is_disc_folder,
    scan_album_folders,
)

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


def make(root: Path, rel: str, tracks: int) -> Path:
    folder = root / rel
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(1, tracks + 1):
        (folder / f"{i:02d} Track.flac").write_bytes(b"fLaC" + b"\0" * 32)
    return folder


def units(root: Path) -> dict[str, int]:
    return {
        str(f.path.relative_to(root)): f.audio_count
        for f in scan_album_folders(root)
    }


def main() -> int:
    print("\nname recognition")
    for name in ("CD1", "CD 2", "Disc 3", "disk4", "Vol. 2", "CD 2 (320)",
                 "CD 1 [L]", "CD2 - Live In Madrid", "Disc 1 — Bonus", "2"):
        check(f"{name!r} reads as a disc", is_disc_folder(Path(name)))
    for name in ("American Beauty", "Live-Dead", "Grace", "1999", "OK Computer",
                 "Discovery", "Volume Control"):
        check(f"{name!r} reads as an album", not is_disc_folder(Path(name)))

    root = Path(tempfile.mkdtemp(prefix="dj-skippy-md-"))
    try:
        # An ordinary album.
        make(root, "Artist/Normal Album", 10)
        # A plain two-disc set.
        make(root, "Artist/Two Disc Set/CD1", 12)
        make(root, "Artist/Two Disc Set/CD2", 11)
        # Discs with descriptive suffixes.
        make(root, "Artist/Deluxe Reissue/CD1", 11)
        make(root, "Artist/Deluxe Reissue/CD2 - Live In Madrid", 16)
        # Nested: Disc 1 holds its own tracks *and* the other discs.
        make(root, "Artist/Nested Mess/Disc 1", 13)
        make(root, "Artist/Nested Mess/Disc 1/Disc 2", 13)
        make(root, "Artist/Nested Mess/Disc 1/Disc 3", 14)
        # Discs directly under an artist, with no album folder.
        make(root, "Bare Artist/CD1", 20)
        make(root, "Bare Artist/CD2", 21)
        # An album with a bonus disc *and* its own tracks.
        bonus = make(root, "Artist/With Bonus", 9)
        make(root, "Artist/With Bonus/Disc 2", 4)
        # A disc folder loose at the top level, belonging to nothing.
        make(root, "Loose CD1", 5)

        found = units(root)
        print("\ngrouping")
        check("ordinary album kept", found.get("Artist/Normal Album") == 10,
              str(found.get("Artist/Normal Album")))
        check("two-disc set becomes one album",
              found.get("Artist/Two Disc Set") == 23,
              str(found.get("Artist/Two Disc Set")))
        check("descriptive disc names group too",
              found.get("Artist/Deluxe Reissue") == 27,
              str(found.get("Artist/Deluxe Reissue")))
        check("nested discs roll up to the album",
              found.get("Artist/Nested Mess") == 40,
              str(found.get("Artist/Nested Mess")))
        check("discs under a bare artist group",
              found.get("Bare Artist") == 41, str(found.get("Bare Artist")))
        check("album with a bonus disc counts both",
              found.get("Artist/With Bonus") == 13,
              str(found.get("Artist/With Bonus")))
        check("a loose top-level disc stays on its own",
              found.get("Loose CD1") == 5, str(found.get("Loose CD1")))

        print("\nnothing offered as a bare disc")
        stray = [k for k in found if is_disc_folder(Path(k)) and "/" in k]
        check("no disc subfolder offered separately", not stray, str(stray))

        print("\nevery file accounted for, exactly once")
        expected = {
            str(p) for p in root.rglob("*")
            if p.is_file() and p.suffix == ".flac"
        }
        seen: list[str] = []
        for f in scan_album_folders(root):
            seen.extend(audio_files_for(f.path))
        check("no file lost", set(seen) == expected,
              f"{len(set(seen))} of {len(expected)}")
        check("no file counted twice", len(seen) == len(set(seen)),
              f"{len(seen)} vs {len(set(seen))}")
        check("counts match the files",
              sum(f.audio_count for f in scan_album_folders(root)) == len(expected),
              f"{sum(f.audio_count for f in scan_album_folders(root))} vs {len(expected)}")

        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)
        print()
        if FAILURES:
            print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        else:
            print("all checks passed")


if __name__ == "__main__":
    code = main()
    raise SystemExit(1 if FAILURES else (code or 0))
