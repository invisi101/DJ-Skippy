"""Optional browser player on localhost.

Runs a small Flask app in a daemon thread. It is a remote control for the
running DJ-Skippy instance *and* a standalone player: /stream serves the audio
so you can listen in the browser on a machine that is not the one with the
speakers attached.

Bound to 127.0.0.1 by default. Nothing on the network can reach it.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .library import Library
    from .player import Player

PAGE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DJ-Skippy</title>
<style>
  :root {
    --bg: #16161e; --panel: #1f1f2b; --fg: #e6e6f0; --dim: #9a9ab0;
    --accent: #7aa2f7; --accent-2: #bb9af7; --line: #2c2c3c;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 17px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  header {
    padding: 18px 20px; border-bottom: 1px solid var(--line);
    display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
  }
  h1 { margin: 0; font-size: 1.4rem; letter-spacing: .5px; }
  .sub { color: var(--dim); font-size: 1rem; }
  main { padding: 20px; max-width: 1100px; margin: 0 auto; }
  .now {
    background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
    padding: 20px; margin-bottom: 22px;
  }
  .title { font-size: 1.3rem; font-weight: 600; margin-bottom: 4px; }
  .meta { color: var(--dim); font-size: 1.05rem; }
  .bar {
    height: 8px; background: var(--line); border-radius: 4px;
    margin: 18px 0 8px; overflow: hidden; cursor: pointer;
  }
  .fill { height: 100%; background: linear-gradient(90deg,var(--accent),var(--accent-2)); width: 0; }
  .times { display: flex; justify-content: space-between; color: var(--dim); font-size: .95rem; }
  .controls { display: flex; gap: 10px; margin-top: 18px; flex-wrap: wrap; }
  button {
    font-size: 1.1rem; padding: 10px 18px; border-radius: 8px;
    border: 1px solid var(--line); background: #262636; color: var(--fg);
    cursor: pointer; font-family: inherit;
  }
  button:hover { background: #30304a; border-color: var(--accent); }
  button.primary { background: var(--accent); color: #11111a; font-weight: 600; border-color: var(--accent); }
  input[type=search] {
    width: 100%; font-size: 1.05rem; padding: 12px 14px; border-radius: 8px;
    border: 1px solid var(--line); background: var(--panel); color: var(--fg);
    font-family: inherit; margin-bottom: 14px;
  }
  ul { list-style: none; padding: 0; margin: 0; }
  li {
    padding: 12px 14px; border-bottom: 1px solid var(--line);
    display: flex; justify-content: space-between; gap: 14px; cursor: pointer;
  }
  li:hover { background: var(--panel); }
  li .t { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  li .d { color: var(--dim); font-variant-numeric: tabular-nums; }
  .hint { color: var(--dim); font-size: .95rem; margin-top: 24px; }
</style>
</head>
<body>
<header>
  <h1>DJ-Skippy</h1>
  <span class="sub" id="backend">&nbsp;</span>
</header>
<main>
  <div class="now">
    <div class="title" id="title">Nothing playing</div>
    <div class="meta" id="meta">&nbsp;</div>
    <div class="bar" id="bar"><div class="fill" id="fill"></div></div>
    <div class="times"><span id="pos">0:00</span><span id="dur">0:00</span></div>
    <div class="controls">
      <button onclick="ctl('previous')">&#9664;&#9664;</button>
      <button class="primary" onclick="ctl('playpause')" id="pp">&#9654;</button>
      <button onclick="ctl('stop')">&#9632;</button>
      <button onclick="ctl('next')">&#9654;&#9654;</button>
      <button onclick="ctl('volume_down')">Vol &minus;</button>
      <button onclick="ctl('volume_up')">Vol +</button>
      <button onclick="ctl('shuffle')" id="sh">Shuffle</button>
      <button onclick="ctl('repeat')" id="rp">Repeat</button>
    </div>
  </div>

  <input type="search" id="q" placeholder="Search your library - artist, album, title or genre">
  <ul id="results"></ul>
  <p class="hint">Click a result to play it here. Transport buttons control the
     DJ-Skippy running in your terminal.</p>
</main>
<script>
const $ = id => document.getElementById(id);
let audio = new Audio();

function fmt(s) {
  s = Math.max(0, Math.floor(s || 0));
  const m = Math.floor(s / 60), r = s % 60;
  return m + ':' + String(r).padStart(2, '0');
}

async function ctl(action) {
  await fetch('api/control/' + action, { method: 'POST' });
  refresh();
}

async function refresh() {
  try {
    const s = await (await fetch('api/state')).json();
    $('backend').textContent = s.track_count + ' tracks \\u00b7 ' + s.backend;
    $('title').textContent = s.title || 'Nothing playing';
    $('meta').textContent = s.artist ? s.artist + ' \\u2014 ' + s.album : '\\u00a0';
    $('pos').textContent = fmt(s.position);
    $('dur').textContent = fmt(s.duration);
    $('fill').style.width = (s.duration ? (s.position / s.duration * 100) : 0) + '%';
    $('pp').innerHTML = s.playing && !s.paused ? '&#10073;&#10073;' : '&#9654;';
    $('sh').style.borderColor = s.shuffle ? 'var(--accent)' : 'var(--line)';
    $('rp').textContent = 'Repeat: ' + s.repeat;
  } catch (e) { /* server went away; keep trying */ }
}

$('bar').addEventListener('click', async ev => {
  const rect = ev.currentTarget.getBoundingClientRect();
  const ratio = (ev.clientX - rect.left) / rect.width;
  await fetch('api/seek?ratio=' + ratio, { method: 'POST' });
  refresh();
});

let timer = null;
$('q').addEventListener('input', ev => {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    const q = ev.target.value.trim();
    if (!q) { $('results').innerHTML = ''; return; }
    const rows = await (await fetch('api/search?q=' + encodeURIComponent(q))).json();
    $('results').innerHTML = rows.map(r =>
      `<li onclick="playLocal(${r.id})"><span class="t">${esc(r.title)}
       <span class="d"> \\u2014 ${esc(r.artist)}</span></span>
       <span class="d">${esc(r.length)}</span></li>`).join('');
  }, 200);
});

function esc(s) {
  return String(s).replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function playLocal(id) {
  audio.pause();
  audio = new Audio('stream/' + id);
  audio.play();
}

refresh();
setInterval(refresh, 1000);
</script>
</body>
</html>
"""


