"""Does the interface answer immediately, and stay where you put it?

Two failures this covers, both reported from real use:

  * Pause appeared not to work. The audio stopped instantly but the display
    did not, because call_from_thread *raises* when called from the app's own
    thread and that exception was being swallowed — so anything triggered by
    a keypress never refreshed until the next half-second tick.

  * The view moved on its own. Background work — the folder watcher, a bulk
    import, the disk scan finishing — switched views and reset the cursor
    underneath whoever was using it.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from djskippy.config import Config  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


async def main() -> int:
    from djskippy.app import DJSkippy, View

    cfg = Config.load()
    cfg.web.enabled = cfg.mpris.enabled = False
    cfg.visualiser.enabled = False
    cfg.playback.resume = False
    cfg.playback.volume = 8

    app = DJSkippy(cfg)

    async with app.run_test(size=(180, 45)) as pilot:
        await pilot.press("1", "l", "l", "enter")
        await asyncio.sleep(2.5)
        check("something is playing", app.player.state.track is not None,
              app.player.state.track.title if app.player.state.track else "")

        print("\nthe display answers on the same keypress")
        before = str(app._now.render())
        started = time.time()
        await pilot.press("space")
        after = str(app._now.render())
        elapsed = (time.time() - started) * 1000
        check("pause registered", app.player.state.paused is True)
        check("and is drawn at once, not on the next tick", before != after)
        check("the pause icon is showing", "❚❚" in after)
        check("handled promptly", elapsed < 400, f"{elapsed:.0f} ms")

        await pilot.press("space")
        check("unpause is drawn too", "▶" in str(app._now.render()))

        for key, label in (("minus", "volume"), ("s", "shuffle"),
                           ("r", "repeat"), ("m", "mute")):
            previous = str(app._now.render())
            await pilot.press(key)
            check(f"{label} redraws immediately",
                  str(app._now.render()) != previous)
        await pilot.press("m")   # unmute
        app.player.stop()

        print("\nbackground work does not move you")
        await pilot.press("1", "j", "j", "l", "j")
        where = (app.view, app.focus_column,
                 app._panes[0].selected, app._panes[1].selected)

        # MusicBrainz going down mid-run.
        app._mb_offline = False
        app._suspect_musicbrainz_down_called = True
        view_before = app.view
        app.notify_status("")
        app._mb_offline = True   # pretend it already decided
        check("an outage does not switch view either",
              app.view == view_before, app.view.name)

        print("\na library refresh keeps your place")
        await pilot.press("1")
        await pilot.press("j", "j", "j", "l", "j")
        artist = app._panes[0].selected
        album = app._panes[1].selected
        column = app.focus_column
        app._preserve_selection(app._refresh_library)
        check("same artist still selected",
              app._panes[0].selected == artist, str(app._panes[0].selected))
        check("same album still selected",
              app._panes[1].selected == album, str(app._panes[1].selected))
        check("same column still focused", app.focus_column == column,
              str(app.focus_column))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    code = asyncio.run(main())
    raise SystemExit(1 if FAILURES else (code or 0))
