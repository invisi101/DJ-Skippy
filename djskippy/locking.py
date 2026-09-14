"""A lock so only one thing writes to the beets library at a time.

Two beets processes on the same SQLite database is how a library gets
corrupted, and it has nearly happened twice here: once from a stray
background import, once from a second import started while the first was
still running. The in-application guard covers one instance; this covers
two terminals, a headless import, and anything else.

The lock is advisory and self-healing: it records the holder's pid, and a
stale lock left by a process that died is taken over rather than blocking
forever.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

LOCK_DIR = Path(
    os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
) / "dj-skippy"
LOCK_FILE = LOCK_DIR / "import.lock"


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists, owned by someone else.
        return True


def read_holder() -> dict | None:
    """Who holds the lock, or None if nobody does."""
    try:
        data = json.loads(LOCK_FILE.read_text())
    except (OSError, ValueError):
        return None
    pid = int(data.get("pid", 0))
    if not pid or not _process_alive(pid):
        return None
    return data


def describe_holder() -> str:
    holder = read_holder()
    if holder is None:
        return ""
    age = max(0, int(time.time() - float(holder.get("since", time.time()))))
    minutes, seconds = divmod(age, 60)
    when = f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"
    return f"{holder.get('what', 'an import')} (pid {holder.get('pid')}, {when})"


def acquire(what: str = "import") -> bool:
    """Take the lock if it is free. Never blocks."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    if read_holder() is not None:
        return False

    payload = json.dumps(
        {"pid": os.getpid(), "what": what, "since": time.time()}
    )
    try:
        # O_EXCL so two processes racing cannot both believe they won.
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        # Either a live holder we raced with, or a stale file. read_holder
        # said it was free, so replace it.
        try:
            LOCK_FILE.unlink()
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except OSError:
            return False
    except OSError:
        return False

    try:
        os.write(fd, payload.encode())
    finally:
        os.close(fd)
    return True


def release() -> None:
    """Give up the lock, if it is ours."""
    try:
        data = json.loads(LOCK_FILE.read_text())
        if int(data.get("pid", 0)) != os.getpid():
            return
    except (OSError, ValueError):
        return
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass


@contextmanager
def held(what: str = "import") -> Iterator[bool]:
    """Run a block holding the lock. Yields False if it could not be taken."""
    got = acquire(what)
    try:
        yield got
    finally:
        if got:
            release()
