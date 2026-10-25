#!/usr/bin/env python3
"""
Pixoo Radar for the Divoom Pixoo64
==================================

Shows the airborne flight closest to home: airline logo, route with an
animated plane, and a panel cycling flight number, altitude, aircraft type,
registration, speed, distance and direction (optionally on split-flap tiles).
Military and rare aircraft are highlighted and shown first. Checks for a new
closest flight every REFRESH_SECONDS; the built-in web server shows it live.

With GIF_BASE_URL set, each animation is served as a GIF from a small built-in
web server and the Pixoo fetches and plays it itself (Device/PlayTFGif), so the
picture swaps in without the Pixoo's "Loading.." screen. Without it, frames are
pushed one by one over the Pixoo's API, which shows "Loading.." on every update.

Install:   pip install -r requirements.txt
Run:       PIXOO_HOST=192.168.1.50 HOME_LAT=51.4700 HOME_LON=-0.4543 python pixoo_radar.py
Preview:   HOME_LAT=51.4700 HOME_LON=-0.4543 python pixoo_radar.py --preview ./preview
           (renders PNGs and the GIF, no Pixoo needed)
Health:    GET /health on GIF_PORT -> 200 while the main loop is running, 503 if it stalls

Every setting below can be overridden with an environment variable of the same
name, e.g.  PIXOO_HOST=192.168.1.50 REFRESH_SECONDS=90 python pixoo_radar.py
PIXOO_HOST, HOME_LAT and HOME_LON are required.

Inspired by chrivoge/PixooRadar. Flight data comes from FlightRadar24's public
web endpoints via the unofficial FlightRadarAPI package, so keep it to personal use.
"""

import argparse
import base64
import hashlib
import ipaddress
import json
import logging
import math
import os
import secrets
import signal
import socket
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from urllib.parse import urlsplit, urlunsplit

import requests
from FlightRadarAPI import FlightRadar24API
from PIL import Image, ImageChops, ImageDraw


# =============================================================================
# Settings (each can be overridden by an environment variable of the same name)
# =============================================================================

def _setting(name, default):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    if isinstance(default, bool):
        return raw.lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, tuple):
        return tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    return raw


PIXOO_HOST = _setting("PIXOO_HOST", "")                    # IP or hostname of the Pixoo (required)
PIXOO_BRIGHTNESS = _setting("PIXOO_BRIGHTNESS", -1)        # 0-100, or -1 to leave it alone

HOME_LAT = _setting("HOME_LAT", 0.0)                       # your location (required)
HOME_LON = _setting("HOME_LON", 0.0)
RADAR_RADIUS_KM = _setting("RADAR_RADIUS_KM", 100)
SHOW_GROUND_TRAFFIC = _setting("SHOW_GROUND_TRAFFIC", False)  # include parked/taxiing aircraft
IGNORE_HELICOPTERS = _setting("IGNORE_HELICOPTERS", True)
SKIP_UNKNOWN_DESTINATION = _setting("SKIP_UNKNOWN_DESTINATION", False)  # e.g. positioning/training flights

# Military and rare aircraft get a flashing border and badge, and (with PRIORITIZE_HIGHLIGHTS)
# are shown ahead of closer ordinary flights. Military flights rarely have a flight number,
# so they're only shown at all with HIGHLIGHT_MILITARY on.
HIGHLIGHT_MILITARY = _setting("HIGHLIGHT_MILITARY", True)
HIGHLIGHT_RARE = _setting("HIGHLIGHT_RARE", True)
PRIORITIZE_HIGHLIGHTS = _setting("PRIORITIZE_HIGHLIGHTS", True)
RARE_TYPES = _setting("RARE_TYPES", (                       # ICAO type designators
    "a124", "a225", "an22", "a3st", "a337", "blcf", "b741", "b742", "b743", "b74s", "b744", "b748",
    "md11", "dc10", "l101", "il76", "il96", "il62", "a342", "a343", "a345", "a346", "conc", "dc3", "dc6",
))
RARE_REGISTRATIONS = _setting("RARE_REGISTRATIONS", ())     # e.g. special liveries: g-xlea,a6-eeu
MILITARY_CALLSIGNS = _setting("MILITARY_CALLSIGNS", (       # callsign prefixes / operator ICAO codes
    "rch", "cnv", "pat", "duke", "rrr", "cfc", "gaf", "ctm", "baf", "naf", "iam", "ame", "haf",
    "plf", "svf", "asy", "kiw", "nato",
))
COLOR_MILITARY = _setting("COLOR_MILITARY", "#FF3030")
COLOR_RARE = _setting("COLOR_RARE", "#00C8FF")

REFRESH_SECONDS = _setting("REFRESH_SECONDS", 60)          # how often to look for the closest flight
FRAME_MS = _setting("FRAME_MS", 300)                         # animation speed
SHOW_DIRECTION = _setting("SHOW_DIRECTION", True)            # compass point of the plane in place of "DIST"
# Flight values on flipping departures-board tiles: off, all (every character) or digits
SPLIT_FLAP = {"1": "all", "true": "all", "yes": "all", "on": "all", "all": "all", "digits": "digits"}.get(
    _setting("SPLIT_FLAP", "off").lower(), "off")

LOGO_SOURCES = _setting("LOGO_SOURCES", ("fr24", "avcodes"))  # tried in order

# Address where the Pixoo can reach this app's GIF server, e.g. http://pixooradar.lan:8765
# (plain http; the Pixoo won't fetch https or follow a redirect to it). A hostname is
# resolved here and handed to the Pixoo as an IP. Leave empty to push frames instead.
# Opening the server's root in a browser shows what the Pixoo is showing.
GIF_BASE_URL = _setting("GIF_BASE_URL", "")
GIF_PORT = _setting("GIF_PORT", 8765)

COLOR_TEXT = _setting("COLOR_TEXT", "#FFFF00")    # flight details
COLOR_BOX = _setting("COLOR_BOX", "#2B2B2B")      # panels behind text
COLOR_LABEL = "#999999"
COLOR_SEPARATOR = "#555555"
COLOR_ROUTE = "#666666"
COLOR_PLANE = "#FFFFFF"
COLOR_WHITE = "#FFFFFF"

