"""Album art rendering for the terminal.

Art is drawn with the half-block trick: each character cell shows U+2580 (upper
half block) with the foreground set to the top pixel's colour and the
background to the bottom pixel's. That doubles vertical resolution and gives a
cell grid of W x 2H pixels in true colour.

Why not the kitty graphics protocol, given kitty is what you run? Because it
writes images directly to the screen at absolute positions, and Textual owns
the screen - the two fight, and the image survives exactly until the next
repaint. Half-blocks are real widget content, so they scroll, clip, and redraw
like everything else.

Art is looked for in this order:
  1. cover/folder/front/album art files next to the audio (what beets fetchart
     downloads)
  2. artwork embedded in the file itself
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from rich.segment import Segment
from rich.style import Style

ART_NAMES = (
    "cover", "folder", "front", "album", "albumart", "albumartsmall", "art",
)
ART_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")

UPPER_HALF = "▀"


def find_art_file(track_path: str) -> Path | None:
    """Look for a cover image alongside the audio file."""
    folder = Path(track_path).parent
    if not folder.is_dir():
        return None

    try:
        entries = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
    except OSError:
        return None

    for name in ART_NAMES:
        for suffix in ART_SUFFIXES:
            hit = entries.get(f"{name}{suffix}")
            if hit is not None:
                return hit

    # Nothing canonically named - take any image in the folder.
    for path in sorted(entries.values()):
        if path.suffix.lower() in ART_SUFFIXES:
            return path
    return None


def extract_embedded_art(track_path: str) -> bytes | None:
    """Pull cover art out of the audio file's own tags."""
    try:
        import mutagen
    except ImportError:
        return None

    try:
        meta = mutagen.File(track_path)
    except Exception:
        return None
    if meta is None:
        return None

    # FLAC and friends
    pictures = getattr(meta, "pictures", None)
    if pictures:
        return pictures[0].data

    tags = getattr(meta, "tags", None)
    if tags is None:
        return None

    # ID3 (MP3)
    try:
        for key in tags.keys():
            if str(key).startswith("APIC"):
                return tags[key].data
    except Exception:
        pass

    # MP4 / M4A
    try:
        covr = tags.get("covr")
        if covr:
            return bytes(covr[0])
    except Exception:
        pass

    # Vorbis comment with base64 picture
    try:
        import base64

        data = tags.get("metadata_block_picture")
        if data:
            from mutagen.flac import Picture

            return Picture(base64.b64decode(data[0])).data
    except Exception:
        pass

    return None


def load_art_bytes(track_path: str) -> bytes | None:
    """Cover file first (usually higher resolution), then embedded."""
    path = find_art_file(track_path)
    if path is not None:
        try:
            return path.read_bytes()
        except OSError:
            pass
    return extract_embedded_art(track_path)


@lru_cache(maxsize=64)
def _render_cached(key: tuple[str, float, int, int]) -> tuple[tuple[str, str, str], ...]:
    """Render to a hashable structure so lru_cache can hold it.

    The key carries the file mtime so edited art re-renders rather than
    serving a stale image forever.
    """
    track_path, _mtime, width, height = key
    data = load_art_bytes(track_path)
    if not data:
        return ()

    try:
        from PIL import Image
    except ImportError:
        return ()

    try:
        image = Image.open(io.BytesIO(data))
        image = image.convert("RGB")
    except Exception:
        return ()

    # Two pixels per cell vertically. Keep the image square-ish: terminal cells
    # are about twice as tall as they are wide, so a W x 2H pixel grid drawn in
    # W x H cells comes out roughly square on screen.
    pixel_w = max(1, width)
    pixel_h = max(2, height * 2)

    try:
        image = image.resize((pixel_w, pixel_h), Image.LANCZOS)
    except Exception:
        image = image.resize((pixel_w, pixel_h))

    pixels = image.load()
    rows: list[tuple[str, str, str]] = []
    for y in range(0, pixel_h - 1, 2):
        for x in range(pixel_w):
            top = pixels[x, y]
            bottom = pixels[x, y + 1]
            rows.append(
                (
                    f"#{top[0]:02x}{top[1]:02x}{top[2]:02x}",
                    f"#{bottom[0]:02x}{bottom[1]:02x}{bottom[2]:02x}",
                    UPPER_HALF,
                )
            )
    return tuple(rows)


def render_segments(track_path: str, width: int, height: int) -> list[list[Segment]]:
    """Render album art as rich Segments, one list per terminal row.

    Returns an empty list when there is no art - callers should fall back to
    something else rather than treat that as an error.
    """
    if not track_path or width < 2 or height < 1:
        return []

    try:
        mtime = Path(track_path).parent.stat().st_mtime
    except OSError:
        mtime = 0.0

    cells = _render_cached((track_path, mtime, width, height))
    if not cells:
        return []

    rows: list[list[Segment]] = []
    for row_index in range(height):
        row: list[Segment] = []
        base = row_index * width
        if base >= len(cells):
            break
        for column in range(width):
            index = base + column
            if index >= len(cells):
                break
            fg, bg, char = cells[index]
            row.append(Segment(char, Style(color=fg, bgcolor=bg)))
        rows.append(row)
    return rows


def has_art(track_path: str) -> bool:
    return bool(track_path) and load_art_bytes(track_path) is not None


def load_pil_image(track_path: str):
    """Return the cover as a PIL image, or None.

    Used to feed textual-image, which - when it is installed and the terminal
    supports it - draws real graphics via the kitty protocol or sixel instead
    of the half-block approximation above. kitty gets genuinely sharp art this
    way; everything else falls back to render_segments().
    """
    data = load_art_bytes(track_path)
    if not data:
        return None
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(data))
        image.load()
        return image.convert("RGB")
    except Exception:
        return None


def best_renderer() -> str:
    """Which art backend is available: "graphics" or "halfblock"."""
    try:
        import textual_image.widget  # noqa: F401

        return "graphics"
    except Exception:
        return "halfblock"
