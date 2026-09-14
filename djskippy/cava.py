"""Cava visualiser integration.

Rather than linking against cava, we run it with `output.method = raw` and read
its ASCII stream: one line per frame, N semicolon-separated values scaled
0-100. That keeps us on the packaged cava binary and lets us draw the bars as
native Textual content instead of trying to embed another TUI.

Cava listens to the *output* device, so it visualises whatever is audible -
including audio from other applications. That is a feature, not a bug.
"""

from __future__ import annotations

import asyncio
import atexit
import ctypes
import os
import shutil
import signal
import tempfile
import time
from pathlib import Path

#: Every cava process we have started, so they can be cleaned up even on an
#: abrupt exit. See `_kill_stragglers`.
_LIVE_PROCESSES: set[int] = set()


def _die_with_parent() -> None:
    """Ask the kernel to SIGTERM this child when its parent dies.

    Linux's PR_SET_PDEATHSIG. Without it, killing DJ-Skippy in a way that
    skips its cleanup - SIGKILL, a crash, a terminal that vanishes - leaves
    cava running and consuming CPU until logout. This makes the guarantee
    kernel-level rather than best-effort.
    """
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_PDEATHSIG = 1
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except Exception:
        pass


def _kill_stragglers() -> None:
    """Backstop at interpreter exit: terminate any cava we started."""
    for pid in list(_LIVE_PROCESSES):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        _LIVE_PROCESSES.discard(pid)


atexit.register(_kill_stragglers)


def clean_stale_configs(max_age_seconds: float = 3600.0) -> int:
    """Remove leftover cava configs from previous runs.

    Returns how many were removed. Only touches files old enough that no
    running instance could still be using them.
    """
    removed = 0
    now = time.time()
    try:
        for path in Path(tempfile.gettempdir()).glob("dj-skippy-cava-*.conf"):
            try:
                if now - path.stat().st_mtime > max_age_seconds:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    except OSError:
        pass
    return removed

# Eighth-block characters give eight sub-rows of vertical resolution per cell.
BLOCKS = " ▁▂▃▄▅▆▇█"

CONFIG_TEMPLATE = """\
[general]
framerate = {framerate}
bars = {bars}
autosens = 1
overshoot = 0

[input]
method = {method}
source = auto

[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = {max_range}

[smoothing]
noise_reduction = 30
"""

MAX_RANGE = 100


