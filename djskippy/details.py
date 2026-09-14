"""Lyrics, or failing that, whatever else is known about a track.

Looked for in this order:

  1. an .lrc file beside the audio, or in a Lyrics/ subfolder
  2. lyrics embedded in the file's own tags — USLT for MP3, LYRICS for
     FLAC and Vorbis, ©lyr for MP4

Both forms are cached against the file's size and mtime: this is read on
every cursor move, and re-decoding a file each time is exactly how the album
art pane used to make navigation feel heavy.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

#: "[00:12.34] line" — synced lyrics. The timestamps are stripped for display;
#: nothing here scrolls in time with the music.
TIMESTAMP = re.compile(r"^\s*\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]\s*")

#: Tag names that hold lyrics across the formats people actually have.
LYRIC_TAGS = ("USLT", "LYRICS", "UNSYNCEDLYRICS", "©LYR", "\xa9LYR", "LYRICIST")


def _stamp(path: Path) -> str:
    try:
        st = path.stat()
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return ""


def _lrc_beside(audio: Path) -> str | None:
    """An .lrc file named after the track, beside it or in Lyrics/."""
    candidates = [
        audio.with_suffix(".lrc"),
        audio.with_suffix(".LRC"),
        audio.parent / "Lyrics" / f"{audio.stem}.lrc",
        audio.parent / "lyrics" / f"{audio.stem}.lrc",
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return None


def _embedded(audio: Path) -> str | None:
    """Lyrics stored in the file's own tags."""
    try:
        import mutagen

        meta = mutagen.File(audio)
    except Exception:
        return None
    if meta is None or not meta.tags:
        return None

    try:
        for key in meta.tags.keys():
            name = str(key).upper()
            if not any(tag in name for tag in LYRIC_TAGS):
                continue
            value = meta.tags[key]
            text = getattr(value, "text", value)
            if isinstance(text, (list, tuple)):
                text = text[0] if text else ""
            text = str(text).strip()
            if len(text) > 20:      # a stray "lyricist" credit is not lyrics
                return text
    except Exception:
        pass
    return None


@lru_cache(maxsize=64)
def _lookup(path_str: str, _stamp_value: str) -> str | None:
    audio = Path(path_str)
    raw = _lrc_beside(audio) or _embedded(audio)
    if not raw:
        return None

    lines = [TIMESTAMP.sub("", line).rstrip() for line in raw.splitlines()]
    # Trim leading and trailing blanks without collapsing the verses.
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) if lines else None


def lyrics_for(track) -> str | None:
    """The lyrics for a track, or None."""
    if track is None or not getattr(track, "path", ""):
        return None
    path = Path(track.path)
    return _lookup(str(path), _stamp(path))


def details_for(track) -> list[tuple[str, str]]:
    """What is known about a track, for when there are no lyrics."""
    if track is None:
        return []

    path = Path(track.path)
    rows: list[tuple[str, str]] = [
        ("Title", track.title),
        ("Artist", track.artist),
    ]
    if track.albumartist and track.albumartist != track.artist:
        rows.append(("Album artist", track.albumartist))
    rows.append(("Album", track.album))
    if track.year:
        rows.append(("Year", str(track.year)))
    if track.track_no:
        rows.append(("Track", str(track.track_no)))
    if track.disc_no:
        rows.append(("Disc", str(track.disc_no)))
    if track.genre:
        rows.append(("Genre", track.genre))
    rows.append(("Length", track.length_str))
    rows.append(("Format", track.format))

    # Anything the decoder can tell us that the tags cannot.
    try:
        import mutagen

        meta = mutagen.File(path)
        info = getattr(meta, "info", None) if meta else None
        if info is not None:
            bitrate = getattr(info, "bitrate", 0)
            if bitrate:
                rows.append(("Bitrate", f"{round(bitrate / 1000)} kbps"))
            rate = getattr(info, "sample_rate", 0)
            if rate:
                rows.append(("Sample rate", f"{rate / 1000:g} kHz"))
            bits = getattr(info, "bits_per_sample", 0)
            if bits:
                rows.append(("Bit depth", f"{bits}-bit"))
            channels = getattr(info, "channels", 0)
            if channels:
                rows.append(
                    ("Channels", {1: "mono", 2: "stereo"}.get(channels, str(channels)))
                )
    except Exception:
        pass

    try:
        size = path.stat().st_size
        rows.append(("Size", f"{size / 1024 / 1024:.1f} MB"))
    except OSError:
        pass

    rows.append(("File", path.name))
    try:
        rows.append(("Folder", str(path.parent).replace(str(Path.home()), "~")))
    except Exception:
        pass
    return rows


def has_lyrics(track) -> bool:
    return lyrics_for(track) is not None