class WebServer:
    """Flask app in a daemon thread."""

    def __init__(
        self,
        player: "Player",
        library: "Library",
        host: str = "127.0.0.1",
        port: int = 8080,
    ) -> None:
        self._player = player
        self._library = library
        self.host = host
        self.port = port
        self.running = False
        self.error: str | None = None
        #: Set when the configured port was taken and we moved off it.
        self.moved_from: int | None = None
        self._thread: threading.Thread | None = None
        self._server = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _build_app(self):
        from flask import Flask, Response, jsonify, request, send_file

        app = Flask(__name__)
        # Flask's request log would scribble all over the TUI.
        logging.getLogger("werkzeug").setLevel(logging.ERROR)

        player, library = self._player, self._library

        @app.route("/")
        def index():
            return Response(PAGE, mimetype="text/html")

        @app.route("/api/state")
        def state():
            s = player.state
            track = s.track
            return jsonify(
                title=track.title if track else "",
                artist=track.artist if track else "",
                album=track.album if track else "",
                position=s.position,
                duration=s.duration or (track.length if track else 0),
                playing=s.playing,
                paused=s.paused,
                volume=s.volume,
                shuffle=s.shuffle,
                repeat=s.repeat.value,
                backend=library.backend,
                track_count=library.track_count,
            )

        @app.route("/api/control/<action>", methods=["POST"])
        def control(action: str):
            actions = {
                "play": player.play,
                "pause": player.pause,
                "playpause": player.toggle_pause,
                "stop": player.stop,
                "next": player.next,
                "previous": player.previous,
                "volume_up": lambda: player.adjust_volume(5),
                "volume_down": lambda: player.adjust_volume(-5),
                "mute": player.toggle_mute,
                "shuffle": player.toggle_shuffle,
                "repeat": player.cycle_repeat,
            }
            handler = actions.get(action)
            if handler is None:
                return jsonify(error="unknown action"), 404
            handler()
            return jsonify(ok=True)

        @app.route("/api/seek", methods=["POST"])
        def seek():
            try:
                ratio = float(request.args.get("ratio", 0))
            except ValueError:
                return jsonify(error="bad ratio"), 400
            duration = player.state.duration
            if duration:
                player.seek_to(max(0.0, min(1.0, ratio)) * duration)
            return jsonify(ok=True)

        @app.route("/api/search")
        def search():
            query = request.args.get("q", "")
            return jsonify([
                {
                    "id": t.id,
                    "title": t.title,
                    "artist": t.artist,
                    "album": t.album,
                    "length": t.length_str,
                }
                for t in library.search(query)[:100]
            ])

        @app.route("/stream/<int:track_id>")
        def stream(track_id: int):
            track = library.by_id(track_id)
            if track is None or not track.exists:
                return jsonify(error="not found"), 404
            # conditional=True gives us range requests, so the browser can seek.
            return send_file(track.path, conditional=True)

        return app

    @staticmethod
    def _port_free(host: str, port: int) -> bool:
        """Can we actually bind this port?"""
        import socket

        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, port))
            return True
        except OSError:
            return False
        finally:
            probe.close()

    def _pick_port(self, attempts: int = 12) -> int | None:
        """The configured port, or the next free one after it.

        Probing first matters: werkzeug's make_server does not raise a
        catchable error when the port is taken - it prints a message and
        terminates the process. 8080 is contested enough (Docker, Jellyfin,
        any number of dev servers) that a music player refusing to start
        because of it would be absurd.
        """
        for offset in range(attempts):
            candidate = self.port + offset
            if candidate > 65535:
                break
            if self._port_free(self.host, candidate):
                return candidate
        return None

    def start(self) -> bool:
        if self.running:
            return True
        try:
            from werkzeug.serving import make_server

            port = self._pick_port()
            if port is None:
                self.error = (
                    f"ports {self.port}-{self.port + 11} are all in use"
                )
                self.running = False
                return False
            if port != self.port:
                self.moved_from = self.port
                self.port = port

            app = self._build_app()
            self._server = make_server(self.host, self.port, app, threaded=True)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="dj-skippy-web",
                daemon=True,
            )
            self._thread.start()
            self.running = True
            self.error = None
            return True
        except Exception as exc:
            self.error = str(exc)
            self.running = False
            return False

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            self._server = None
        self.running = False
