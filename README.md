# DJ-Skippy

A terminal music player that does the whole job: plays anything, tags your
library from MusicBrainz, watches your music folder and imports new albums by
itself, shows album art and a live audio visualiser, manages named playlists,
and serves a browser player on localhost.

Navigation is vim/yazi-style miller columns. Transport and library semantics
are borrowed from cmus, which got them right.

```
╭─ Artists ─────╬─ Albums ──────╬─ Tracks ─────────╮ ╭─ Art ──────╮
│ Bob Dylan     │ 1984 Red Ros. │ 01 Transmetropo. │ │            │
│>The Pogues    │>1985 Rum, So. │>02 The Old Main  │ │  [ cover ] │
│ Frank Zappa   │ 1988 If I Sh. │ 03 Wild Cats of  │ │            │
│ Jeff Buckley  │ 1989 Peace a. │ 04 I'm a Man You │ │            │
╰───────────────┴───────────────┴──────────────────╯ ╰────────────╯
▂▅▇▃▂▆█▅▃▁▄▇▆▂▅█▃▁▄▆▇▃▂▅▆█▄▂▁▃▅▇▆▄▂▁▃▅▇█▆▄▂▁▃▄▆▇▅▃▁▂▄
▶ The Old Main Drag — The Pogues (FLAC)
  1:12 ━━━━━━━━╸───────────────── 3:32   vol 80%  shuffle
 1:Library 2:Playlist 3:Queue 4:Browser 5:Review 6:Import 7:Help
```

---

## Contents

