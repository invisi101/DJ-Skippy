# DJ-Skippy

A terminal music player. Plays everything in your music folder — every format,
gapless — with a live audio visualiser, named playlists, media-key support and
an optional browser player.

Navigation is vim/yazi-style miller columns. Transport semantics are borrowed
from cmus, which got them right.

No database, no tagging service, nothing to import. Put music in the folder and
it is in the library.

```
╭─ Artists ──────╬─ Albums ───────────────╬─ Tracks ─────────────────╮
│ Bob Dylan      │ 1984 Red Roses for Me  │ 01 Transmetropolitan     │
│>The Pogues     │>1985 Rum, Sodomy & the │>02 The Old Main Drag     │
│ Frank Zappa    │ 1988 If I Should Fall  │ 03 Wild Cats of Kilkenny │
│ Jeff Buckley   │ 1989 Peace and Love    │ 04 I'm a Man You Don't   │
╰────────────────┴────────────────────────┴──────────────────────────╯
▂▅▇▃▂▆█▅▃▁▄▇▆▂▅█▃▁▄▆▇▃▂▅▆█▄▂▁▃▅▇▆▄▂▁▃▅▇█▆▄▂▁▃▄▆▇▅▃▁▂▄
▶ The Old Main Drag — The Pogues (FLAC)
  1:12 ━━━━━━━━╸───────────────── 3:32   vol 80%  shuffle
 1:Library 2:Playlist 3:Queue 4:Browser 5:Help
   enter play  space pause  v stop  b/z next/prev  ? help  q quit
```

---

## Install

```bash
sudo pacman -S --needed python mpv cava python-mpv python-textual \
                        python-dbus-next python-flask python-mutagen playerctl

git clone https://github.com/invisi101/DJ-Skippy.git ~/Projects/dj-skippy
cd ~/Projects/dj-skippy
ln -sf "$PWD/bin/dj-skippy" ~/.local/bin/dj
```

| Package | For |
|---|---|
| `mpv`, `python-mpv` | playback — every format ffmpeg decodes, gapless, ReplayGain |
| `python-textual` | the interface |
| `python-mutagen` | reading tags |
| `cava` | the visualiser (optional) |
| `python-dbus-next` | media keys via MPRIS (optional) |
| `python-flask` | the browser player (optional) |

Only the first three are needed. Everything else can be switched off.

---

## Quick start

```bash
dj
```

1. **`j`/`k`** move, **`l`** goes deeper (artist → album → track), **`h`** back
2. **`Enter`** on a track plays the album from there
3. **`space`** pauses
4. **`?`** shows every key

Startup is instant: tags are cached against each file's size and mtime, so
after the first run a few thousand files load in a twentieth of a second. Add,
remove or retag a file and it is picked up automatically.

---

## Keys

### Navigation — vim and yazi

| Key | Action |
|---|---|
| `j` `k` | down / up |
| `h` `l` | left / right between columns (in Browser, `h` goes up a folder) |
| `g g` / `G` | top / bottom |
| `ctrl+d` `ctrl+u` | half page down / up |
| `/` | search — then `n` / `N` |
| `:` | command mode |
| `Enter` | descend, or play |

### Views

| Key | View |
|---|---|
| `1` | Library — the three-column browser |
| `2` | Playlist — what plays through in order |
| `3` | Queue — jumps ahead of the playlist |
| `4` | Browser — play anything on disk |
| `5` | Help |

### Transport — from cmus

| Key | Action |
|---|---|
| `space` | play / pause |
| `x` `c` `v` | play / pause / stop |
| `b` `z` | next / previous |
| `←` `→` | seek 5s · `[` `]` seek 30s |
| `+` `-` | volume · `m` mute |
| `s` | shuffle · `r` repeat: off → all → one |

`z` restarts the current track unless you are within the first five seconds, in
which case it goes to the genuinely previous one. cmus's behaviour, and the
right one.

### Playlists

| Key | Action |
|---|---|
| `a` | append the selection to the playlist |
| `e` | enqueue — plays next, ahead of the playlist |
| `S` | name and save the current playlist |
| `L` | browse saved playlists (`d` delete, `r` rename) |
| `p` | add the selection straight to a named playlist |
| `d` | remove from playlist / clear queue |

On an **artist** that means every album; on an **album**, the whole album; on a
**track**, just that track.

### Toggles

| Key | Action |
|---|---|
| `V` | visualiser · `w` web player · `?` help · `q` quit |

---

## The library

Everything under your music folder, read with mutagen. Where a file has no
useful tags the folder layout is used instead — `Artist/Album/track` — so even
untagged music is browsable. Disc folders are stepped over, so
`Artist/Album/CD2/track` still reports the album rather than "CD2".

Artists sort under their real name: "The Pogues" files under P. Albums sort
oldest first.

`:reload` rescans.

---

## Playlists

Saved as **M3U8** in `~/.local/share/dj-skippy/playlists/`, so mpv, VLC and
everything else can open them.

**Build one:** navigate to an album or artist, press **`a`** to append, repeat
as often as you like, then **`S`** and give it a name.

**Add to an existing one without loading it:** put the cursor on anything and
press **`p`**, then type the name. Created if it does not exist; nothing is
duplicated.

**Manage:** **`L`** lists them, **`Enter`** plays one, **`d`** deletes, **`r`**
renames. Or `:save`, `:load`, `:addto`, `:rename`, `:rm`, `:playlists`.

### Playlist vs. queue

The best idea cmus ever had:

