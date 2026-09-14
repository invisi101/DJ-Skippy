"""Robustness against the mess real filesystems contain.

A music player that crashes on an unreadable folder, a symlink loop or a
zero-byte file is not finished. Every case here is something that exists in
real libraries; the requirement is that none of it raises, hangs, or loses
track of anything valid.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


class Timeout:
    """Guard against a hang, which a test alone cannot detect."""

    def __init__(self, seconds: int) -> None:
        self.seconds = seconds

    def __enter__(self):
        def fire(signum, frame):
            raise TimeoutError(f"exceeded {self.seconds}s")

        self.previous = signal.signal(signal.SIGALRM, fire)
        signal.alarm(self.seconds)
        return self

    def __exit__(self, *exc):
        signal.alarm(0)
        signal.signal(signal.SIGALRM, self.previous)
        return False


def track(folder: Path, name: str, data: bytes = b"fLaC" + b"\0" * 32) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    return path


def main() -> int:
    from djskippy.library import Library
    from djskippy.maintenance import audio_files_for, scan_album_folders

    root = Path(tempfile.mkdtemp(prefix="dj-skippy-rough-"))
    unreadable = root / "Artist" / "No Permission"
    try:
        # Ordinary control case.
        track(root / "Artist" / "Good Album", "01 Fine.flac")

        # Unicode, emoji and awkward punctuation - all present in real
        # libraries (this one has "80s Rock Essentials ⭐️").
        track(root / "Artist" / "Essentials ⭐️", "01 Star.flac")
        track(root / "アーティスト" / "神楽坂の夜", "01 Tokyo.flac")
        track(root / "Artist" / "Album 'with' \"quotes\" & things",
              "01 Punct.flac")
        track(root / "Artist" / ("Very " + "long " * 40).strip(),
              "01 Long.flac")

        # A zero-byte file, and one that is not audio at all despite the name.
        track(root / "Artist" / "Broken", "01 Empty.flac", b"")
        track(root / "Artist" / "Broken", "02 Not Audio.flac", b"this is text")

        # A folder we cannot read.
        track(unreadable, "01 Secret.flac")
        os.chmod(unreadable, 0o000)

        # A symlink loop, which naive recursion follows forever.
        loop = root / "Artist" / "Loop"
        loop.mkdir(parents=True, exist_ok=True)
        track(loop, "01 Round.flac")
        try:
            (loop / "back").symlink_to(root, target_is_directory=True)
        except OSError:
            pass

        # A symlink to a file that no longer exists.
        dangling = root / "Artist" / "Good Album" / "02 Gone.flac"
        try:
            dangling.symlink_to(root / "nowhere.flac")
        except OSError:
            pass

        print("\nscanning a hostile tree")
        with Timeout(60):
            started = time.time()
            folders = scan_album_folders(root)
            elapsed = time.time() - started
        check("scan completes without hanging", elapsed < 60, f"{elapsed:.1f}s")
        check("found the ordinary album",
              any(f.path.name == "Good Album" for f in folders))
        check("handled emoji folder names",
              any("⭐️" in f.path.name for f in folders))
        check("handled Japanese folder names",
              any("神楽坂" in f.path.name for f in folders))
        check("handled quotes and ampersands",
              any("quotes" in f.path.name for f in folders))
        check("did not crash on the unreadable folder", True)

        print("\nreading files from it")
        with Timeout(60):
            for folder in folders:
                audio_files_for(folder.path)
        check("audio_files_for survives every folder", True)

        print("\nbuilding a library from it")
        with Timeout(120):
            lib = Library(root, True)
        check("library builds", lib.backend in ("filesystem", "empty"),
              f"{lib.backend}, {lib.track_count} tracks")
        check("zero-byte file did not crash the scan", True)
        for t in lib.all_tracks:
            check_name = t.title and isinstance(t.title, str)
            if not check_name:
                break
        check("every track has a usable title",
              all(t.title for t in lib.all_tracks))
        check("every track has a path",
              all(t.path for t in lib.all_tracks))

        print("\nuntagged files fall back to the folder structure")
        plain = Path(tempfile.mkdtemp(prefix="dj-skippy-plain-"))
        try:
            track(plain / "Pink Floyd" / "The Wall", "01 In The Flesh.flac")
            track(plain / "Pink Floyd" / "The Wall" / "CD2", "01 Hey You.flac")
            track(plain / "Loose Album", "01 Track.flac")
            with Timeout(60):
                plain_lib = Library(plain, True)
            by_path = {Path(t.path).name: t for t in plain_lib.all_tracks}
            flesh = by_path.get("01 In The Flesh.flac")
            check("artist read from the folder above the album",
                  flesh is not None and flesh.artist == "Pink Floyd",
                  flesh.artist if flesh else "missing")
            check("album read from its own folder",
                  flesh is not None and flesh.album == "The Wall",
                  flesh.album if flesh else "missing")
            hey = by_path.get("01 Hey You.flac")
            check("a disc folder does not become the album name",
                  hey is not None and hey.album == "The Wall",
                  hey.album if hey else "missing")
            loose = by_path.get("01 Track.flac")
            check("a single folder level still names the album",
                  loose is not None and loose.album == "Loose Album",
                  loose.album if loose else "missing")
        finally:
            shutil.rmtree(plain, ignore_errors=True)

        print("\nthe beets database is not used for other folders")
        elsewhere = Path(tempfile.mkdtemp(prefix="dj-skippy-elsewhere-"))
        try:
            track(elsewhere / "Someone" / "Something", "01 Track.flac")
            with Timeout(60):
                other = Library(elsewhere, True)
            check("a different folder is scanned, not the beets library",
                  other.backend == "filesystem" and other.track_count == 1,
                  f"{other.backend}, {other.track_count} tracks")
            check("and it says why", bool(other.error), other.error or "")
        finally:
            shutil.rmtree(elsewhere, ignore_errors=True)

        print("\nan empty music folder")
        empty = Path(tempfile.mkdtemp(prefix="dj-skippy-empty-"))
        try:
            with Timeout(30):
                empty_lib = Library(empty, True)
                empty_folders = scan_album_folders(empty)
            check("empty folder gives an empty library",
                  empty_lib.track_count == 0 and empty_folders == [],
                  f"{empty_lib.track_count} tracks, {len(empty_folders)} folders")
            check("artists list is empty, not broken",
                  empty_lib.artists() == [])
        finally:
            shutil.rmtree(empty, ignore_errors=True)

        print("\na music folder that does not exist")
        with Timeout(30):
            missing = Library(Path("/nonexistent/path/xyz"), True)
            missing_folders = scan_album_folders(Path("/nonexistent/path/xyz"))
        check("missing folder handled", missing.track_count == 0
              and missing_folders == [])

        print("\nthe app starts against a hostile library")

        async def boot() -> tuple[bool, str]:
            from djskippy.app import DJSkippy
            from djskippy.config import Config

            cfg = Config.load()
            cfg.library.music_dir = root
            cfg.web.enabled = cfg.mpris.enabled = False
            cfg.visualiser.enabled = cfg.watcher.enabled = False
            cfg.playback.resume = False
            app = DJSkippy(cfg)
            async with app.run_test(size=(120, 40)) as pilot:
                for key in ("1", "l", "l", "j", "k", "2", "3", "4", "5",
                            "6", "7", "1", "G", "g", "g"):
                    await pilot.press(key)
                return True, f"{app.library.track_count} tracks"

        with Timeout(180):
            ok, detail = asyncio.run(boot())
        check("app boots and navigates without raising", ok, detail)

        return 0
    finally:
        try:
            os.chmod(unreadable, 0o755)
        except OSError:
            pass
        shutil.rmtree(root, ignore_errors=True)
        print()
        if FAILURES:
            print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        else:
            print("all checks passed")


if __name__ == "__main__":
    code = main()
    raise SystemExit(1 if FAILURES else (code or 0))
