# beets — a working guide

beets is a **library manager and tagger**, not a player. It looks your music up
on MusicBrainz, corrects the tags, and keeps a database of what you own.

DJ-Skippy drives beets for you, so you should rarely need this. Keep it for the
days you want to do something unusual, or something has gone wrong.

> **Your setup already has non-obvious fixes in it.** Read
> [Gotchas specific to this machine](#gotchas-specific-to-this-machine) before
> changing `config.yaml` — several defaults are wrong in ways that are not
> obvious until they have wasted an afternoon.

---

## Contents

- [The mental model](#the-mental-model)
- [Your configuration](#your-configuration)
- [Importing](#importing)
- [The import prompt](#the-import-prompt)
- [Duplicates](#duplicates)
- [Querying the library](#querying-the-library)
- [Fixing things after the fact](#fixing-things-after-the-fact)
- [Plugins you have](#plugins-you-have)
- [Gotchas specific to this machine](#gotchas-specific-to-this-machine)
- [Recipes](#recipes)

---

## The mental model

Three separate things, and confusing them causes most beets pain:

| Thing | Where | What it is |
|---|---|---|
| **Your files** | `~/Music` | The actual audio |
| **Tags** | inside the files | Artist, album, title, MusicBrainz IDs |
| **The database** | `~/.config/beets/library.db` | beets' index of what it has seen |

**Importing** does two things: it writes correct tags *into your files*, and it
records them *in the database*.

Critically: **deleting the database does not undo tag changes.** The tags are in
the files. The database is only an index — delete it and you lose the catalogue,
not the work.

Your config has `copy: no` and `move: no`, which means **beets never
reorganises your folders**. It only rewrites tags in place. This is the safe
configuration and you should keep it.

---

## Your configuration

`~/.config/beets/config.yaml`. The parts that matter:

```yaml
directory: ~/Music
library: ~/.config/beets/library.db

import:
    copy: no            # never duplicate files into a new tree
    move: no            # never reorganise your folders
    write: yes          # DO fix tags inside the files
    incremental: yes    # skip directories already imported
    languages: [en]     # prefer English release titles
    log: ~/.config/beets/import.log

match:
    strong_rec_thresh: 0.10     # auto-accept 90%+ matches

musicbrainz:
    search_limit: 10
    data_source_mismatch_penalty: 0.0   # see Gotchas — this one matters
```

Check what beets actually thinks it is running:

```bash
beet config          # effective config, after all defaults
beet config -e       # open it in $EDITOR
beet version         # includes the loaded plugin list — check this first
```

---

## Importing

```bash
beet import ~/Music/"Some Artist"    # one artist
beet import ~/Music                  # everything
beet import --pretend ~/Music        # show what would happen, change nothing
```

Useful flags:

| Flag | Effect |
|---|---|
| `-q` | quiet — never prompt; auto-apply strong matches, skip the rest |
| `-p` | resume an interrupted run without asking |
| `-P` | do *not* resume |
| `-i` | incremental: skip already-imported directories (on by default here) |
| `-I` | ignore incremental, re-examine everything |
| `-s` | singletons — treat files as individual tracks, not albums |
| `-C` | do not copy (already the default in your config) |
| `-R` | do not record skipped folders, so they are offered again later |
| `-l FILE` | log to a specific file |

**The two-pass approach** for a large library — do the easy ones unattended,
then handle the awkward ones yourself:

```bash
beet import -q -p -R ~/Music                 # pass 1: everything obvious
grep skip ~/.config/beets/import.log         # what needs a human
beet import ~/Music                          # pass 2: interactive
```

**Watch out:** `-q` with a terminal that is not interactive (a background job,
a script) combined with `resume: ask` in the config will stall or fail. Pass
`-p` or `-P` explicitly for unattended runs.

---

## The import prompt

When beets is not confident it shows a match and asks. The score is a
similarity percentage; the differences are listed with `≠`.

```
  Match (96.0%):
  The Pogues - Red Roses for Me
  ≠ tracks
  MusicBrainz, CD, 1984, XE, Pogue Mahone Records
  https://musicbrainz.org/release/7d62e337-...
     ≠ (#2) The Battle Of Brisbane -> (#2) The Battle of Brisbane
```

| Key | Meaning |
|---|---|
| `A` | **Apply** — use this match, rewrite the tags |
| `M` | **More candidates** — show other releases |
| `S` | **Skip** — leave this album alone entirely |
| `U` | **Use as-is** — import with your existing tags, no MusicBrainz data |
| `T` | **as Tracks** — treat as individual singles, not an album |
| `G` | **Group albums** — split a folder holding more than one album |
| `R` | **Rescan directory** — try the lookup again (useful when MusicBrainz is busy) |
| `E` | **Enter search** — search by artist and album name |
| `I` | **enter Id** — paste a MusicBrainz release ID or URL directly |
| `B` | **aBort** — stop the whole run |
| `D` | **eDit** — edit the tags by hand before importing |
| `C` | **edit Candidates** — edit, then re-match |

**`U` (use as-is) is not a shortcut past a difficult album.** It imports with
whatever tags the files already had, which defeats the point of running beets.
Use `E` or `I` to find the right release instead.

**Pasting an ID** is the fastest fix when beets cannot find something. Search
musicbrainz.org in a browser, open the correct release, and paste either the
full URL or just the ID at the `I` prompt.

---

## Duplicates

When an album is already in the library, beets asks:

```
This album is already in the library!
Old: 21 items, FLAC, 834kbps, 453.3 MiB
New: 21 items, MP3,  192kbps, 106.2 MiB
➜ [S]kip new, Merge all, Remove old, Keep all, Upgrade?
```

| Key | Effect | Danger |
|---|---|---|
| `S` | Skip the new one, keep what you have | safe |
| `K` | Keep all — both stay in the library | safe |
| `M` | Merge them into one album | safe-ish |
| `U` | Upgrade — replace the old with the new | **destructive** |
| `R` | Remove old — delete the existing one | **destructive** |

**Read the bitrates before answering.** In the example above, `R` or `U` would
throw away a 453 MiB FLAC rip in favour of a 106 MiB MP3. The answer there is
`S`.

Set a permanent default if you like:

```yaml
import:
    duplicate_action: skip     # skip | keep | merge | remove | ask
```

Find duplicates you already have:

```bash
beet dup                 # duplicate tracks
beet dup -a              # duplicate albums
beet dup -F bitrate      # show bitrate, to decide which to keep
```

---

## Querying the library

The query syntax is the same everywhere — `ls`, `dup`, `missing`, `modify`,
`remove`, `stats`.

```bash
beet ls dylan                     # anything matching "dylan"
beet ls -a pogues                 # albums only
beet ls artist:pogues             # a specific field
beet ls album:"Rum Sodomy"        # quotes for spaces
beet ls year:1984                 # exact
beet ls year:1980..1989           # range
beet ls genre:celtic              # substring by default
beet ls artist::^The              # regex with a double colon
beet ls -f '$artist - $title'     # custom output format
beet ls -f '$path'                # just the file paths
```

Combine terms with a space (AND) or `,` (OR):

```bash
beet ls artist:dylan year:1975..1979
beet ls genre:punk , genre:folk
```

Useful reports:

```bash
beet stats                        # totals
beet missing                      # albums with tracks missing
beet missing -c                   # just the counts
beet info "fairytale"             # every tag on a matching file
beet info -l "fairytale"          # include the library's view too
```

---

## Fixing things after the fact

```bash
# Re-fetch tags from MusicBrainz for things already imported
beet mbsync
beet mbsync artist:pogues

# Write the database's tags back into the files
beet write
beet write -p                     # preview only, change nothing

# Read tags from the files back into the database
beet update

# Change a field by hand
beet modify artist:pogues genre="Celtic Punk"
beet modify -a album:"Hell's Ditch" year=1990

# Edit in $EDITOR
beet edit artist:pogues

# Remove from the database (keeps the files)
beet remove artist:pogues
# Remove AND delete the files — careful
beet remove -d artist:pogues

# Album art
beet fetchart
beet fetchart -f                  # re-fetch even where art exists
```

`beet remove -d` deletes audio from disk. There is no undo.

---

## Plugins you have

Set in `config.yaml` under `plugins:`.

| Plugin | What it does |
|---|---|
| `musicbrainz` | **The tag database itself.** Required — see Gotchas |
| `chroma` | Acoustic fingerprinting — identifies files with useless tags |
| `fetchart` | Downloads album art as `cover.jpg` |
| `lastgenre` | Pulls genres from Last.fm |
| `duplicates` | `beet dup` |
| `missing` | `beet missing` |
| `info` | `beet info` |
| `edit` | `beet edit` |
| `mbsync` | `beet mbsync` |
| `web` | Browser interface on `localhost:8337` |
| `webfix` | **Local fix** so `web` can actually serve audio — see Gotchas |

Not enabled, deliberately:

- **`lyrics`** — fetches lyrics into tags. Slow, and noisy on a large library.
- **`embedart`** — embeds art *into every audio file*, rewriting all of them.

---

## Gotchas specific to this machine

These cost real time to find. They are all live in your config now.

### 1. `musicbrainz` is a plugin, and listing `plugins:` disables it

In beets 2.x, MusicBrainz support is itself a plugin, enabled by default. The
moment you write a `plugins:` list in your config, you **replace** that default
— and if you do not name `musicbrainz` explicitly, beets silently has no tag
source at all. Every album reports "No matching release found", including
albums MusicBrainz obviously has.

Check with `beet version` — the plugin list is printed. `musicbrainz` must be
in it.

### 2. `data_source_mismatch_penalty` taxes every album ~11%

beets docks every candidate a flat penalty when the file's existing
`data_source` tag does not name the source being matched. Files ripped
elsewhere have no such tag, so **every album takes the full hit, permanently.**

The penalty exists to break ties between multiple metadata sources (Discogs,
Spotify, Beatport). With MusicBrainz alone it is pure noise.

Measured on a real album: **85.1% → 96.0%** with it disabled.

```yaml
musicbrainz:
    data_source_mismatch_penalty: 0.0
```

### 3. `languages: [en]` matters on a Japanese locale

Without it, MusicBrainz will happily return Japanese-script titles for releases
that have them, and your albums come back tagged in kanji.

### 4. The `web` plugin cannot serve audio without a patch

beets 2.x stores paths *relative* to the library directory and expands them via
a per-thread ContextVar. The web plugin serves requests with
`app.run(threaded=True)`, and those threads never inherit the variable — so
paths stay relative and Flask resolves them against the Python package
directory:

```
FileNotFoundError: .../site-packages/beetsplug/web/The Pogues/....flac
```

The fix lives in `~/.config/beets/beetsplug/webfix.py` and rebinds the
directory per request. It is outside `/usr/lib` so a system update cannot
remove it.

### 5. Settings that do not exist are silently ignored

`ratelimit` and `ratelimit_interval` do not exist in beets 2.14 at all, and
`searchlimit` is the deprecated spelling of `search_limit`. beets does not warn
about unknown keys — it just ignores them. Always confirm with `beet config`.

### 6. `beet version` runs migrations

The first run after an upgrade migrates the database and writes
`library.db-before-*.bak` files alongside it. They are safe to keep or delete.

### 7. MusicBrainz returns "server is currently busy"

Under load, roughly one request in three can fail. This looks exactly like "no
match found". If matches suddenly stop working, test directly:

```bash
curl -s 'https://musicbrainz.org/ws/2/release/?query=release:"Red Roses For Me"&fmt=json&limit=1'
```

If it returns `{"error": "The MusicBrainz web server is currently busy..."}`,
wait and try again — nothing is wrong with your setup.

---

## Recipes

**Import a new album non-destructively**

```bash
beet import --pretend ~/Music/"New Artist"   # look first
beet import ~/Music/"New Artist"             # then do it
```

**Find the badly-tagged corners of your library**

```bash
beet ls -a album:"Unknown Album"
beet ls artist:"Unknown Artist"
beet ls -f '$path' year:0            # no year
```

**Identify files with no useful tags at all** — fingerprinting reads the audio
itself:

```bash
beet import -s ~/Music/mystery-files    # chroma will fingerprint them
```

**Re-tag something you got wrong the first time**

```bash
beet remove -a album:"Wrong Album"       # out of the database, files untouched
beet import ~/Music/"Artist/Album"       # and in again
```

**Bulk genre fix**

```bash
beet modify -a artist:pogues genre="Celtic Punk"
```

**See what beets would change without changing it**

```bash
beet write -p              # tag changes it would write
beet import --pretend DIR  # imports it would perform
beet move -p               # file moves (you have move: no, so: nothing)
```

**Back up the database**

```bash
cp ~/.config/beets/library.db ~/library.db.backup
```

It is a plain SQLite file. `sqlite3 library.db 'PRAGMA integrity_check'` will
tell you if it is healthy.

---

## Further reading

- Official docs: <https://beets.readthedocs.io>
- Query syntax: <https://beets.readthedocs.io/en/stable/reference/query.html>
- Plugin list: <https://beets.readthedocs.io/en/stable/plugins/>
- MusicBrainz: <https://musicbrainz.org>
