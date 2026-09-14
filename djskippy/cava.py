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
import shutil
import tempfile
from pathlib import Path

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
        for method in ("pipewire", "pulse"):
            self._config_path = self._write_config(method)
            try:
                self._process = await asyncio.create_subprocess_exec(
                    "cava", "-p", str(self._config_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except Exception as exc:
                self.error = str(exc)
                continue

            # Give it a moment to fail on a bad input method.
            await asyncio.sleep(0.4)
            if self._process.returncode is None:
                self.running = True
                self.error = None
                self._task = asyncio.create_task(self._read_loop())
                return True

            self._cleanup_config()

        self.error = "cava could not capture audio"
        return False

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
        except Exception:
            pass
        finally:
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
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
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
