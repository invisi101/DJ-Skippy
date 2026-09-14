# cmus — a working guide

cmus is a small, fast terminal music player. It has been around since 2004, it
has never crashed on anyone, and its library model is good enough that
DJ-Skippy copies it.

This is a reference for using cmus directly — as a backup player, or when you
want something that starts in 50ms.

> Your cmus is already configured at `~/.config/cmus/rc` with ReplayGain and
> resume enabled. See [Your configuration](#your-configuration).

---

## Contents

- [Getting started](#getting-started)
- [The seven views](#the-seven-views)
- [Keys](#keys)
- [Playlist vs queue vs library](#playlist-vs-queue-vs-library)
- [Searching and filtering](#searching-and-filtering)
- [Commands](#commands)
- [Your configuration](#your-configuration)
- [Settings worth knowing](#settings-worth-knowing)
- [Colour schemes](#colour-schemes)
- [Controlling cmus from outside](#controlling-cmus-from-outside)
- [Gotchas](#gotchas)

---

## Getting started

```bash
cmus
```

**First run only** — add your music. Type this inside cmus, including the
colon:

```
:add ~/Music
```

It scans in the background. A large library takes a minute; you only do this
once, because the library persists in `~/.config/cmus/cache`.

Then press `1` for the library and `Enter` to play something.

To quit: `q`, then `y`.

---

## The seven views

Press the number keys to switch.

| Key | View | What it holds |
|---|---|---|
| `1` | **Library (tree)** | Artist → album tree. The one you will live in |
| `2` | **Library (sorted)** | The same tracks as one flat sortable list |
| `3` | **Playlist** | A list you build and save |
| `4` | **Play queue** | Tracks to play next, ahead of everything else |
| `5` | **File browser** | The filesystem, for adding music |
| `6` | **Filters** | Saved library filters |
| `7` | **Settings** | Every setting and key binding, editable live |

View `7` is genuinely useful — it lists every option with its current value,
and you can edit them in place.

---

## Keys

### Playback

| Key | Action |
|---|---|
| `x` | play |
| `c` | pause / unpause |
| `v` | stop |
| `b` | next track |
| `z` | previous track |
| `Enter` | play the selected track |
| `right` / `left` | seek +10s / −10s |
| `.` / `,` | seek +1m / −1m |
| `+` / `-` | volume up / down |
| `m` | toggle mute |

### Navigation

| Key | Action |
|---|---|
| `j` / `k` or arrows | down / up |
| `h` / `l` | collapse / expand in the tree |
| `g` / `G` | top / bottom |
| `ctrl+f` / `ctrl+b` | page down / up |
| `ctrl+d` / `ctrl+u` | half page down / up |
| `tab` | switch between the two panes in the tree view |

### Adding and removing

| Key | Action |
|---|---|
| `a` | add selection to the **library** |
| `y` | add selection to the **playlist** |
| `e` | add selection to the **queue** |
| `E` | add to the queue, at the front |
| `D` or `delete` | remove selection from the current view |
| `u` | update / rescan the library |
| `U` | update the selected item only |

### Playback modes

| Key | Action |
|---|---|
| `s` | toggle shuffle |
| `r` | toggle repeat |
| `C` | toggle continue (play the next track when one ends) |
| `M` | cycle play mode: all / artist / album |
| `o` | cycle play order |
| `f` | toggle follow (keep the cursor on the playing track) |

### Other

| Key | Action |
|---|---|
| `/` | search forward — then `n` / `N` |
| `?` | search backward |
| `:` | command mode |
| `i` | jump to the currently playing track |
| `q` | quit |
| `ctrl+l` | redraw the screen |

---

## Playlist vs queue vs library

This is cmus's best idea, and it confuses everyone at first.

- **Library** (`1`, `2`) — everything cmus knows about. Your whole collection.
- **Playlist** (`3`) — a list *you* built, and can save to a file. Persistent.
- **Queue** (`4`) — what plays **next**, jumping ahead of whatever is playing.
  The queue empties as it plays, and then normal playback resumes exactly where
  it was.

So: `y` adds to the playlist (keep for later), `e` adds to the queue (play
after this one).

The queue survives view switches and is the right tool for "play this next
without losing my place".

---

## Searching and filtering

**Search** (`/`) jumps the cursor to the next match. It does not hide anything.
`n` and `N` move between matches.

**Filters** hide everything that does not match, and are much more powerful:

```
:filter genre="Celtic Folk"
:filter artist=pogues
:filter year>=1980&year<=1989
:filter bitrate<192
:filter play_count>10
:filter !genre=country
```

Clear the filter with `:filter` on its own.

Save one for reuse:

```
:fset eighties=year>=1980&year<=1989
```

Saved filters appear in view `6`, where `space` toggles them on and off.

Operators: `=` `!=` `<` `>` `<=` `>=`, combined with `&` (and), `|` (or), `!`
(not). Fields include `artist`, `album`, `albumartist`, `title`, `genre`,
`year`, `bitrate`, `duration`, `filename`, `play_count`, `comment`.

---

## Commands

Press `:` first.

| Command | Effect |
|---|---|
| `:add PATH` | add files or folders to the library |
| `:clear` | empty the current view |
| `:save FILE` | save the playlist to a file |
| `:load FILE` | load a playlist file |
| `:set OPTION=VALUE` | change a setting |
| `:toggle OPTION` | flip a boolean setting |
| `:colorscheme NAME` | change theme |
| `:filter EXPR` | filter the library |
| `:fset NAME=EXPR` | save a named filter |
| `:live-filter EXPR` | filter as you type |
| `:invert` | invert the selection |
| `:mark` / `:unmark` | mark tracks for batch operations |
| `:win-activate` | act on the selection |
| `:shuffle` | reshuffle |
| `:update-cache` | rescan the library for changes |
| `:echo TEXT` | print a message |
| `:quit` | quit |

Tab completes commands and file paths.

---

## Your configuration

`~/.config/cmus/rc`, read at startup:

```
set replaygain=smart
set resume=true
```

cmus writes its own runtime state to `~/.config/cmus/autosave` separately, so
nothing in `rc` gets clobbered when you change things in the interface.

**ReplayGain is the important one.** Your files carry `REPLAYGAIN_*` tags
already, but cmus ignores them by default. Without it a quiet 1984 rip and a
loud 2003 remaster differ enormously in volume.

`smart` means: **album gain** when playing an album straight through —
preserving the dynamics it was mastered with — and **track gain** when
shuffling or playing a queue, so everything matches. Valid values are
`disabled`, `track-preferred`, `album-preferred`, and `smart`.

---

## Settings worth knowing

Change with `:set name=value`, or browse them all in view `7`.

| Setting | Default | Notes |
|---|---|---|
| `replaygain` | `disabled` | **Set this.** `smart` is what you want |
| `replaygain_limit` | `true` | Prevents clipping when gain overshoots |
| `replaygain_preamp` | `0.0` | dB adjustment on top |
| `resume` | `false` | Reopen on the track and position you left |
| `continue` | `true` | Play the next track automatically |
| `repeat` / `shuffle` | `false` | |
| `repeat_current` | `false` | Loop one track |
| `softvol` | `false` | Software volume, independent of system volume |
| `mpris` | `true` | D-Bus control — media keys, playerctl, your bar |
| `smart_artist_sort` | `true` | "The Pogues" files under P, not T |
| `sort_albums_by_name` | `false` | Otherwise albums sort by date |
| `rewind_offset` | `5` | Seconds before "previous" leaves the track |
| `auto_expand_albums_follow` | `true` | |
| `display_artist_sort_name` | `false` | |
| `format_current` | | Status line format string |
| `format_playlist` | | Playlist row format |
| `output_plugin` | auto | `pulse`, `alsa`, `jack`, `sndio`, `oss`, `ao` |
| `buffer_seconds` | `10` | Raise it if audio stutters |

---

## Colour schemes

Seventeen ship with cmus:

```
amazon  cyan  default  dracula  gray-88  green  green-mono-88  gruvbox
gruvbox-alt  gruvbox-warm  jellybeans  night  solarized-dark  solarized-light
spotify  xterm-white  zenburn
```

Try one live:

```
:colorscheme gruvbox
```

Make it permanent by adding `colorscheme gruvbox` to `~/.config/cmus/rc`.

---

## Controlling cmus from outside

`cmus-remote` drives a running instance — useful for keybindings and scripts.

```bash
cmus-remote --play
cmus-remote --pause          # toggles
cmus-remote --stop
cmus-remote --next
cmus-remote --prev
cmus-remote --volume +10%
cmus-remote --seek +30
cmus-remote --repeat
cmus-remote --shuffle

cmus-remote -Q               # full status, machine-readable
cmus-remote -C "set replaygain=album"   # run any cmus command
```

Because `mpris=true`, `playerctl` also works, which is what a desktop bar will
use:

```bash
playerctl --player=cmus play-pause
playerctl --player=cmus metadata title
```

Parsing status in a script:

```bash
cmus-remote -Q | grep '^tag title' | cut -d' ' -f3-
cmus-remote -Q | grep '^status' | cut -d' ' -f2
```

---

## Gotchas

### The rc file does not support trailing comments

This one is genuinely surprising:

```
set resume=true      # this comment becomes part of the value
```

cmus parses `true      # this comment...` as the value and errors with
`expected [0..1] or [false, true]`. **Comments must be on their own line.**

### Errors on startup scroll past

Config errors are printed before the interface initialises, so they can vanish
instantly. To see them:

```bash
cmus 2>&1 | head -20
```

### The library is a cache, not a live view

cmus does not watch your music folder. Add new music with `:add`, or rescan
with `u` / `:update-cache`. Deleted files stay listed until you update.

### `a` vs `y` vs `e`

`a` adds to the **library**, which is probably not what you meant when browsing
the file browser looking to play something now. `y` is playlist, `e` is queue.

### Volume above 100%

cmus can exceed 100% with `softvol`, and it will distort. If things sound bad,
check the volume is not at 130.

### It uses PulseAudio by default

Which works fine through PipeWire's compatibility layer. If you have no sound,
check `:set output_plugin` in view `7`.

---

## Further reading

- `man cmus` — genuinely good, unusually complete
- `man cmus-tutorial` — the official walkthrough
- <https://cmus.github.io>
- Key bindings and settings are all browsable live in view `7`