HTTP_TIMEOUT = (5, 20)        # connect, read (seconds)
GIF_RESET_EVERY = 32          # fresh animation-id counter every N uploads (Pixoo freezes otherwise)
SELF_CHECK_WAIT = _setting("SELF_CHECK_WAIT", 120)   # how long our own address may take to start
SELF_CHECK_INTERVAL = 5                              # answering (Swarm routes to healthy tasks only)
HOSTED_RETRY_SECONDS = _setting("HOSTED_RETRY_SECONDS", 1800)  # re-try hosted GIFs this often
AVCODES_URL = "https://raw.githubusercontent.com/Jxck-S/airline-logos/main/avcodes_banners/{icao}.png"

log = logging.getLogger("pixoo_radar")
fr24 = FlightRadar24API(timeout=15)


# =============================================================================
# Pixel fonts: 5x7 for values and names, 3x5 for labels
# =============================================================================

def _font(glyphs):
    return {char: spec.split() for char, spec in glyphs.items()}


F57 = _font({
    "A": ".###. #...# #...# ##### #...# #...# #...#", "B": "####. #...# #...# ####. #...# #...# ####.",
    "C": ".###. #...# #.... #.... #.... #...# .###.", "D": "####. #...# #...# #...# #...# #...# ####.",
    "E": "##### #.... #.... ####. #.... #.... #####", "F": "##### #.... #.... ####. #.... #.... #....",
    "G": ".###. #...# #.... #.### #...# #...# .####", "H": "#...# #...# #...# ##### #...# #...# #...#",
    "I": "### .#. .#. .#. .#. .#. ###", "J": "..### ...#. ...#. ...#. ...#. #..#. .##..",
    "K": "#...# #..#. #.#.. ##... #.#.. #..#. #...#", "L": "#.... #.... #.... #.... #.... #.... #####",
    "M": "#...# ##.## #.#.# #.#.# #...# #...# #...#", "N": "#...# #...# ##..# #.#.# #..## #...# #...#",
    "O": ".###. #...# #...# #...# #...# #...# .###.", "P": "####. #...# #...# ####. #.... #.... #....",
    "Q": ".###. #...# #...# #...# #.#.# #..#. .##.#", "R": "####. #...# #...# ####. #.#.. #..#. #...#",
    "S": ".#### #.... #.... .###. ....# ....# ####.", "T": "##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..",
    "U": "#...# #...# #...# #...# #...# #...# .###.", "V": "#...# #...# #...# #...# #...# .#.#. ..#..",
    "W": "#...# #...# #...# #.#.# #.#.# #.#.# .#.#.", "X": "#...# #...# .#.#. ..#.. .#.#. #...# #...#",
    "Y": "#...# #...# .#.#. ..#.. ..#.. ..#.. ..#..", "Z": "##### ....# ...#. ..#.. .#... #.... #####",
    "0": ".###. #...# #..## #.#.# ##..# #...# .###.", "1": "..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###.",
    "2": ".###. #...# ....# ...#. ..#.. .#... #####", "3": "##### ...#. ..#.. ...#. ....# #...# .###.",
    "4": "...#. ..##. .#.#. #..#. ##### ...#. ...#.", "5": "##### #.... ####. ....# ....# #...# .###.",
    "6": "..##. .#... #.... ####. #...# #...# .###.", "7": "##### ....# ...#. ..#.. .#... .#... .#...",
    "8": ".###. #...# #...# .###. #...# #...# .###.", "9": ".###. #...# #...# .#### ....# ...#. .##..",
    " ": "... ... ... ... ... ... ...", "-": "... ... ... ### ... ... ...",
    ":": ". . # . . # .", ".": ". . . . . . #", "/": "....# ...#. ...#. ..#.. .#... .#... #....",
    "+": "..... ..#.. ..#.. ##### ..#.. ..#.. .....", "?": ".###. #...# ....# ...#. ..#.. ..... ..#..",
    "▲": "..... ..... ..#.. .###. ##### ..... .....", "▼": "..... ..... ##### .###. ..#.. ..... .....",
})

F35 = _font({
    "A": ".#. #.# ### #.# #.#", "B": "##. #.# ##. #.# ##.", "C": ".## #.. #.. #.. .##",
    "D": "##. #.# #.# #.# ##.", "E": "### #.. ##. #.. ###", "F": "### #.. ##. #.. #..",
    "G": ".## #.. #.# #.# .##", "H": "#.# #.# ### #.# #.#", "I": "### .#. .#. .#. ###",
    "J": "..# ..# ..# #.# .#.", "K": "#.# #.# ##. #.# #.#", "L": "#.. #.. #.. #.. ###",
    "M": "#.# ### ### #.# #.#", "N": "##. #.# #.# #.# #.#", "O": ".#. #.# #.# #.# .#.",
    "P": "##. #.# ##. #.. #..", "Q": ".#. #.# #.# ##. .##", "R": "##. #.# ##. #.# #.#",
    "S": ".## #.. .#. ..# ##.", "T": "### .#. .#. .#. .#.", "U": "#.# #.# #.# #.# ###",
    "V": "#.# #.# #.# #.# .#.", "W": "#.# #.# ### ### #.#", "X": "#.# #.# .#. #.# #.#",
    "Y": "#.# #.# .#. .#. .#.", "Z": "### ..# .#. #.. ###",
    "0": "### #.# #.# #.# ###", "1": ".#. ##. .#. .#. ###", "2": "##. ..# .#. #.. ###",
    "3": "### ..# .## ..# ###", "4": "#.# #.# ### ..# ..#", "5": "### #.. ##. ..# ##.",
    "6": ".## #.. ### #.# ###", "7": "### ..# .#. .#. .#.", "8": "### #.# ### #.# ###",
    "9": "### #.# ### ..# ##.", " ": "... ... ... ... ...", "-": "... ... ### ... ...",
    ":": ". # . # .", ".": ". . . . #", "/": "..# ..# .#. #.. #..", "+": "... .#. ### .#. ...",
    "?": "##. ..# .#. ... .#.",
})


