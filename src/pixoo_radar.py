#!/usr/bin/env python3
"""
Pixoo Radar for the Divoom Pixoo64
==================================

Shows the airborne flight closest to home: airline logo, route with an
animated plane, and a split-flap panel cycling flight number, altitude,
aircraft type, registration, speed and distance. Checks for a new closest
flight every REFRESH_SECONDS.

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
from dataclasses import dataclass
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

REFRESH_SECONDS = _setting("REFRESH_SECONDS", 60)          # how often to look for the closest flight
FRAME_MS = _setting("FRAME_MS", 300)                         # animation speed

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
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="30">
<title>Pixoo Radar</title>
<style>html,body{margin:0;height:100%;background:#000;display:grid;place-items:center}
img{width:min(90vw,90vh);image-rendering:pixelated}</style>
<img src="latest.gif" alt="What the Pixoo is showing">
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
        host = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self, with_body):
                path, status = self.path.split("?")[0].lstrip("/"), 200
                if path == "":
                    data, ctype = INDEX_HTML.encode(), "text/html; charset=utf-8"
                elif path == f"{host.token}/check":
                    data, ctype = host.check_body, "text/plain"
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

    def self_check(self):
        """Fetch our own address the way the Pixoo will. False only if it's definitely broken."""
        url = f"{self.base_url}/{self.token}/check"
        try:
            resp = requests.get(url, timeout=HTTP_TIMEOUT, allow_redirects=False)
        except Exception as exc:
            log.warning("Couldn't test %s from inside the container (%s); trying it with the Pixoo anyway", url, exc)
            return True
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


def _on_ground(f):
    if f.on_ground in (1, True, "1"):
        return True
    return number(f.altitude) in (0, None) and number(f.ground_speed) in (0, None)


def fetch_nearest():
    bounds = fr24.get_bounds_by_point(HOME_LAT, HOME_LON, RADAR_RADIUS_KM * 1000)
    candidates = [
        f for f in (fr24.get_flights(bounds=bounds) or [])
        if not is_missing(f.airline_iata)                       # has a flight number
        and number(f.latitude) is not None and number(f.longitude) is not None
        and (SHOW_GROUND_TRAFFIC or not _on_ground(f))
    ]
    if not candidates:
        return None
    f = min(candidates, key=lambda c: haversine_km(HOME_LAT, HOME_LON, c.latitude, c.longitude))

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
        distance_km=haversine_km(HOME_LAT, HOME_LON, f.latitude, f.longitude),
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
        (("SPD", _fmt_speed(n)), ("DIST", _fmt_distance(n))),
    ]
    per_panel = RADAR_FRAMES // len(panels)

    frames = []
    for i in range(RADAR_FRAMES):
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
        _draw_plane(img, ROUTE_START - PLANE_W + i, 24)

        dashed_line(img, 32)
        fill(img, 0, 33, 64, 31, COLOR_BOX)
        dashed_line(img, 48)
        upper, lower = panels[min(i // per_panel, len(panels) - 1)]
        _draw_field(img, 33, *upper)
        _draw_field(img, 49, *lower)
        frames.append(img)

    summary = f"{n.flight} {origin}->{dest} {n.aircraft or ''} {_fmt_altitude(n)} {_fmt_distance(n)}"
    return frames, FRAME_MS, summary


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

    on_screen, failures = None, 0
    while True:
        started = gif_host.heartbeat = time.monotonic()
        try:
            frames, frame_ms, summary = radar_page()
            failures = 0
        except LookupError as exc:  # nothing airborne nearby: keep the last flight on screen
            log.info("%s", exc)
            time.sleep(REFRESH_SECONDS)
            continue
        except Exception as exc:
            failures += 1
            log.warning("Flight data unavailable (%s)", exc)
            time.sleep(min(300, REFRESH_SECONDS * failures))
            continue

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
                        log.warning("The Pixoo didn't fetch %s: it can't resolve or reach that address "
                                    "(DNS or firewall on its network?). Pushing frames until restart.", url)
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
        frames, _, summary = radar_page()
    except Exception as exc:
        print(f"Nothing to show: {exc}")
        return
    for k, frame in enumerate(frames[::RADAR_FRAMES // 3][:3]):  # one shot per info panel
        frame.resize((512, 512), Image.Resampling.NEAREST).save(os.path.join(out_dir, f"radar{k + 1}.png"))
    with open(os.path.join(out_dir, "radar.gif"), "wb") as fh:
        fh.write(encode_gif(frames, FRAME_MS))  # exactly what the Pixoo would play
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
