"""Review-queue tests.

The point of the Review view is that it must never leave you wondering what to
do. These checks confirm that every reason an album can land there produces
distinct, actionable guidance, and that a MusicBrainz outage is reported as an
outage rather than filed as a library problem.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from djskippy.config import Config  # noqa: E402
from djskippy.maintenance import check_musicbrainz  # noqa: E402
from djskippy.tagger import Candidate, TaggerRequest  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


async def main() -> int:
    from djskippy.app import DJSkippy, ReviewItem, View

    print("\nguidance exists for every reason")
    for kind in ("weak", "nomatch", "offline", "duplicate"):
        item = ReviewItem(Path("/tmp/Album"), "reason", kind)
        guidance = item.guidance
        check(f"{kind} has guidance", len(guidance) >= 3,
              f"{len(guidance)} lines")
        check(f"{kind} names a key to press",
              any(k in " ".join(guidance) for k in ("enter", "X", "u", "a")),
              "")
    check("guidance differs by kind",
          len({tuple(ReviewItem(Path("/t"), "r", k).guidance)
               for k in ("weak", "nomatch", "offline", "duplicate")}) == 4)

    print("\nmulti-disc folders are named and explained properly")
    disc = ReviewItem(Path("/home/neil/Music/Merle Haggard/CD1"),
                      "best match only 70%", "weak")
    check("recognised as a disc", disc.is_disc_folder)
    check("shows the album, not just 'CD1'",
          disc.display_name == "Merle Haggard/CD1", disc.display_name)
    guide = " ".join(disc.guidance)
    check("explains why the score is low", "complete" in guide)
    check("points at the parent folder", "Merle Haggard" in guide)
    check("differs from the generic advice",
          disc.guidance != ReviewItem.GUIDANCE["weak"])

    normal = ReviewItem(Path("/home/neil/Music/Jeff Buckley/Grace"),
                        "best match only 74%", "weak")
    check("a normal album is not treated as a disc",
          not normal.is_disc_folder and normal.display_name == "Grace",
          normal.display_name)

    print("\nMusicBrainz health probe")
    healthy, message = check_musicbrainz()
    check("probe returns a verdict and a message",
          isinstance(healthy, bool) and bool(message),
          f"healthy={healthy}: {message}")

    cfg = Config.load()
    cfg.web.enabled = cfg.mpris.enabled = False
    cfg.visualiser.enabled = cfg.watcher.enabled = False
    cfg.playback.resume = False
    app = DJSkippy(cfg)

    def request(similarity: float | None, duplicate: bool = False,
                tag: str = "") -> TaggerRequest:
        candidates = []
        if similarity is not None:
            candidates = [Candidate(
                album="A", artist="B", distance=1.0 - similarity / 100.0,
                url="", info_line="",
            )]
        return TaggerRequest(
            # Distinct per call: _defer_for_review dedupes by path, so
            # reusing one would silently drop the second entry.
            path=f"/home/neil/Music/Test/Album-{tag or similarity}",
            item_count=10, candidates=candidates,
            is_duplicate=duplicate, duplicate_info="Existing",
        )

    async with app.run_test(size=(160, 45)) as pilot:
        answers: list = []
        app.tagger.respond = answers.append
        app._auto_tagging = True

        print("\nreasons are recorded distinctly")
        app._auto_answer(request(72.0))
        check("weak match recorded as weak",
              app.review_queue[-1].kind == "weak",
              app.review_queue[-1].reason)

        app._auto_answer(request(None, duplicate=True, tag="dup"))
        check("duplicate recorded as duplicate",
              app.review_queue[-1].kind == "duplicate",
              app.review_queue[-1].reason)

        # When MusicBrainz is answering, an empty result means the album
        # genuinely is not there - believe it rather than retrying.
        app._consecutive_no_candidates = 0
        app._health_checked_at = __import__("time").time()
        app._health_verdict = True
        app._auto_answer(request(None, tag="gap"))
        check("a believed no-match is recorded as nomatch",
              app.review_queue[-1].kind == "nomatch",
              app.review_queue[-1].reason)
        check("and it was not retried", answers[-1] == "skip",
              str(answers[-1]))

        print("\nempty lookups are retried before being given up on")
        app.review_queue.clear()
        answers.clear()
        app._retry_counts.clear()
        app._consecutive_no_candidates = 0
        app.cfg.watcher.lookup_retries = 2
        # Retrying only makes sense when the server is the problem.
        app._health_checked_at = __import__("time").time()
        app._health_verdict = False
        # Keep the test quick - the real delays are 3s, 6s, 12s, 24s.
        import djskippy.app as appmod
        real_sleep, appmod.time.sleep = appmod.time.sleep, lambda _s: None

        req = request(None, tag="flaky")
        app._auto_answer(req)
        check("first empty answer retries", answers[-1] == "rescan",
              str(answers[-1]))
        app._auto_answer(req)
        check("second empty answer retries", answers[-1] == "rescan",
              str(answers[-1]))
        app._auto_answer(req)
        check("gives up only after the retries", answers[-1] == "skip",
              str(answers[-1]))
        check("then lands in review", len(app.review_queue) == 1,
              f"{len(app.review_queue)} queued")

        appmod.time.sleep = real_sleep
        app.cfg.watcher.lookup_retries = 2
        app._health_verdict = True
        app.review_queue.clear()
        app._auto_answer(request(72.0, tag="weak2"))

        print("\nthe view renders guidance")
        # Populate explicitly. Relying on what the earlier sections left in
        # the queue made this order-dependent, and it failed in a full run
        # while passing in isolation.
        app.review_queue = [
            ReviewItem(Path("/home/neil/Music/A/Weak"), "best match only 72%",
                       "weak"),
            ReviewItem(Path("/home/neil/Music/B/Gap"), "no MusicBrainz match",
                       "nomatch"),
        ]
        await pilot.press("5")
        check("view 5 is Review", app.view is View.REVIEW)
        rows = app._panes[2].items
        check("review list rendered", len(rows) > 5, f"{len(rows)} rows")
        text = "\n".join(rows)
        check("shows a count", "waiting" in text)
        check("shows what to press", "enter" in text or "X" in text)
        check("offers retry", "retry" in text.lower())

        print("\nempty review reads as success, not emptiness")
        app.review_queue.clear()
        app._render_review()
        empty = "\n".join(app._panes[2].items)
        check("explains why empty is good",
              "Nothing needs you" in empty and "guessed at" in empty)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
