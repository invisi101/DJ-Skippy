"""Watcher tests.

Checks the two behaviours that make auto-import safe rather than merely
automatic: it waits for a copy to finish before importing, and it ignores
directories it has already seen.

Uses a temporary folder and real files - no mocking of the filesystem, because
the thing under test *is* filesystem behaviour.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from djskippy.watcher import MusicWatcher  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="dj-skippy-watch-"))
    ready: list[Path] = []
    known: set[str] = set()

    watcher = MusicWatcher(
        root,
        known_paths=lambda: known,
        on_ready=ready.append,
        on_status=lambda m: None,
        settle_seconds=3.0,   # short, so the test is quick
    )

    print("\nstartup")
    check("watcher starts", watcher.start(), watcher.error or "")
    if not watcher.running:
        return 1

    try:
        print("\nnew album detection")
        album = root / "Some Artist - Some Album"
        album.mkdir()
        # Simulate a copy landing over several seconds.
        for i in range(1, 4):
            (album / f"{i:02d} Track.flac").write_bytes(b"fLaC" + b"\0" * 64)
            time.sleep(0.8)

        check("album is pending while copying", len(watcher.pending()) == 1,
              f"{len(watcher.pending())} pending")
        check("not imported mid-copy", len(ready) == 0,
              f"{len(ready)} imported")

        # Now let it settle.
        deadline = time.time() + 12
        while time.time() < deadline and not ready:
            time.sleep(0.5)

        check("imported after settling", len(ready) == 1,
              ready[0].name if ready else "none")
        check("correct directory reported",
              bool(ready) and ready[0] == album,
              str(ready[0]) if ready else "")

        print("\nalready-known albums are ignored")
        ready.clear()
        known.update(str(p) for p in album.glob("*.flac"))
        (album / "04 Track.flac").write_bytes(b"fLaC" + b"\0" * 64)
        known.add(str(album / "04 Track.flac"))
        time.sleep(5.0)
        check("known album not re-imported", len(ready) == 0,
              f"{len(ready)} imported")

        print("\nnon-audio folders are ignored")
        ready.clear()
        junk = root / "Not An Album"
        junk.mkdir()
        (junk / "notes.txt").write_text("hello")
        time.sleep(5.0)
        check("folder without audio ignored", len(ready) == 0,
              f"{len(ready)} imported")

    finally:
        watcher.stop()
        shutil.rmtree(root, ignore_errors=True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