- [Install](#install)
- [Quick start](#quick-start)
- [Keys](#keys)
- [The library](#the-library)
- [Playlists](#playlists)
- [Importing your library](#importing-your-library)
- [When something needs you](#when-something-needs-you--review-5)
- [Automatic importing](#automatic-importing)
- [Tagging by hand](#tagging-by-hand)
- [Album art](#album-art)
- [The visualiser](#the-visualiser)
- [Web player](#web-player)
- [Media keys and MPRIS](#media-keys-and-mpris)
- [Commands](#commands)
- [Configuration](#configuration)
- [Command line](#command-line)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Tests](#tests)
- [Reference docs](#reference-docs)

---

## Install

### Dependencies

System packages (Arch / CachyOS):

```bash
sudo pacman -S --needed python mpv cava python-mpv python-textual \
                        python-dbus-next python-flask python-mutagen \
                        python-pillow python-watchdog beets playerctl
```

| Package | Used for |
|---|---|
| `mpv`, `python-mpv` | playback — every format ffmpeg can decode, gapless, ReplayGain |
| `python-textual` | the interface |
| `cava` | the audio visualiser |
| `beets` | library database and MusicBrainz tagging |
| `python-dbus-next` | MPRIS, so media keys work |
| `python-flask` | the browser player |
| `python-mutagen`, `python-pillow` | tags and album art |
| `python-watchdog` | watching the music folder |
| `playerctl` | optional; command-line media control |

### Setup

```bash
git clone https://github.com/invisi101/DJ-Skippy.git ~/Projects/dj-skippy
cd ~/Projects/dj-skippy

# Optional but recommended: sharp album art via the kitty graphics protocol.
python -m venv --system-site-packages .venv
./.venv/bin/pip install textual-image

# Put it on your PATH
ln -sf "$PWD/bin/dj-skippy" ~/.local/bin/dj-skippy
ln -sf "$PWD/bin/dj-skippy" ~/.local/bin/dj
```

Without the virtualenv DJ-Skippy still runs on the system Python and draws
album art as true-colour half-blocks instead. The launcher picks the
virtualenv automatically when it exists.

---

## Quick start

```bash
dj
```

That is it. DJ-Skippy reads your beets library if you have one, and otherwise
scans `~/Music` directly, so it works before anything has been imported.

1. **`j`/`k`** move up and down, **`l`** goes deeper (artist → album → track),
   **`h`** comes back.
2. **`Enter`** on a track plays that album from that track.
3. **`space`** pauses.
4. **`?`** shows every key.

---

## Keys

### Navigation — vim and yazi

| Key | Action |
|---|---|
| `j` `k` | down / up |
| `h` `l` | left / right between columns |
| `g g` | jump to top |
| `G` | jump to bottom |
| `ctrl+d` `ctrl+u` | half page down / up |
| `/` | search — then `n` / `N` for next and previous match |
| `:` | command mode |
| `Enter` | descend, or play |

### Views

| Key | View |
|---|---|
| `1` | Library — the three-column browser |
| `2` | Playlist — what is queued to play through |
| `3` | Queue — tracks that jump ahead of the playlist |
| `4` | Browser — the filesystem, for tagging folders |
| `5` | Review — albums the auto-importer could not decide alone |
| `6` | Import — bulk import and beets maintenance |
| `7` | Help |

### Transport — from cmus

| Key | Action |
|---|---|
| `space` | play / pause |
| `x` `c` `v` | play / pause / stop |
| `b` `z` | next / previous track |
| `←` `→` | seek 5 seconds |
| `[` `]` | seek 30 seconds |
| `+` `-` | volume up / down |
| `m` | mute |
| `s` | shuffle |
| `r` | repeat: off → all → one |

`z` (previous) restarts the current track unless you are within the first five
seconds, in which case it goes to the genuinely previous track. This is cmus's
behaviour and it is the correct one.

### Library and playlists

| Key | Action |
|---|---|
| `a` | append the selection to the current playlist |
| `e` | enqueue the selection — plays next, ahead of the playlist |
| `S` | name and save the current playlist |
| `L` | browse saved playlists (`d` deletes, `r` renames) |
| `p` | add the selection straight to a named playlist |
| `t` | tag this album from MusicBrainz |
| `I` | import everything not yet in the library |
| `d` | remove from playlist / clear queue |

Whatever the cursor is on is what gets used. On an **artist** that means every
album they have; on an **album**, the whole album; on a **track**, just that
track.

### Toggles

| Key | Action |
|---|---|
| `V` | visualiser on / off |
| `A` | album art on / off |
| `w` | web player on / off |
| `?` | help |
| `q` | quit |

---

## The library

DJ-Skippy has two backends and picks automatically:

1. **beets** — if `~/.config/beets/library.db` exists and has tracks in it.
   This is preferred because it holds proper MusicBrainz metadata.
2. **Filesystem scan** — reads tags directly with mutagen. Slower and dumber,
   but it means DJ-Skippy is useful on a folder of untagged files.

The status line tells you which is in use. `:reload` rescans.

Artists sort under their real name — "The Pogues" files under P, not T. Albums
sort oldest first, because that is how people think about a discography. Both
are configurable.

---

## Playlists

Playlists are saved as **M3U8** in `~/.local/share/dj-skippy/playlists/`, so
mpv, VLC, and everything else can open them too.

**Build one:**

1. Navigate to an album or artist and press **`a`** to append it. Repeat for
   as many as you like — different artists, individual tracks, whatever.
2. Press **`S`**, type a name, press Enter.

**Add to an existing playlist without loading it** — the Apple Music move:

- Put the cursor on anything and press **`p`**, then type the playlist name.
  If it does not exist yet, it is created. If the tracks are already in it,
  nothing is duplicated.

**Manage them:**

- **`L`** lists every playlist with its track count.
- **`Enter`** loads and plays one.
- **`d`** deletes the highlighted playlist.
- **`r`** renames it.

Or by command: `:save <name>`, `:load <name>`, `:addto <name>`,
`:rename <old> <new>`, `:rm <name>`, `:playlists`.

### Playlist vs. queue

They are different things, and the distinction is the best idea cmus ever had:

- The **playlist** (`2`) is what you are listening to — it plays in order.
- The **queue** (`3`) is what you want to hear *next*. Queued tracks jump the
  line, and once the queue empties the playlist resumes exactly where it was.

`a` adds to the playlist. `e` adds to the queue.

---

## Importing your library

Press **`6`** for the Import view. It tells you how much of what is on disk is
actually in the library, and lists every album folder that is not.

| Key | Action |
|---|---|
| `I` | **import everything** that is not in the library yet |
| `Enter` | tag just the highlighted folder, interactively |
| `Esc` | stop a running import |
| `D` | find duplicates |
| `M` | albums with tracks missing |
| `R` | re-sync tags from MusicBrainz |
| `F` | download missing album art |

`I` runs unattended under the same confidence gate as the watcher: matches at
or above 90% are applied, everything weaker goes to Review (`5`). Progress is
shown live, and `Esc` stops it at any point — already-tagged albums stay
tagged.

The same operations exist as commands: `:import`, `:dup`, `:missing`,
`:mbsync`, `:fetchart`, `:stats`.

**You should never need to type a `beet` command.** If you want to anyway,
there is a full guide in [docs/BEETS.md](docs/BEETS.md).

---

## Automatic importing

**Drop an album into `~/Music` and DJ-Skippy handles it.** No command, no
prompt.

What actually happens:

1. The folder watcher notices new audio files.
2. It **waits until the folder stops changing** (20 seconds by default). Albums
   arrive one file at a time; importing a half-copied folder gives you a
   half-tagged album.
3. It looks the album up on MusicBrainz through beets.
4. **If the match is 90% or better, it is tagged automatically** and appears in
   your library.
5. **If it is weaker than that — or it duplicates an album you already have —
   it goes to the Review list (`5`) instead.**

That last point matters. Auto-applying whatever MusicBrainz returns first is
how libraries quietly rot. Above 90% the differences are cosmetic
capitalisation; below it, they are usually a genuinely different release, and
that is a decision for a human.

**Nothing is ever deleted or overwritten.** A new album that duplicates an
existing one is always *kept alongside* it and flagged for you — DJ-Skippy will
not throw away your FLACs because a 192kbps copy turned up.

Press **`5`** to see what is waiting, and **`Enter`** on any entry to tag it
interactively.

Turn the whole thing off with `enabled = false` under `[watcher]`.

---

## When something needs you — Review (`5`)

Nothing is ever guessed at. Anything the importer will not decide alone lands
in Review, **and every entry tells you why it is there and what to do**.

| Marker | Means | What to do |
|---|---|---|
| `?` | Match found but below the threshold | `Enter` — look at it and decide. Usually a different edition |
| `✗` | MusicBrainz has no such release | `Enter`, then `u` to import with your own tags. Common for bootlegs and live recordings |
| `⟳` | MusicBrainz did not answer | `X` to re-check and retry. **Their server, not your library** |
| `=` | You already have this album | `Enter` to decide. It was *kept* — nothing was replaced |

Keys: `Enter` opens it, `X` retries everything, `D` dismisses an entry. The
guidance panel follows your cursor.

### About MusicBrainz outages

Under load MusicBrainz returns HTTP 503, and beets surfaces that as "no
matching release found" — indistinguishable, from the outside, from an album
that genuinely is not in the database. Treating the two the same would file
your whole library under "needs review" on a bad afternoon.

DJ-Skippy handles it in three layers:

1. **Retries every empty lookup** — 4 times by default, backing off 3s, 6s,
   12s, 24s. Albums that appear missing usually resolve on a later attempt.
2. **Checks before starting** a bulk import, and refuses to begin if
   MusicBrainz is down, rather than burning through your library for nothing.
3. **Stops mid-run** if three albums in a row come back empty and a health
   check confirms the server is the problem — then says so plainly.

Tune with `lookup_retries` under `[watcher]`.

---

## Tagging by hand

Press **`t`** on an album in the Library, or on a folder in the Browser (`4`).

DJ-Skippy shows you the proposed match: similarity score, the release it
matched, the MusicBrainz URL, and a track-by-track before/after with changes
marked `≠`.

| Key | Action |
|---|---|
| `a` | apply the match |
| `s` | skip — change nothing |
| `u` | use the existing tags as-is (import without MusicBrainz data) |
| `1`–`9` | pick a different candidate |
| `Esc` | abort the whole run |

On a duplicate: `k` keeps both (the safe default), `u` upgrades, `m` merges,
`s` skips.

This drives beets underneath, so it uses your `~/.config/beets/config.yaml` —
the same settings, the same plugins, the same behaviour as `beet import`.

---

## Album art

Art is found in this order:

1. `cover.jpg` / `folder.jpg` / `front.jpg` next to the audio files (this is
   what beets' `fetchart` plugin downloads)
2. Artwork embedded in the file's own tags — FLAC pictures, ID3 APIC, MP4
   `covr`

Rendering depends on what is available:

- **With `textual-image` installed** (the virtualenv), art is drawn with the
  **kitty graphics protocol** or **sixel** — real, sharp images.
- **Without it**, art is drawn as **true-colour half-blocks**: each character
  cell carries two pixels using `▀` with separate foreground and background
  colours. Chunky, but works in any 24-bit terminal.

Toggle the pane with **`A`**.

---

## The visualiser

cava runs as a subprocess with `output.method = raw`, and DJ-Skippy reads its
ASCII stream and draws the bars as native content. Bar colours shift from blue
through green to red with height.

Because cava listens to your **output device**, it visualises whatever is
audible — including audio from other applications. That is deliberate.

Toggle with **`V`**, or `:cava` / `:nocava`.

---

## Web player

A browser player on `http://127.0.0.1:8080`, bound to localhost — nothing on
your network can reach it.

It is both a remote control and a player:

- Transport buttons drive the DJ-Skippy running in your terminal.
- Search results play **in the browser**, streamed from `/stream/<id>` with
  range requests, so you can listen on a different machine.

Toggle with **`w`**, or `:web` / `:noweb`. Change the port under `[web]`.

---

## Media keys and MPRIS

DJ-Skippy registers on D-Bus as `org.mpris.MediaPlayer2.DJSkippy`, so your
media keys, `playerctl`, and any desktop bar that speaks MPRIS (Quickshell,
Waybar, Plasma) control it with no further setup.

```bash
playerctl --player=DJSkippy play-pause
playerctl --player=DJSkippy metadata title
```

Or without D-Bus knowledge:

```bash
dj --remote playpause
dj --remote next
dj --remote status
```

---

## Commands

Press `:` then type.

| Command | Effect |
|---|---|
| `:q`, `:quit` | quit |
| `:save <name>` | save the current playlist |
| `:load <name>` | load and play a playlist |
| `:addto <name>` | add the selection to a playlist, creating it if needed |
| `:rename <old> <new>` | rename a playlist |
| `:rm <name>` | delete a playlist |
| `:playlists` | list saved playlists |
| `:add <query>` | append everything matching a search to the playlist |
| `:import <path>` | tag a folder |
| `:reload` | rescan the library |
| `:web` / `:noweb` | start / stop the browser player |
| `:cava` / `:nocava` | start / stop the visualiser |
| `:set volume=80` | set volume (0–130) |
| `:set replaygain=smart` | `smart`, `track`, `album`, or `off` |
| `:theme <name>` | Textual theme |

---

## Configuration

`~/.config/dj-skippy/config.toml`, written with commented defaults on first
run. Delete it to regenerate.

```toml
[library]
music_dir = "~/Music"
smart_artist_sort = true      # "The Pogues" sorts under P

[playback]
replaygain = "smart"          # smart | track | album | off
resume = true                 # reopen on the track and position you left
rewind_offset = 5             # seconds before "previous" leaves the track
volume = 80
continue_playback = true

[visualiser]
enabled = true
bars = "auto"
framerate = 60

[web]
enabled = true
host = "127.0.0.1"
port = 8080

[mpris]
enabled = true

[watcher]
enabled = true
settle_seconds = 20           # quiet period before a folder counts as copied
auto_threshold = 90           # below this, park in Review instead of applying
```

### ReplayGain

Your files almost certainly carry `REPLAYGAIN_*` tags already. Without them
applied, a quiet 1984 rip and a loud 2004 remaster differ wildly in volume.

`smart` uses **album gain** when playing an album straight through — preserving
the dynamics it was mastered with — and **track gain** when shuffling or
working through a queue, so everything is evenly matched. This is cmus's
semantics, mapped onto mpv.

---

## Command line

```
dj                        launch
dj --music-dir PATH       use a different folder for this run
dj --no-web               do not start the browser player
dj --no-cava              do not start the visualiser
dj --no-mpris             do not register on D-Bus
dj --remote ACTION        control a running instance
dj --version
```

`ACTION` is one of `play`, `pause`, `playpause`, `stop`, `next`, `prev`,
`status`.

---

## How it works

DJ-Skippy is deliberately not a from-scratch music player. It is a well-joined
interface over engines that are already excellent:

| Layer | Engine |
|---|---|
| Decoding and playback | **mpv** via libmpv — every format, gapless, ReplayGain |
| Library and tagging | **beets**, used as a Python library rather than a subprocess |
| Visualisation | **cava**, read through its raw output |
| Interface | **Textual** |
| Remote control | **MPRIS** over D-Bus, plus a Flask web player |

Because the tagger embeds beets' own `ImportSession`, it honours your existing
`~/.config/beets/config.yaml` exactly — same plugins, same match settings, same
non-destructive defaults.

### Things worth knowing if you read the source

- **beets 2.x stores paths relative** to the library directory and expands them
  through a *per-thread* ContextVar. Any thread that did not construct the
  Library reads it back empty and gets relative paths. `library.py` rebinds it
  on every access. This is the same bug that breaks beets' own web plugin.
- **beets 2.14 made `genre` multi-valued**, so `item.genre` raises on items
  that have none. Every field is read with `item.get()` and a default.
- **dbus-next needs evaluated string annotations**, so `mpris.py` deliberately
  does *not* use `from __future__ import annotations` — PEP 563 would store
  `"b"` as the source text `'"b"'` and break every signature.
- **Textual's `Widget` already owns `.offset`**, so the list pane's scroll
  position is `scroll_top`.
- **`Input` selects its whole value on focus**, which silently wipes any
  prefilled command prefix. `select_on_focus` is turned off.

---

## Troubleshooting

**No music showing up**
Check `music_dir` in the config. The status line on start says which backend
loaded and how many tracks it found. `:reload` rescans.

**No sound**
mpv plays through PipeWire/PulseAudio. Confirm with `mpv --no-video <file>`.
Check the volume is not at 0 and mute (`m`) is off.

**Visualiser is flat**
cava captures the *output* device. If nothing is audible, there is nothing to
draw. It needs PipeWire or PulseAudio; on a bare ALSA setup it will not
capture.

**Album art is blocky**
That is the half-block fallback. Install `textual-image` into the virtualenv
for real graphics, and use a terminal that supports the kitty protocol or
sixel.

**Media keys do nothing**
Check it registered: `playerctl --list-all` should show `DJSkippy`. It needs a
session D-Bus, so this will not work on a bare TTY.

**New albums are not importing**
The watcher waits for the folder to go quiet for `settle_seconds` first. Check
Review (`5`) — it may be waiting on a decision. Anything below the confidence
threshold lands there by design.

**Auto-import tagged something wrong**
Raise `auto_threshold` toward 95 or 100 so more albums route to Review. Setting
it above 100 sends everything there.

---

## Tests

```bash
./.venv/bin/python tests/smoke.py          # UI, keys, navigation (headless)
./.venv/bin/python tests/watcher_test.py   # folder watching and debouncing
./.venv/bin/python tests/playlist_test.py  # playlist create/load/rename/delete
./.venv/bin/python tests/integration.py    # real audio, cava, web, D-Bus
```

`smoke.py` drives the real application through Textual's test harness with no
terminal. `integration.py` plays a few seconds of actual audio and needs a
sound device, a session D-Bus, and a free port.

---

## Reference docs

- **[docs/BEETS.md](docs/BEETS.md)** — working guide to beets: importing,
  queries, fixing tags, and the non-obvious configuration traps (the
  `musicbrainz` plugin disabling itself, the data-source penalty that costs
  every album 11%, the web plugin's broken paths).
- **[docs/CMUS.md](docs/CMUS.md)** — working guide to cmus: views, keys,
  filters, ReplayGain, `cmus-remote`, and why its config file rejects trailing
  comments.

---

## Licence

MIT.

Built with [Claude Code](https://claude.com/claude-code).
