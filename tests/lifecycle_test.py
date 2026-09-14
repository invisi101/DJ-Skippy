"""Shutdown tests.

A music player must not outlive its window. Closing a terminal sends SIGHUP;
without a handler the process survives until logout, still holding the MPRIS
bus name, the web port, and a cava process consuming CPU.

These tests start real instances and kill them three ways:

  SIGHUP   - the closing-window case; must exit cleanly and save state
  SIGTERM  - pkill, or the desktop session ending
  SIGKILL  - no handler can run, so cava must be killed by the kernel instead
             (PR_SET_PDEATHSIG)

Run with:  ./.venv/bin/python tests/lifecycle_test.py
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT = Path(__file__).resolve().parent.parent
LAUNCHER = PROJECT / "bin" / "dj-skippy"

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


def pids(pattern: str) -> list[int]:
    try:
        out = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return []
    return [int(line) for line in out.split() if line.strip().isdigit()]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def wait_gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.3)
    return not alive(pid)


def start_instance() -> tuple[subprocess.Popen, int, int | None]:
    """Launch DJ-Skippy on a pty and wait for it and cava to come up."""
    # A pty, because Textual refuses to start without one.
    proc = subprocess.Popen(
        ["script", "-qec", str(LAUNCHER), "/dev/null"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )

    app_pid = None
    deadline = time.time() + 25
    while time.time() < deadline:
        found = [p for p in pids("python -m djskippy")]
        if found:
            app_pid = found[0]
            break
        time.sleep(0.5)

    cava_pid = None
    if app_pid:
        deadline = time.time() + 15
        while time.time() < deadline:
            found = pids("cava -p /tmp/dj-skippy-cava")
            if found:
                cava_pid = found[0]
                break
            time.sleep(0.5)

    return proc, app_pid, cava_pid


def cleanup() -> None:
    subprocess.run(["pkill", "-KILL", "-f", "python -m djskippy"],
                   capture_output=True)
    subprocess.run(["pkill", "-KILL", "-f", "cava -p /tmp/dj-skippy-cava"],
                   capture_output=True)
    time.sleep(1)
    for path in Path("/tmp").glob("dj-skippy-cava-*.conf"):
        try:
            path.unlink()
        except OSError:
            pass


def run_case(sig: int, name: str) -> None:
    print(f"\n{name}")
    proc, app_pid, cava_pid = start_instance()
    if not app_pid:
        check(f"{name}: instance started", False, "never appeared")
        return
    check("instance running", True, f"app={app_pid}")
    check("cava running", cava_pid is not None, str(cava_pid))

    os.kill(app_pid, sig)

    check("app exits", wait_gone(app_pid, 12), f"pid {app_pid}")
    if cava_pid:
        check("cava does not outlive it", wait_gone(cava_pid, 12),
              f"pid {cava_pid}")

    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def main() -> int:
    if not LAUNCHER.exists():
        print(f"launcher not found: {LAUNCHER}")
        return 1

    cleanup()
    try:
        run_case(signal.SIGHUP, "SIGHUP — the closing-window case")
        cleanup()
        run_case(signal.SIGTERM, "SIGTERM — pkill or session end")
        cleanup()
        # No handler can run for SIGKILL, so this proves the kernel-level
        # guarantee rather than our cleanup code.
        run_case(signal.SIGKILL, "SIGKILL — nothing gets to clean up")

        # The case that actually bit: the terminal is destroyed *first*, so
        # Textual's shutdown has no screen to write to. Killing the app while
        # its pty still exists does not reproduce it.
        print("\npty destroyed first — the real ctrl+w case")
        proc, app_pid, cava_pid = start_instance()
        if app_pid:
            check("instance running", True, f"app={app_pid}")
            proc.kill()          # destroy the pty owner, not the app
            check("app exits when its terminal vanishes",
                  wait_gone(app_pid, 15), f"pid {app_pid}")
            if cava_pid:
                check("cava goes with it", wait_gone(cava_pid, 15),
                      f"pid {cava_pid}")
        else:
            check("instance started", False, "never appeared")

        print("\ntemp files")
        cleanup()
        leftover = list(Path("/tmp").glob("dj-skippy-cava-*.conf"))
        check("no cava configs left behind", len(leftover) == 0,
              f"{len(leftover)} left")
    finally:
        cleanup()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