@lru_cache(maxsize=None)
def rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def text_width(text, font):
    glyphs = [font.get(c, font[" "]) for c in str(text).upper()]
    return sum(len(g[0]) + 1 for g in glyphs) - 1 if glyphs else 0


def draw_text(img, xy, text, font, color):
    x, y = xy
    px, col = img.load(), rgb(color)
    for char in str(text).upper():
        glyph = font.get(char, font[" "])
        for dy, row in enumerate(glyph):
            for dx, bit in enumerate(row):
                if bit == "#" and 0 <= x + dx < 64 and 0 <= y + dy < 64:
                    px[x + dx, y + dy] = col
        x += len(glyph[0]) + 1
    return x


def fill(img, x, y, w, h, color):
    ImageDraw.Draw(img).rectangle((x, y, x + w - 1, y + h - 1), fill=rgb(color))


def dashed_line(img, y):
    for x in range(0, 64, 4):
        fill(img, x, y, 2, 1, COLOR_SEPARATOR)


# =============================================================================
# Pixoo64 client (local HTTP API)
# =============================================================================

class Pixoo:
    """Minimal client for the Pixoo64's local HTTP API."""

    def __init__(self, host):
        self.url = f"http://{host}/post"
        self.gif_id = None

    def _post(self, command, **params):
        resp = requests.post(self.url, data=json.dumps({"Command": command, **params}), timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        result = resp.json()
        if result.get("error_code", 0) != 0:
            raise RuntimeError(f"{command} returned {result}")
        return result

    def set_brightness(self, level):
        self._post("Channel/SetBrightness", Brightness=max(0, min(100, level)))

    def play_gif_url(self, url):
        """Have the Pixoo download a GIF and loop it (no "Loading.." screen)."""
        self._post("Device/PlayTFGif", FileType=2, FileName=url)

    def show(self, frames, frame_ms):
        """Upload 64x64 RGB frames as one looping animation (shows "Loading..")."""
        if self.gif_id is None or self.gif_id >= GIF_RESET_EVERY:
            self._post("Draw/ResetHttpGifId")
            self.gif_id = 0
        self.gif_id += 1
        try:
            for offset, frame in enumerate(frames):
                self._post(
                    "Draw/SendHttpGif",
                    PicNum=len(frames), PicWidth=64, PicOffset=offset, PicID=self.gif_id,
                    PicSpeed=frame_ms, PicData=base64.b64encode(frame.convert("RGB").tobytes()).decode(),
                )
        except Exception:
            self.gif_id = None  # resync the counter on the next upload
            raise


def encode_gif(frames, frame_ms):
    """Encode frames the way the Pixoo plays hosted GIFs cleanly: one global palette,
    no transparency, later frames cropped to what changed (disposal 1), one delay, looping."""
    strip = Image.new("RGB", (64, 64 * len(frames)))
    for i, frame in enumerate(frames):
        strip.paste(frame, (0, 64 * i))
    palette = strip.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    indexed = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    out = BytesIO()
    indexed[0].save(
        out, format="GIF", save_all=True, append_images=indexed[1:], duration=frame_ms, loop=0,
        disposal=1, optimize=False, palette=bytes(palette.getpalette()[:768]),
    )
    return out.getvalue()


INDEX_HTML = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pixoo Radar</title>
<style>
:root{color-scheme:dark;--bg:#0b0b0c;--card:#161618;--line:#2a2a2e;--text:#ececec;--dim:#8a8a90;
--accent:#ffd60a;--mil:#ff4b4b;--rare:#2ec8ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,-apple-system,sans-serif;
padding:16px;display:flex;justify-content:center}
main{width:100%;max-width:980px;display:grid;gap:16px;grid-template-columns:minmax(0,1fr)}
@media(min-width:760px){main{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.wide{grid-column:1/-1}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
#screen{display:block;width:100%;aspect-ratio:1;image-rendering:pixelated;background:#000;border-radius:8px}
h1{margin:0;font-size:28px;letter-spacing:.5px}
.airline{color:var(--dim);margin-bottom:12px}
.route{font-size:22px;font-weight:600;color:var(--accent);margin:8px 0 14px}
dl{display:grid;grid-template-columns:auto 1fr;gap:6px 16px;margin:0}
dt{color:var(--dim)}dd{margin:0;font-variant-numeric:tabular-nums}
.tag{display:inline-block;font-size:12px;font-weight:700;padding:2px 8px;border-radius:99px;
margin-left:8px;vertical-align:middle;color:#000}
.tag.military{background:var(--mil)}.tag.rare{background:var(--rare)}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--dim);font-weight:500}tr.on td{color:var(--accent)}
.scroll{overflow-x:auto}.dim{color:var(--dim);font-size:13px}
</style>
<main>
  <div class="card"><img id="screen" src="latest.gif" alt="What the Pixoo is showing"></div>
  <div class="card" id="flight"><p class="dim">Waiting for the first flight…</p></div>
  <div class="card wide"><div class="dim" style="margin-bottom:8px">Nearby traffic</div>
    <div class="scroll"><table id="traffic"></table></div></div>
  <p class="dim wide" id="updated"></p>
</main>
<script>
const $ = (tag, text, cls) => { const e = document.createElement(tag);
  if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const km = d => d == null ? "" : (d < 10 ? d.toFixed(1) : Math.round(d)) + " km";
const ft = a => a == null ? "" : a <= 0 ? "ground" : a.toLocaleString() + " ft";
const tags = (el, list) => (list || []).forEach(t => el.append($("span", t === "military" ? "MIL" : "RARE", "tag " + t)));
let shown = null;
function render(s) {
  if (s.screen !== shown) { shown = s.screen; document.getElementById("screen").src = "latest.gif?" + s.screen; }
  const box = document.getElementById("flight"), f = s.flight;
  if (f) {
    box.replaceChildren();
    const h = $("h1", f.flight || "?"); tags(h, f.tags); box.append(h);
    box.append($("div", f.airline || "", "airline"));
    box.append($("div", (f.origin || "---") + " → " + (f.destination || "---"), "route"));
    const dl = $("dl");
    for (const [k, v] of [["Altitude", f.altitude_text], ["Speed", f.speed ? f.speed + " kt" : ""],
                          ["Distance", km(f.distance_km) + " " + f.direction], ["Aircraft", f.aircraft],
                          ["Registration", f.registration]]) { dl.append($("dt", k), $("dd", v || "—")); }
    box.append(dl);
  }
  const t = document.getElementById("traffic"); t.replaceChildren();
  const head = $("tr"); ["Flight", "Type", "Route", "Altitude", "Distance", ""].forEach(c => head.append($("th", c)));
  t.append(head);
  for (const r of s.traffic || []) {
    const tr = $("tr", null, f && r.flight === f.flight ? "on" : "");
    const name = $("td", r.flight); tags(name, r.tags);
    tr.append(name, $("td", r.aircraft || ""), $("td", (r.origin || "") + "–" + (r.destination || "")),
              $("td", ft(r.altitude)), $("td", km(r.distance_km)), $("td", r.direction));
    t.append(tr);
  }
  if (!(s.traffic || []).length) { const tr = $("tr"); tr.append($("td", s.note || "No traffic yet")); t.append(tr); }
  document.getElementById("updated").textContent = s.age_seconds == null ? "Starting…" :
    "Updated " + s.age_seconds + " s ago · " + (s.mode || "");
}
async function tick() {
  try { render(await (await fetch("api/current", {cache: "no-store"})).json()); }
  catch (e) { document.getElementById("updated").textContent = "Can't reach Pixoo Radar, retrying…"; }
}
tick(); setInterval(tick, 5000);
</script>
"""


def with_ip(url):
    """Swap the URL's hostname for its IP, so the Pixoo doesn't have to resolve it."""
    parts = urlsplit(url)
    try:
        ipaddress.ip_address(parts.hostname)
        return url
    except ValueError:
        pass
    try:
        ip = socket.gethostbyname(parts.hostname)
    except OSError as exc:
        log.warning("Couldn't resolve %s (%s); giving the Pixoo the name instead", parts.hostname, exc)
        return url
    netloc = ip + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class GifHost:
    """Tiny web server holding the latest animations for the Pixoo to fetch."""

    def __init__(self, base_url, port):
        self.base_url = base_url.rstrip("/")
        self.token = secrets.token_hex(4)   # fresh path every start
        self.files, self.fetched, self.count, self.latest = {}, {}, 0, None
        self.check_body = f"pixoo-radar {self.token}".encode()
        self.heartbeat = time.monotonic()   # bumped by the main loop; /health goes stale without it
        self.status, self.status_time = {}, None   # what the web page shows next to the screen
        host = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self, with_body):
                path, status = self.path.split("?")[0].lstrip("/"), 200
                if path == "":
                    data, ctype = INDEX_HTML.encode(), "text/html; charset=utf-8"
                elif path == f"{host.token}/check":
                    data, ctype = host.check_body, "text/plain"
                elif path == "api/current":
                    data, ctype = json.dumps(host.current()).encode(), "application/json"
                elif path == "health":
                    healthy, body = host.health()
                    data, ctype, status = json.dumps(body).encode(), "application/json", 200 if healthy else 503
                else:
                    data, ctype = host.files.get(host.latest if path == "latest.gif" else path), "image/gif"
                if data is None:
                    self.send_error(404)
                    return
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if with_body:
                    self.wfile.write(data)
                    if path in host.fetched:   # the Pixoo fetching a fresh animation
                        host.fetched[path].set()

            def do_GET(self):
                self._serve(True)

            def do_HEAD(self):
                self._serve(False)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def self_check(self, wait_seconds=SELF_CHECK_WAIT):
        """Fetch our own address the way the Pixoo will. False only if it's definitely broken.

        Keeps retrying a refused/unanswered connection for wait_seconds: an orchestrator
        can take a while to route our published port to us (Docker Swarm doesn't add a
        task to the ingress mesh until it passes its healthcheck, so our own address
        refuses connections for the first half-minute or so)."""
        url = f"{self.base_url}/{self.token}/check"
        deadline, waited = time.monotonic() + wait_seconds, False
        while True:
            try:
                resp = requests.get(url, timeout=HTTP_TIMEOUT, allow_redirects=False)
                break
            except Exception as exc:
                if time.monotonic() >= deadline:
                    log.warning("Couldn't test %s from inside the container (%s); "
                                "trying it with the Pixoo anyway", url, exc)
                    return True
                if not waited:
                    waited = True
                    log.info("%s isn't answering yet (%s); waiting up to %ss for it to come up",
                             url, exc, wait_seconds)
                time.sleep(SELF_CHECK_INTERVAL)
        if waited:
            log.info("%s came up after the wait", url)
        if 300 <= resp.status_code < 400:
            log.warning("%s redirects to %s, which the Pixoo can't follow (no TLS). Pushing frames instead.",
                        url, resp.headers.get("Location"))
            return False
        if resp.status_code != 200 or resp.content != self.check_body:
            log.warning("%s answered HTTP %s but not from this app (proxy routing?). Pushing frames instead.",
                        url, resp.status_code)
            return False
        log.info("%s reaches this app over plain http", self.base_url)
        return True

    def health(self):
        """Healthy while the main loop keeps checking in (it can legitimately sleep up to
        a few minutes when FlightRadar24 or the Pixoo is unavailable)."""
        age = time.monotonic() - self.heartbeat
        healthy = age < 3 * REFRESH_SECONDS + 600
        return healthy, {"status": "ok" if healthy else "stalled", "last_loop_seconds_ago": round(age),
                         "showing": self.latest is not None}

    def set_status(self, **status):
        self.status, self.status_time = status, time.time()

    def current(self):
        """Status for the web page: the flight on screen, nearby traffic, and which GIF is current."""
        age = round(time.time() - self.status_time) if self.status_time else None
        return {**self.status, "screen": self.count, "age_seconds": age}

    def publish(self, gif):
        """Store a GIF under a fresh name; returns (name, url). Keeps the last few."""
        self.count += 1
        name = f"{self.token}/{self.count}.gif"
        self.fetched[name] = threading.Event()
        self.files[name] = gif
        self.latest = name
        for old in list(self.files)[:-3]:
            self.files.pop(old, None)
            self.fetched.pop(old, None)
        return name, f"{self.base_url}/{name}"


# =============================================================================
# Helpers for FlightRadar24 data
# =============================================================================

def is_missing(value):
    return value is None or (isinstance(value, str) and value.strip() in ("", "N/A"))


def first(*values):
    return next((v for v in values if not is_missing(v)), None)


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def dig(data, *keys):
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial great-circle bearing from point 1 to point 2, 0-360 degrees from north."""
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    x = math.sin(lon2 - lon1) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return math.degrees(math.atan2(x, y)) % 360


def compass(degrees):
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[round(degrees / 45) % 8]


# Best-effort classification from the live feed (ICAO type designators, address blocks)
HELICOPTER_TYPES = frozenset("""
    R22 R44 R66 B06 B06T B407 B412 B429 B430 B505 B212 B222 B230 EC20 EC25 EC30 EC35 EC45 EC55
    EC75 H160 AS32 AS50 AS55 AS65 AS3B A109 A119 A139 A169 A189 S76 S92 S61 H47 H60 UH1 MI8 MI24
    MI26 KA32 B47G H269 H500 EXPL NH90 PUMA LYNX GAZL ALO3 EH10 V22
""".split())
MILITARY_TYPES = frozenset("""
    C17 C130 C30J C5M K35R R135 E3TF E3CF E6 P8 P3 A400 B52 B1 F16 F15 F18H F18S F35 EUFI RFAL
    TOR GRIF HAWK M346 A10 V22 H60 H47 NH90 U2 C2 Y20
""".split())
MILITARY_HEX = ((0xAE0000, 0xAFFFFF), (0x43C000, 0x43CFFF))   # US and UK military 24-bit addresses


def is_helicopter(f):
    return str(f.aircraft_code or "").upper() in HELICOPTER_TYPES


def is_military(f):
    try:
        address = int(str(f.icao_24bit), 16)
    except ValueError:
        address = None
    if address is not None and any(lo <= address <= hi for lo, hi in MILITARY_HEX):
        return True
    callsign, operator = str(f.callsign or "").lower(), str(f.airline_icao or "").lower()
    return (str(f.aircraft_code or "").upper() in MILITARY_TYPES or operator in MILITARY_CALLSIGNS
            or any(callsign.startswith(prefix) for prefix in MILITARY_CALLSIGNS))


def is_rare(f):
    return str(f.aircraft_code or "").lower() in RARE_TYPES or str(f.registration or "").lower() in RARE_REGISTRATIONS


def highlights(f):
    """("military",), ("rare",), both or neither, as far as the highlight settings allow."""
    return tuple(tag for tag, on, test in (("military", HIGHLIGHT_MILITARY, is_military), ("rare", HIGHLIGHT_RARE, is_rare))
                 if on and test(f))


# =============================================================================
# Airline logos: fetched once per airline, fitted to a 64x20 strip on black
# =============================================================================

_logos = {}  # (iata, icao) -> (image or None, expires_at)


def _download_logo(iata, icao):
    for source in LOGO_SOURCES:
        try:
            if source == "fr24" and (iata or icao):
                result = fr24.get_airline_logo(iata, icao)
                if result and result[0]:
                    return result[0]
            elif source == "avcodes" and icao:
                resp = requests.get(AVCODES_URL.format(icao=icao.upper()), timeout=HTTP_TIMEOUT)
                if resp.ok and resp.content:
                    return resp.content
        except Exception as exc:
            log.debug("Logo from %s failed for %s/%s: %s", source, iata, icao, exc)
    return None


def logo_on_black(raw, width=64, height=20):
    """Fit a logo into the strip: key out solid backgrounds, trim margins, shrink
    cleanly, firm up thin strokes, and lift logos too dark to see on black."""
    try:
        src = Image.open(BytesIO(raw)).convert("RGBA")
    except Exception:
        return None

    # Solid-background logo: make the background colour (taken from the corners) transparent
    if src.getchannel("A").getextrema()[0] == 255:
        w, h = src.width - 1, src.height - 1
        corners = [src.getpixel(p)[:3] for p in ((0, 0), (w, 0), (0, h), (w, h))]
        key = corners[0]
        if all(sum(abs(a - b) for a, b in zip(c, key)) < 30 for c in corners):
            dist = ImageChops.difference(src.convert("RGB"), Image.new("RGB", src.size, key)).convert("L")
            src.putalpha(dist.point(lambda d: 0 if d < 12 else 255 if d > 60 else (d - 12) * 255 // 48))

    bbox = src.getchannel("A").point(lambda a: 255 if a > 24 else 0).getbbox()
    if bbox:
        src = src.crop(bbox)

    scale = min(width / src.width, height / src.height)
    if scale >= 1:
        k = int(scale)
        small = src.resize((src.width * k, src.height * k), Image.Resampling.NEAREST)
    else:
        size = (max(1, round(src.width * scale)), max(1, round(src.height * scale)))
        small = src.resize(size, Image.Resampling.LANCZOS)

    # Thin strokes more solid, faint ringing removed
    small.putalpha(small.getchannel("A").point(lambda a: 0 if a < 24 else min(255, round(a * 1.5))))

    # Readable on black: black/grey ink turns light, dim logos get brightened
    px = small.load()
    levels = sorted(max(px[x, y][:3]) for x in range(small.width) for y in range(small.height) if px[x, y][3] > 128)
    typical = levels[len(levels) * 3 // 4] if levels else 255
    gain = min(3.0, 230 / max(typical, 1)) if typical < 230 else 1.0
    for y in range(small.height):
        for x in range(small.width):
            r, g, b, a = px[x, y]
            if max(r, g, b) < 80 and max(r, g, b) - min(r, g, b) < 40:
                r = g = b = 255 - max(r, g, b)
            elif gain > 1:
                r, g, b = (min(255, round(v * gain)) for v in (r, g, b))
            px[x, y] = (r, g, b, a)

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    canvas.alpha_composite(small, ((width - small.width) // 2, (height - small.height) // 2))
    return canvas.convert("RGB")


def airline_logo(iata, icao):
    key = (iata or "", icao or "")
    cached = _logos.get(key)
    if cached and cached[1] > time.time():
        return cached[0]
    raw = _download_logo(iata, icao)
    image = logo_on_black(raw) if raw else None
    _logos[key] = (image, time.time() + (7 * 86400 if image else 6 * 3600))
    return image


# =============================================================================
# Radar page: the closest airborne flight
# =============================================================================

@dataclass
class Nearby:
    flight: str
    airline: str
    airline_iata: str
    airline_icao: str
    origin: str
    destination: str
    aircraft: str
    registration: str
    altitude: int
    speed: int
    vspeed: int
    distance_km: float
    bearing: float = 0.0
    tags: tuple = ()                                # "military" and/or "rare"
    traffic: list = field(default_factory=list)     # everything else in range, for the web page


def _on_ground(f):
    if f.on_ground in (1, True, "1"):
        return True
    return number(f.altitude) in (0, None) and number(f.ground_speed) in (0, None)


def _candidates():
    """(flight, tags, distance) for everything worth showing in range, closest first."""
    bounds = fr24.get_bounds_by_point(HOME_LAT, HOME_LON, RADAR_RADIUS_KM * 1000)
    found = []
    for f in fr24.get_flights(bounds=bounds) or []:
        if number(f.latitude) is None or number(f.longitude) is None:
            continue
        if (not SHOW_GROUND_TRAFFIC and _on_ground(f)) or (IGNORE_HELICOPTERS and is_helicopter(f)):
            continue
        tags = highlights(f)
        if not tags:   # ordinary traffic: airline flights only
            if is_missing(f.airline_iata) or (SKIP_UNKNOWN_DESTINATION and is_missing(f.destination_airport_iata)):
                continue
        found.append((f, tags, haversine_km(HOME_LAT, HOME_LON, f.latitude, f.longitude)))
    return sorted(found, key=lambda c: c[2])


def _traffic_row(f, tags, distance):
    return {
        "flight": str(first(f.number, f.callsign) or "").upper(),
        "aircraft": first(f.aircraft_code),
        "origin": first(f.origin_airport_iata),
        "destination": first(f.destination_airport_iata),
        "altitude": number(f.altitude),
        "distance_km": round(distance, 1),
        "direction": compass(bearing_deg(HOME_LAT, HOME_LON, f.latitude, f.longitude)),
        "tags": list(tags),
    }


def fetch_nearest():
    candidates = _candidates()
    if not candidates:
        return None
    # Closest first; with PRIORITIZE_HIGHLIGHTS a military or rare aircraft anywhere in range wins
    f, tags, distance = min(candidates, key=lambda c: (PRIORITIZE_HIGHLIGHTS and not c[1], c[2]))

    try:
        details = fr24.get_flight_details(f) or {}
    except Exception as exc:
        log.warning("Details unavailable for %s (%s); using live feed only", f.callsign, exc)
        details = {}

    def get(*keys):
        return dig(details, *keys)

    return Nearby(
        flight=str(first(get("identification", "number", "default"), f.number, f.callsign) or "").upper(),
        airline=str(first(get("airline", "name")) or ""),
        airline_iata=first(get("airline", "code", "iata"), f.airline_iata),
        airline_icao=first(get("airline", "code", "icao"), f.airline_icao),
        origin=first(get("airport", "origin", "code", "iata"), f.origin_airport_iata),
        destination=first(get("airport", "destination", "code", "iata"), f.destination_airport_iata),
        aircraft=first(get("aircraft", "model", "code"), f.aircraft_code),
        registration=first(get("aircraft", "registration"), f.registration),
        altitude=number(f.altitude),
        speed=number(f.ground_speed),
        vspeed=number(f.vertical_speed),
        distance_km=distance,
        bearing=bearing_deg(HOME_LAT, HOME_LON, f.latitude, f.longitude),
        tags=tags,
        traffic=[_traffic_row(*c) for c in candidates[:15]],
    )


def _fmt_altitude(n):
    if n.altitude is None:
        return "---"
    if n.altitude <= 0:
        return "GND"
    alt = int(n.altitude)
    text = f"FL{alt // 100:03d}" if alt >= 10000 else f"{alt}FT"
    if n.vspeed and n.vspeed > 300:
        text += "▲"
    elif n.vspeed and n.vspeed < -300:
        text += "▼"
    return text


def _fmt_speed(n):
    return f"{int(n.speed)}KT" if n.speed else "---"


def _fmt_distance(n):
    d = n.distance_km
    return f"{d:.1f}KM" if d < 10 else f"{d:.0f}KM"


ROUTE_START, ROUTE_END, PLANE_W = 21, 43, 5
RADAR_FRAMES = ROUTE_END - ROUTE_START + PLANE_W   # 27: one full plane pass


def _draw_plane(img, x, y):
    px, col = img.load(), rgb(COLOR_PLANE)
    points = [(x + i, y + 2) for i in range(5)] + [(x + 2, y + j) for j in range(5)] + [(x, y + j) for j in (1, 2, 3)]
    for a, b in points:
        if ROUTE_START <= a < ROUTE_END:
            px[a, b] = col


def _draw_field(img, top, label, value):
    """Label (small, grey) on the left, value (large, yellow) right-aligned."""
    value = str(first(value) or "---").upper()[:7]
    draw_text(img, (2, top + 5), label, F35, COLOR_LABEL)
    draw_text(img, (62 - text_width(value, F57), top + 4), value, F57, COLOR_TEXT)


# Split-flap values: a fixed board of character tiles. Each time the info panel turns
# to its next page, the tiles that change flip through the drum, left to right.
FLAP_SLOTS = 7            # tiles per value, right-aligned like the plain values
FLAP_DRUM = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-./+:?▲▼"
FLAP_STEPS = 3            # drum positions shown per flip, the last one being the new character
FLAP_SUBFRAMES = 2        # frames per plane step, so flips run at FRAME_MS / 2
COLOR_TILE = "#151515"
COLOR_HINGE = "#000000"


def _flap_text(value):
    return str(first(value) or "---").upper()[:FLAP_SLOTS].rjust(FLAP_SLOTS)


def _flap_sequence(old, new):
    """Drum positions a tile shows on its way from old to new (empty if unchanged)."""
    if old == new:
        return []
    a, b = FLAP_DRUM.find(old), FLAP_DRUM.find(new)
    if a < 0 or b < 0:
        return [new]
    passed = [FLAP_DRUM[(a + k) % len(FLAP_DRUM)] for k in range(1, (b - a) % len(FLAP_DRUM) + 1)]
    return passed[-FLAP_STEPS:]


def _draw_glyph_rows(img, x, y, char, rows):
    """Part of a 5x7 glyph, centred in a 5-pixel tile."""
    glyph = F57.get(char, F57[" "])
    x += (5 - len(glyph[0])) // 2
    px, col = img.load(), rgb(COLOR_TEXT)
    for dy in rows:
        for dx, bit in enumerate(glyph[dy]):
            if bit == "#":
                px[x + dx, y + dy] = col


def _draw_flap_field(img, top, label, old, new, t):
    """Like _draw_field, with each value character on a tile that flips from old to new.
    t counts frames since the page turned."""
    draw_text(img, (2, top + 5), label, F35, COLOR_LABEL)
    old, new = _flap_text(old), _flap_text(new)
    y, delay = top + 4, 0
    for k in range(FLAP_SLOTS):
        x = 63 - (FLAP_SLOTS - k) * 6
        fill(img, x, y - 1, 5, 9, COLOR_TILE)
        flips = _flap_sequence(old[k], new[k])
        if SPLIT_FLAP == "digits" and not (old[k].isdigit() or new[k].isdigit()):
            flips = []                                # letters just change
        step = t - delay                              # changing tiles start one frame apart
        delay += bool(flips)
        if step < 0 or step >= len(flips):
            _draw_glyph_rows(img, x, y, old[k] if step < 0 else new[k], range(7))
        else:                                         # mid-flip: next top half over previous bottom half
            _draw_glyph_rows(img, x, y, flips[step], range(3))
            _draw_glyph_rows(img, x, y, flips[step - 1] if step else old[k], range(4, 7))
            fill(img, x, y + 3, 5, 1, COLOR_HINGE)


HIGHLIGHT_PERIOD = 9      # border and badge are on for 6 of every 9 plane steps (divides RADAR_FRAMES)


def _draw_highlight(img, tags, step):
    if not tags or step % HIGHLIGHT_PERIOD >= 6:
        return
    color, badge = (COLOR_MILITARY, "MIL") if "military" in tags else (COLOR_RARE, "RARE")
    ImageDraw.Draw(img).rectangle((0, 0, 63, 63), outline=rgb(color))
    fill(img, 0, 0, text_width(badge, F35) + 3, 7, color)
    draw_text(img, (2, 1), badge, F35, "#000000")


def radar_page():
    n = fetch_nearest()
    if n is None:
        raise LookupError("no airborne flights nearby")

    logo = airline_logo(n.airline_iata, n.airline_icao)
    origin = str(n.origin or "---").upper()[:3]
    dest = str(n.destination or "---").upper()[:3]
    panels = [
        (("FLT", n.flight), ("ALT", _fmt_altitude(n))),
        (("TYPE", n.aircraft), ("REG", n.registration)),
        (("SPD", _fmt_speed(n)), (compass(n.bearing) if SHOW_DIRECTION else "DIST", _fmt_distance(n))),
    ]
    per_panel = RADAR_FRAMES // len(panels)
    sub = FLAP_SUBFRAMES if SPLIT_FLAP != "off" else 1

    frames = []
    for i in range(RADAR_FRAMES * sub):
        step = i // sub
        img = Image.new("RGB", (64, 64))
        if logo is not None:
            img.paste(logo, (0, 0))
        else:
            name = (n.airline or n.flight).upper()[:10]
            draw_text(img, ((64 - text_width(name, F57)) // 2, 6), name, F57, COLOR_WHITE)
        dashed_line(img, 20)

        fill(img, 0, 21, 64, 11, COLOR_BOX)
        draw_text(img, (2, 23), origin, F57, COLOR_TEXT)
        draw_text(img, (62 - text_width(dest, F57), 23), dest, F57, COLOR_TEXT)
        for x in range(ROUTE_START, ROUTE_END, 3):
            fill(img, x, 26, 2, 1, COLOR_ROUTE)
        _draw_plane(img, ROUTE_START - PLANE_W + step, 24)

        dashed_line(img, 32)
        fill(img, 0, 33, 64, 31, COLOR_BOX)
        dashed_line(img, 48)
        page = min(step // per_panel, len(panels) - 1)
        upper, lower = panels[page]
        if SPLIT_FLAP != "off":
            t = i - page * per_panel * sub
            previous_upper, previous_lower = panels[page - 1]   # page 0 flips from the last page
            _draw_flap_field(img, 33, upper[0], previous_upper[1], upper[1], t)
            _draw_flap_field(img, 49, lower[0], previous_lower[1], lower[1], t - 1)
        else:
            _draw_field(img, 33, *upper)
            _draw_field(img, 49, *lower)
        _draw_highlight(img, n.tags, step)
        frames.append(img)

    summary = (f"{n.flight} {origin}->{dest} {n.aircraft or ''} {_fmt_altitude(n)} {_fmt_distance(n)} "
               f"{compass(n.bearing)}" + "".join(f" [{tag.upper()}]" for tag in n.tags))
    return frames, FRAME_MS // sub, summary, n


# =============================================================================
# Main loop
# =============================================================================

def run():
    pixoo = Pixoo(PIXOO_HOST)
    log.info("Pixoo at %s | home %.4f,%.4f | radius %skm | checking every %ss",
             PIXOO_HOST, HOME_LAT, HOME_LON, RADAR_RADIUS_KM, REFRESH_SECONDS)

    # The web server always runs (its root page shows the current screen); the Pixoo
    # only fetches from it when GIF_BASE_URL is set and reachable.
    use_url = bool(GIF_BASE_URL) and "CHANGE-ME" not in GIF_BASE_URL
    base_url = with_ip(GIF_BASE_URL) if use_url else f"http://localhost:{GIF_PORT}"
    gif_host = GifHost(base_url, GIF_PORT)
    hosted = False
    if use_url:
        log.info("Serving animations on port %s; the Pixoo fetches them from %s%s", GIF_PORT, base_url,
                 f" ({GIF_BASE_URL})" if base_url != GIF_BASE_URL else "")
        hosted = gif_host.self_check()
    else:
        log.info("GIF_BASE_URL not set: pushing frames (the Pixoo shows Loading.. on each update); "
                 "current screen on port %s", GIF_PORT)

    if 0 <= PIXOO_BRIGHTNESS <= 100:
        try:
            pixoo.set_brightness(PIXOO_BRIGHTNESS)
        except Exception as exc:
            log.warning("Could not set brightness: %s", exc)

    on_screen, failures, retry_hosted_at = None, 0, None
    while True:
        started = gif_host.heartbeat = time.monotonic()

        # A Pixoo that couldn't fetch may just have been waiting on something that has
        # since come up, so look again now and then rather than pushing frames forever.
        if use_url and not hosted and retry_hosted_at and started >= retry_hosted_at:
            hosted = gif_host.self_check(wait_seconds=0)
            retry_hosted_at = None if hosted else started + HOSTED_RETRY_SECONDS
            if hosted:
                log.info("Trying hosted GIFs again")

        try:
            frames, frame_ms, summary, n = radar_page()
            failures = 0
        except LookupError as exc:  # nothing airborne nearby: keep the last flight on screen
            log.info("%s", exc)
            gif_host.set_status(**{**gif_host.status, "traffic": [], "note": str(exc)})
            time.sleep(REFRESH_SECONDS)
            continue
        except Exception as exc:
            failures += 1
            log.warning("Flight data unavailable (%s)", exc)
            time.sleep(min(300, REFRESH_SECONDS * failures))
            continue

        flight = {k: v for k, v in asdict(n).items() if k != "traffic"}
        gif_host.set_status(flight={**flight, "direction": compass(n.bearing), "altitude_text": _fmt_altitude(n)},
                            traffic=n.traffic, mode="hosted GIF" if hosted else "pushed frames")

        # Re-uploading identical content only wears on the Pixoo
        signature = hashlib.sha1(b"".join(f.tobytes() for f in frames) + str(frame_ms).encode()).digest()
        if signature != on_screen:
            try:
                played = False
                name, url = gif_host.publish(encode_gif(frames, frame_ms))  # also feeds the browser page
                if hosted:
                    pixoo.play_gif_url(url)
                    if gif_host.fetched[name].wait(15):
                        played = True
                    else:
                        # A fetch that fails leaves the Pixoo unresponsive for a minute or so,
                        # so don't keep trying, and give it time before pushing frames.
                        hosted = False
                        retry_hosted_at = time.monotonic() + HOSTED_RETRY_SECONDS
                        log.warning("The Pixoo didn't fetch %s: it can't resolve or reach that address "
                                    "(DNS or firewall on its network?). Pushing frames; trying the URL "
                                    "again in %s minutes.", url, round(HOSTED_RETRY_SECONDS / 60))
                        time.sleep(60)
                if not played:
                    pixoo.show(frames, frame_ms)
                on_screen = signature
                log.info("Showing %s", summary)
            except Exception as exc:
                on_screen = None
                log.error("Pixoo at %s not responding (%s); retrying in 30s", PIXOO_HOST, exc)
                time.sleep(30)
                continue

        time.sleep(max(0.0, REFRESH_SECONDS - (time.monotonic() - started)))


def preview(out_dir):
    """Render the current flight to PNG files instead of sending it to the Pixoo."""
    os.makedirs(out_dir, exist_ok=True)
    try:
        frames, frame_ms, summary, _ = radar_page()
    except Exception as exc:
        print(f"Nothing to show: {exc}")
        return
    per_panel = len(frames) // 3
    for k, frame in enumerate(frames[per_panel - 1::per_panel][:3]):  # one shot per info panel, settled
        frame.resize((512, 512), Image.Resampling.NEAREST).save(os.path.join(out_dir, f"radar{k + 1}.png"))
    with open(os.path.join(out_dir, "radar.gif"), "wb") as fh:
        fh.write(encode_gif(frames, frame_ms))  # exactly what the Pixoo would play
    print(summary)


def main():
    parser = argparse.ArgumentParser(description="Closest-flight radar for the Divoom Pixoo64")
    parser.add_argument("--preview", metavar="DIR", help="render the current flight to PNGs in DIR and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    required = ("HOME_LAT", "HOME_LON") if args.preview else ("PIXOO_HOST", "HOME_LAT", "HOME_LON")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        sys.exit(f"Missing required setting(s): {', '.join(missing)}. Set them as environment variables, "
                 "e.g. PIXOO_HOST=192.168.1.50 HOME_LAT=51.4700 HOME_LON=-0.4543")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # PID 1 in Docker ignores SIGTERM otherwise

    try:
        preview(args.preview) if args.preview else run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