- The **playlist** (`2`) is what you are listening to, in order.
- The **queue** (`3`) is what you want to hear *next*. Queued tracks jump the
  line, and once it empties the playlist resumes where it was.

`a` adds to the playlist. `e` adds to the queue.

---

## The Browser (`4`)

A file browser for playing things outside your music folder — a USB stick, a
download.

| Key | Action |
|---|---|
| `Enter` | open a folder, or **play a file** |
| `h` | up one folder |
| `a` `e` | add a file or a whole folder to the playlist / queue |

Playing from here imports nothing and changes nothing. The rest of the folder
is queued behind whatever you pick, so an album plays through.

---

## The visualiser

cava runs alongside and its output is drawn as native bars. It listens to your
*output* device, so it visualises whatever is audible. Toggle with **`V`**, or
`:cava` / `:nocava`.

One trap worth knowing: cava refuses an **odd** number of bars in stereo, and
the bars are sized to your terminal width. Odd widths are rounded down.

---

## Web player

`http://127.0.0.1:8080`, bound to localhost — nothing on your network can reach
it. Transport buttons drive the terminal; search results play **in the
browser**, streamed with range requests. Toggle with **`w`**, or `:web` /
`:noweb`. If the port is taken it moves to the next free one and says so.

---

## Media keys

Registers on D-Bus as `org.mpris.MediaPlayer2.DJSkippy`, so media keys,
`playerctl`, and any desktop bar that speaks MPRIS work with no setup.

```bash
playerctl --player=DJSkippy play-pause
dj --remote next
dj --remote status
```

---

## Commands

| Command | Effect |
|---|---|
| `:q` | quit |
| `:save` / `:load` / `:addto` / `:rename` / `:rm` / `:playlists` | playlists |
| `:add <query>` | append everything matching a search |
| `:reload` | rescan the music folder |
| `:web` / `:noweb` | browser player |
| `:cava` / `:nocava` | visualiser |
| `:set volume=80` | 0–130 |
| `:set replaygain=smart` | `smart`, `track`, `album`, `off` |
| `:theme <name>` | Textual theme |

---

## Configuration

`~/.config/dj-skippy/config.toml`, written with commented defaults on first
run. Delete it to regenerate. A mistyped value falls back to its default rather
than stopping the program.

```toml
[library]
music_dir = "~/Music"
smart_artist_sort = true      # "The Pogues" sorts under P

[playback]
replaygain = "smart"          # smart | track | album | off
resume = true                 # reopen where you left off
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
```

### ReplayGain

Your files probably carry `REPLAYGAIN_*` tags already. `smart` uses **album
gain** when playing an album straight through — preserving the dynamics it was
mastered with — and **track gain** when shuffling or working a queue. cmus's
semantics, mapped onto mpv.

---

## Command line

```
dj                        launch
dj --music-dir PATH       a different folder for this run
dj --no-web               no browser player
dj --no-cava              no visualiser
dj --no-mpris             no D-Bus
dj --remote ACTION        play, pause, playpause, stop, next, prev, status
dj --version
```

---

## How it works

| Layer | Engine |
|---|---|
| Playback | **mpv** via libmpv — every format, gapless, ReplayGain |
| Library | the filesystem, read with **mutagen**, tags cached |
| Interface | **Textual** |
| Visualiser | **cava**, read through its raw output |
| Remote | **MPRIS** over D-Bus, plus a Flask web player |

**Gapless is real.** The next track is handed to mpv while the current one is
still playing, so mpv performs the handover internally. Measured by recording
the speakers during a transition, it matches raw mpv to within 10ms.

**It dies when its window does.** Close the terminal and the player, visualiser,
web server and D-Bus registration all go with it. Playback position is saved on
the way out. Even on `kill -9`, cava is killed by the kernel via
`PR_SET_PDEATHSIG` rather than left running.

### Things worth knowing if you read the source

- **mpv's end-file event carries an integer reason**, not a string. Matching it
  as text matches nothing, so tracks never advance and an unplayable file
  stalls the playlist. Compare against `mpv.MpvEventEndFile.EOF` / `.ERROR`.
- **`call_from_thread` raises when called from the app's own thread.** Swallow
  that and every keypress-triggered redraw silently does nothing.
- **Textual's `Widget` already owns `.offset`**, so the list pane's scroll
  position is `scroll_top`.
- **`Input` selects its whole value on focus**, wiping any prefilled prefix.
- **werkzeug does not raise on a taken port** — it prints and exits the
  process. The port is probed with a plain socket first.
- **Textual's shutdown writes to the terminal**, so on SIGHUP it blocks and the
  process survives detached. Signal handling tears down itself and does not
  wait.
- **cava refuses an odd bar count in stereo**, silently, unless you keep its
  stderr.

---

## Troubleshooting

**No music showing up** — check `music_dir`. `:reload` rescans.

**No sound** — mpv plays through PipeWire/PulseAudio. Confirm with
`mpv --no-video <file>`. Check volume and `m` for mute.

**Visualiser flat** — cava captures the output device; if nothing is audible
there is nothing to draw. Any failure is reported in place of the bars.

**Media keys do nothing** — `playerctl --list-all` should show `DJSkippy`. Needs
a session D-Bus, so not on a bare TTY.

---

## Reference docs

Guides to the other tools on this machine, kept because they are useful:

- **[docs/CMUS.md](docs/CMUS.md)** — cmus: views, keys, filters, ReplayGain
- **[docs/BEETS.md](docs/BEETS.md)** — beets: tagging a library from
  MusicBrainz, if you ever want to do that separately

---

## Licence

MIT.

Built with [Claude Code](https://claude.com/claude-code).
