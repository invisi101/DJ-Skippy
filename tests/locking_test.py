"""The import lock.

Two beets processes on one SQLite database is how a library gets corrupted,
and it nearly happened twice during development — once from a stray background
import, once from a second import started while the first was running. The
in-application guard only covers a single instance; this lock covers two
terminals, a headless run, and anything else.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from djskippy import locking  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


def main() -> int:
    # Never disturb a real import that happens to be running.
    existing = locking.read_holder()
    if existing is not None:
        print(f"  an import is running ({locking.describe_holder()}); skipping")
        return 0

    try:
        print("\ntaking and releasing")
        check("lock is free to start with", locking.read_holder() is None)
        check("acquired", locking.acquire("import") is True)
        check("holder is us",
              (locking.read_holder() or {}).get("pid") == os.getpid())
        check("describes itself", "import" in locking.describe_holder(),
              locking.describe_holder())

        print("\nit excludes")
        check("a second acquire fails", locking.acquire("fetchart") is False)
        locking.release()
        check("released", locking.read_holder() is None)
        check("free again after release", locking.acquire("import") is True)
        locking.release()

        print("\na stale lock does not block forever")
        # A pid that cannot be running: write it directly.
        locking.LOCK_DIR.mkdir(parents=True, exist_ok=True)
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                              capture_output=True, text=True).stdout.strip()
        locking.LOCK_FILE.write_text(
            json.dumps({"pid": int(dead), "what": "import",
                        "since": time.time() - 300})
        )
        check("a dead holder reads as free", locking.read_holder() is None,
              f"pid {dead}")
        check("and the lock can be taken over", locking.acquire("import") is True)
        locking.release()

        print("\na corrupt lock file is survivable")
        locking.LOCK_FILE.write_text("this is not json")
        check("garbage reads as free", locking.read_holder() is None)
        check("and is replaced", locking.acquire("import") is True)
        locking.release()

        print("\nthe context manager always releases")
        with locking.held("import") as got:
            check("held inside the block", got is True)
            check("and visible to others", locking.acquire("other") is False)
        check("released on the way out", locking.read_holder() is None)

        try:
            with locking.held("import") as got:
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        check("released even after an exception",
              locking.read_holder() is None)

        print("\na real second import refuses to start")
        from djskippy.tagger import Tagger

        locking.acquire("import")
        try:
            logged: list[str] = []
            tagger = Tagger(on_request=lambda r: None, on_log=logged.append)
            tagger.start(["/home/neil/Music"])
            deadline = time.time() + 10
            while tagger.running and time.time() < deadline:
                time.sleep(0.2)
            check("it declined", not tagger.running)
            check("and said why",
                  bool(tagger.error) and "already running" in tagger.error,
                  tagger.error or "")
        finally:
            locking.release()

        return 0
    finally:
        # Leave nothing behind.
        try:
            data = json.loads(locking.LOCK_FILE.read_text())
            if int(data.get("pid", 0)) == os.getpid():
                locking.LOCK_FILE.unlink()
        except Exception:
            pass
        print()
        if FAILURES:
            print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        else:
            print("all checks passed")


if __name__ == "__main__":
    code = main()
    raise SystemExit(1 if FAILURES else (code or 0))