class CavaVisualiser:
    """Runs cava and exposes the latest frame as a list of 0.0-1.0 levels."""

    def __init__(self, bars: int = 40, framerate: int = 60) -> None:
        self.bars = max(4, bars)
        self.framerate = framerate
        self.levels: list[float] = [0.0] * self.bars
        self._process: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
        self._config_path: Path | None = None
        self.running = False
        self.error: str | None = None

    @staticmethod
    def available() -> bool:
        return shutil.which("cava") is not None

    def _write_config(self, method: str) -> Path:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".conf", prefix="dj-skippy-cava-", delete=False
        )
        handle.write(
            CONFIG_TEMPLATE.format(
                framerate=self.framerate,
                bars=self.bars,
                method=method,
                max_range=MAX_RANGE,
            )
        )
        handle.close()
        return Path(handle.name)

    async def start(self) -> bool:
        """Start cava. Returns False (and sets .error) rather than raising -
        a missing visualiser must never stop the music."""
        if self.running:
            return True
        if not self.available():
            self.error = "cava is not installed"
            return False

        # PipeWire first since that is what this system runs; pulse is the
        # compatibility fallback and works through pipewire-pulse too.
        last_error = ""
        for method in ("pipewire", "pulse"):
            self._config_path = self._write_config(method)
            try:
                # Keep stderr: cava explains itself there, and throwing it
                # away left "could not capture audio" as the only clue.
                self._process = await asyncio.create_subprocess_exec(
                    "cava", "-p", str(self._config_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    preexec_fn=_die_with_parent,
                )
                _LIVE_PROCESSES.add(self._process.pid)
            except Exception as exc:
                self.error = str(exc)
                continue

            # Wait for cava to either produce a frame or fail. Checking
            # only that the process still exists was too weak - it can linger
            # briefly before giving up on an input device.
            first_frame = await self._await_first_frame(timeout=3.0)
            if first_frame:
                self.running = True
                self.error = None
                self._task = asyncio.create_task(self._read_loop())
                return True

            complaint = await self._read_stderr()
            if complaint:
                last_error = f"cava ({method}): {complaint}"
            else:
                code = self._process.returncode
                last_error = (
                    f"cava ({method}) exited with code {code}"
                    if code is not None
                    else f"cava ({method}) produced no audio data"
                )
            await self._terminate_process()
            self._cleanup_config()

        self.error = last_error or "cava could not capture audio"
        return False

    async def _await_first_frame(self, timeout: float) -> bool:
        """Wait for one readable frame, so we know capture really works."""
        if self._process is None or self._process.stdout is None:
            return False
        try:
            line = await asyncio.wait_for(
                self._process.stdout.readline(), timeout=timeout
            )
        except (asyncio.TimeoutError, Exception):
            return False
        if not line:
            return False
        raw = line.decode("ascii", "ignore").strip().rstrip(";")
        try:
            values = [int(part) for part in raw.split(";") if part != ""]
        except ValueError:
            return False
        if values:
            self.levels = [min(1.0, v / MAX_RANGE) for v in values]
            return True
        return False

    async def _read_stderr(self) -> str:
        """Whatever cava had to say, trimmed to something displayable."""
        if self._process is None or self._process.stderr is None:
            return ""
        try:
            data = await asyncio.wait_for(self._process.stderr.read(4096), timeout=1.0)
        except Exception:
            return ""
        text = data.decode("utf-8", "replace").strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return lines[-1][:120] if lines else ""

    async def _terminate_process(self) -> None:
        if self._process is None:
            return
        pid = self._process.pid
        try:
            self._process.terminate()
            await asyncio.wait_for(self._process.wait(), timeout=2)
        except Exception:
            try:
                self._process.kill()
            except Exception:
                pass
        _LIVE_PROCESSES.discard(pid)
        self._process = None

    async def _read_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                raw = line.decode("ascii", "ignore").strip().rstrip(";")
                if not raw:
                    continue
                try:
                    values = [int(part) for part in raw.split(";") if part != ""]
                except ValueError:
                    continue
                if values:
                    self.levels = [min(1.0, v / MAX_RANGE) for v in values]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = f"visualiser read failed: {type(exc).__name__}"
        finally:
            if self.running:
                # The loop ended while we still believed cava was running,
                # so the process went away underneath us. Say so rather than
                # silently showing an empty strip.
                code = getattr(self._process, "returncode", None)
                if self.error is None:
                    self.error = (
                        f"cava exited (code {code})" if code is not None
                        else "cava stopped unexpectedly"
                    )
            self.running = False

    async def set_bars(self, bars: int) -> None:
        """Change bar count - needs a restart, cava reads it at startup."""
        bars = max(4, bars)
        if bars == self.bars:
            return
        self.bars = bars
        self.levels = [0.0] * bars
        if self.running:
            await self.stop()
            await self.start()

    async def stop(self) -> None:
        self.running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

        if self._process is not None:
            pid = self._process.pid
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            _LIVE_PROCESSES.discard(pid)
            self._process = None

        self._cleanup_config()
        self.levels = [0.0] * self.bars

    def _cleanup_config(self) -> None:
        if self._config_path is not None:
            try:
                self._config_path.unlink(missing_ok=True)
            except Exception:
                pass
            self._config_path = None

    # -- rendering -------------------------------------------------------

    def render_row(self, width: int) -> str:
        """A single-row bar strip, one eighth-block per column."""
        if not self.levels:
            return " " * width
        return "".join(
            BLOCKS[min(8, max(0, int(level * 8)))]
            for level in self._resample(width)
        )

    def render_rows(self, width: int, height: int) -> list[str]:
        """A multi-row visualiser, drawn bottom-up so bars grow upward."""
        levels = self._resample(width)
        rows: list[str] = []
        for row in range(height, 0, -1):
            line = []
            for level in levels:
                filled = level * height
                if filled >= row:
                    line.append("█")
                elif filled > row - 1:
                    line.append(BLOCKS[min(8, max(0, int((filled - (row - 1)) * 8)))])
                else:
                    line.append(" ")
            rows.append("".join(line))
        return rows

    def _resample(self, width: int) -> list[float]:
        """Stretch or squash cava's bar count to the available columns."""
        if width <= 0:
            return []
        source = self.levels or [0.0]
        if len(source) == width:
            return list(source)
        return [
            source[min(len(source) - 1, int(i * len(source) / width))]
            for i in range(width)
        ]
