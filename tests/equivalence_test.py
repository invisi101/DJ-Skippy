"""Does DJ-Skippy do what beets would do?

DJ-Skippy embeds beets' own ImportSession rather than shelling out, so in
principle it inherits every setting. This checks that in practice:

  * the configuration DJ-Skippy imports under is the same one `beet` uses
  * the same plugins are active
  * albums tagged through DJ-Skippy carry the same MusicBrainz identifiers
    beets writes
  * the non-destructive promise holds — files still where they were, with
    tags changed in place

Read-only. It inspects the existing library and never imports anything.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


def beet(*args: str) -> str:
    try:
        return subprocess.run(
            ["beet", *args], capture_output=True, text=True, timeout=90
        ).stdout
    except Exception as exc:
        return f"<failed: {exc}>"


def main() -> int:
    from beets import config as beets_config
    from beets import plugins as beets_plugins

    print("\nsame configuration as the beet command")
    beets_config.read()
    our_db = beets_config["library"].as_filename()
    our_dir = beets_config["directory"].as_filename()

    cli = beet("config")

    def cli_value(key: str) -> str:
        """The raw value beet prints, expanded the way beets expands it."""
        for line in cli.splitlines():
            if line.startswith(f"{key}:"):
                return os.path.expanduser(line.split(":", 1)[1].strip())
        return ""

    # `beet config` echoes the file verbatim, so paths come back with ~ intact
    # while our API call returns them expanded. Compare like with like.
    check("library path matches", cli_value("library") == our_db,
          f"{cli_value('library')} vs {our_db}")
    check("music directory matches", cli_value("directory") == our_dir,
          f"{cli_value('directory')} vs {our_dir}")
    for setting in ("copy: no", "move: no", "write: yes"):
        check(f"honours '{setting}'", setting in cli.replace("'", ""))
    check("data_source penalty fix is live",
          "data_source_mismatch_penalty: 0.0" in cli)
    check("languages set to English", "languages: [en]" in cli or
          "languages:\n    - en" in cli)

    print("\nsame plugins")
    beets_plugins.load_plugins()
    beets_plugins.find_plugins()
    ours = {p.name for p in beets_plugins.find_plugins()}
    cli_version = beet("version")
    for name in ("musicbrainz", "fetchart", "chroma", "lastgenre"):
        check(f"{name} active in both",
              name in ours and name in cli_version,
              f"ours={name in ours} cli={name in cli_version}")

    print("\nwhat the tagging actually wrote")
    from djskippy.config import Config
    from djskippy.library import Library

    cfg = Config.load()
    lib = Library(cfg.library.music_dir, cfg.library.smart_artist_sort)
    check("DJ-Skippy reads the beets database",
          lib.backend in ("beets", "beets + disk"), lib.backend)
    check("and separates tagged from untagged", lib.tagged_count > 0,
          f"{lib.tagged_count} tagged of {lib.track_count}")
    check("library has content", lib.track_count > 0,
          f"{lib.track_count} tracks")

    # Inspect real files for the identifiers beets writes on a successful
    # MusicBrainz match. Only the tagged ones: the library now also lists
    # everything on disk that beets has never seen, which by definition
    # carries no MusicBrainz identifiers.
    import mutagen

    sampled = 0
    with_mbid = 0
    missing_files = 0
    for track in [t for t in lib.all_tracks if t.tagged][:400]:
        if not track.exists:
            missing_files += 1
            continue
        try:
            meta = mutagen.File(track.path)
        except Exception:
            continue
        if meta is None:
            continue
        sampled += 1
        keys = {str(k).lower() for k in (meta.tags.keys() if meta.tags else [])}
        if any("musicbrainz_albumid" in k or "musicbrainz album id" in k
               for k in keys):
            with_mbid += 1

    check("sampled real files", sampled > 0, f"{sampled} files read")
    check("every library file exists on disk", missing_files == 0,
          f"{missing_files} missing")
    ratio = (with_mbid / sampled * 100) if sampled else 0
    check("tagged files carry MusicBrainz IDs", ratio > 80,
          f"{with_mbid}/{sampled} ({ratio:.0f}%)")

    print("\nnon-destructive promise")
    music_dir = str(cfg.library.music_dir)
    outside = [t.path for t in lib.all_tracks if not t.path.startswith(music_dir)]
    check("nothing was moved out of the music folder", not outside,
          f"{len(outside)} outside" if outside else "")

    # beets' own view of the same library should agree with ours.
    stats = beet("stats")
    cli_tracks = 0
    for line in stats.splitlines():
        if line.lower().startswith("tracks:"):
            cli_tracks = int(line.split(":")[1].strip().replace(",", ""))
            break
    # beets counts what it has tagged; DJ-Skippy also lists untagged files,
    # so the tagged subset is what should agree.
    check("tagged count agrees with `beet stats`",
          abs(cli_tracks - lib.tagged_count) <= 5,
          f"beets={cli_tracks} dj-skippy tagged={lib.tagged_count} "
          f"(of {lib.track_count} total)")

    print("\nthe operations DJ-Skippy exposes are real beets commands")
    from djskippy.maintenance import BeetsCommand

    help_text = beet("--help")
    for name, (args, _) in BeetsCommand.OPERATIONS.items():
        check(f"`beet {args[0]}` exists", args[0] in help_text, args[0])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
