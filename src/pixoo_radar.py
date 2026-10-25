#!/usr/bin/env python3
"""
Pixoo Radar for the Divoom Pixoo64
==================================

Shows the airborne flight closest to home: airline logo, route with an
animated plane, and a panel cycling flight number, altitude, aircraft type,
registration, speed, distance and direction (optionally on split-flap tiles).
Military and rare aircraft are highlighted and shown first. Checks for a new
closest flight every REFRESH_SECONDS; the built-in web server shows it live.
Optionally logs every aircraft seen (SQLite or CSV) and publishes to MQTT.

With GIF_BASE_URL set, each animation is served as a GIF from a small built-in
web server and the Pixoo fetches and plays it itself (Device/PlayTFGif), so the
picture swaps in without the Pixoo's "Loading.." screen. Without it, frames are
pushed one by one over the Pixoo's API, which shows "Loading.." on every update.

Install:   pip install -r requirements.txt
Run:       PIXOO_HOST=192.168.1.50 HOME_LAT=51.4700 HOME_LON=-0.4543 python pixoo_radar.py
Preview:   HOME_LAT=51.4700 HOME_LON=-0.4543 python pixoo_radar.py --preview ./preview
           (renders PNGs and the GIF, no Pixoo needed)
Health:    GET /health on WEB_PORT -> 200 while the main loop is running, 503 if it stalls

Every setting below can be overridden with an environment variable of the same
name, e.g.  PIXOO_HOST=192.168.1.50 REFRESH_SECONDS=90 python pixoo_radar.py
PIXOO_HOST, HOME_LAT and HOME_LON are required.

Inspired by chrivoge/PixooRadar. Flight data comes from FlightRadar24's public
web endpoints via the unofficial FlightRadarAPI package (so keep it to personal use),
or from adsb.lol, your own ADS-B receiver or OpenSky, with details from adsbdb.com.
"""

import argparse
import base64
import calendar
import csv
import hashlib
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import signal
import socket
import sqlite3
import struct
import subprocess
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

SETTING_PROBLEMS = []     # bad values, reported together at start-up (and by --check)
RENAMED_SETTINGS = []     # (old, new) names still in use, logged as a warning


def _setting(name, default, old_name=None):
    raw = os.environ.get(name, "").strip()
    if not raw and old_name and os.environ.get(old_name, "").strip():
        raw = os.environ[old_name].strip()
        RENAMED_SETTINGS.append((old_name, name))
        name = old_name
    if not raw:
        return default
    if isinstance(default, bool):
        if raw.lower() in ("1", "true", "yes", "on"):
            return True
        if raw.lower() not in ("0", "false", "no", "off"):
            SETTING_PROBLEMS.append(f"{name}={raw!r}: use true or false")
        return False if raw.lower() in ("0", "false", "no", "off") else default
    try:
        if isinstance(default, int):
            return int(raw)
        if isinstance(default, float):
            return float(raw)
    except ValueError:
        SETTING_PROBLEMS.append(f"{name}={raw!r} isn't a valid {'whole number' if isinstance(default, int) else 'number'}")
        return default
    if isinstance(default, tuple):
        return tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    return raw


PIXOO_HOST = _setting("PIXOO_HOST", "")                    # IP or hostname of the Pixoo (required)
PIXOO_BRIGHTNESS = _setting("PIXOO_BRIGHTNESS", -1)        # 0-100, or -1 to leave it alone

HOME_LAT = _setting("HOME_LAT", 0.0)                       # your location (required)
HOME_LON = _setting("HOME_LON", 0.0)
RADAR_RADIUS_KM = _setting("RADAR_RADIUS_KM", 100)
SHOW_GROUND_TRAFFIC = _setting("SHOW_GROUND_TRAFFIC", False)  # include parked/taxiing aircraft

# Where positions come from, tried in order: fr24, adsb (adsb.lol or your own receiver), opensky
DATA_SOURCE = _setting("DATA_SOURCE", ("fr24",))
ADSB_URL = _setting("ADSB_URL", "https://api.adsb.lol/v2/point/{lat}/{lon}/{radius_nm}")  # or .../aircraft.json
LOOKUP_ADSBDB = _setting("LOOKUP_ADSBDB", True)   # route, airline, type from adsbdb.com (adsb/opensky)
OPENSKY_CLIENT_ID = _setting("OPENSKY_CLIENT_ID", "")         # optional OpenSky API client:
OPENSKY_CLIENT_SECRET = _setting("OPENSKY_CLIENT_SECRET", "")  # higher rate limit
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
SHOW_BEARING = _setting("SHOW_BEARING", True, "SHOW_DIRECTION")  # compass point of the plane in place of "DIST"
SHOW_HEADING = _setting("SHOW_HEADING", True)                # arrow after the speed: the way it's flying
# Flight values on flipping departures-board tiles: off, all (every character) or digits
SPLIT_FLAP = {"1": "all", "true": "all", "yes": "all", "on": "all", "all": "all", "digits": "digits",
              "0": "off", "false": "off", "no": "off", "off": "off"}.get(_setting("SPLIT_FLAP", "off").lower())

LOGO_SOURCES = _setting("LOGO_SOURCES", ("fr24", "avcodes"))  # tried in order

# Address where the Pixoo can reach this app's GIF server, e.g. http://pixooradar.lan:8765
# (plain http; the Pixoo won't fetch https or follow a redirect to it). A hostname is
# resolved here and handed to the Pixoo as an IP. Leave empty to push frames instead.
# Opening the server's root in a browser shows what the Pixoo is showing.
GIF_BASE_URL = _setting("GIF_BASE_URL", "")
WEB_PORT = _setting("WEB_PORT", 8765, "GIF_PORT")   # the built-in web server: GIFs, live page, API, /health

COLOR_TEXT = _setting("COLOR_TEXT", "#FFFF00")    # flight details
COLOR_BOX = _setting("COLOR_BOX", "#2B2B2B")      # panels behind text
COLOR_LABEL = "#999999"
COLOR_SEPARATOR = "#555555"
COLOR_ROUTE = "#666666"
COLOR_PLANE = "#FFFFFF"
COLOR_WHITE = "#FFFFFF"

# Log of every aircraft that passed through the radius: off, sqlite or csv, kept for N days
HISTORY = _setting("HISTORY", "off").lower()
HISTORY_DIR = _setting("HISTORY_DIR", "data")
HISTORY_RETENTION_DAYS = _setting("HISTORY_RETENTION_DAYS", 30)
HISTORY_TRIM_HOURS = _setting("HISTORY_TRIM_HOURS", 1)        # how often old entries are removed

# Optional MQTT (Home Assistant discovery included); needs MQTT_HOST
MQTT_HOST = _setting("MQTT_HOST", "")
MQTT_PORT = _setting("MQTT_PORT", 1883)
MQTT_USERNAME = _setting("MQTT_USERNAME", "")
MQTT_PASSWORD = _setting("MQTT_PASSWORD", "")
MQTT_TLS = _setting("MQTT_TLS", False)
MQTT_TOPIC = _setting("MQTT_TOPIC", "pixoo_radar")
MQTT_DISCOVERY = _setting("MQTT_DISCOVERY", True)
MQTT_DISCOVERY_PREFIX = _setting("MQTT_DISCOVERY_PREFIX", "homeassistant")

HTTP_TIMEOUT = (5, 20)        # connect, read (seconds)
GIF_RESET_EVERY = 32          # fresh animation-id counter every N uploads (Pixoo freezes otherwise)
SELF_CHECK_WAIT = _setting("SELF_CHECK_WAIT", 120)   # how long our own address may take to start
SELF_CHECK_INTERVAL = 5                              # answering (Swarm routes to healthy tasks only)
HOSTED_RETRY_SECONDS = _setting("HOSTED_RETRY_SECONDS", 1800)  # re-try hosted GIFs this often
AVCODES_URL = "https://raw.githubusercontent.com/Jxck-S/airline-logos/main/avcodes_banners/{icao}.png"
KNOWN_SOURCES = ("fr24", "adsb", "opensky")
KNOWN_LOGO_SOURCES = ("fr24", "avcodes", "none")


def _check_settings():
    """Add a message to SETTING_PROBLEMS for every value that can't work."""
    global SPLIT_FLAP
    problems = SETTING_PROBLEMS

    def need(ok, message):
        if not ok:
            problems.append(message)

    if SPLIT_FLAP is None:
        problems.append(f"SPLIT_FLAP={os.environ.get('SPLIT_FLAP')!r}: use off, all or digits")
        SPLIT_FLAP = "off"
    need(-90 <= HOME_LAT <= 90, f"HOME_LAT={HOME_LAT}: must be between -90 and 90")
    need(-180 <= HOME_LON <= 180, f"HOME_LON={HOME_LON}: must be between -180 and 180")
    need(1 <= RADAR_RADIUS_KM <= 1000, f"RADAR_RADIUS_KM={RADAR_RADIUS_KM}: must be between 1 and 1000")
    need(REFRESH_SECONDS >= 10, f"REFRESH_SECONDS={REFRESH_SECONDS}: must be at least 10")
    need(20 <= FRAME_MS <= 10000, f"FRAME_MS={FRAME_MS}: must be between 20 and 10000")
    need(PIXOO_BRIGHTNESS == -1 or 0 <= PIXOO_BRIGHTNESS <= 100,
         f"PIXOO_BRIGHTNESS={PIXOO_BRIGHTNESS}: use 0-100, or -1 to leave it alone")
    for name in ("COLOR_TEXT", "COLOR_BOX", "COLOR_MILITARY", "COLOR_RARE"):
        value = globals()[name]
        need(re.fullmatch(r"#?[0-9A-Fa-f]{6}", value), f"{name}={value!r}: use a colour like #FFAA00")
    for name in ("WEB_PORT", "MQTT_PORT"):
        need(1 <= globals()[name] <= 65535, f"{name}={globals()[name]}: must be a port number (1-65535)")
    need(DATA_SOURCE, "DATA_SOURCE is empty: use fr24, adsb or opensky")
    problems.extend(f"DATA_SOURCE: unknown source {name!r} (use fr24, adsb or opensky)"
                    for name in DATA_SOURCE if name not in KNOWN_SOURCES)
    problems.extend(f"LOGO_SOURCES: unknown source {name!r} (use fr24, avcodes or none)"
                    for name in LOGO_SOURCES if name not in KNOWN_LOGO_SOURCES)
    need(re.match(r"https?://", ADSB_URL), f"ADSB_URL={ADSB_URL!r}: must start with http:// or https://")
    if GIF_BASE_URL and "CHANGE-ME" not in GIF_BASE_URL:
        if GIF_BASE_URL.lower().startswith("https://"):
            problems.append(f"GIF_BASE_URL={GIF_BASE_URL!r}: the Pixoo can't fetch https; "
                            "use the app's own http:// address")
        else:
            need(re.match(r"http://[^/:]+", GIF_BASE_URL),
                 f"GIF_BASE_URL={GIF_BASE_URL!r}: must look like http://192.168.1.20:{WEB_PORT}")
    need(HISTORY in ("off", "sqlite", "csv"), f"HISTORY={HISTORY!r}: use off, sqlite or csv")
    need(HISTORY_RETENTION_DAYS >= 0, f"HISTORY_RETENTION_DAYS={HISTORY_RETENTION_DAYS}: must be 0 (keep all) or more")
    need(HISTORY_TRIM_HOURS >= 0, f"HISTORY_TRIM_HOURS={HISTORY_TRIM_HOURS}: must be 0 (never) or more")
    need(SELF_CHECK_WAIT >= 0 and HOSTED_RETRY_SECONDS >= 60,
         "SELF_CHECK_WAIT must be 0 or more and HOSTED_RETRY_SECONDS at least 60")
    need(bool(OPENSKY_CLIENT_ID) == bool(OPENSKY_CLIENT_SECRET),
         "OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET go together: set both or neither")


_check_settings()


def _version():
    """The release this is: a VERSION file next to the script (Docker, install.sh),
    $PIXOO_RADAR_VERSION, or `git describe` in a checkout."""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "VERSION"), encoding="utf-8") as fh:
            found = fh.read().strip()
        if found:
            return found.lstrip("v")
    except OSError:
        pass
    if os.environ.get("PIXOO_RADAR_VERSION"):
        return os.environ["PIXOO_RADAR_VERSION"].lstrip("v")
    try:
        out = subprocess.run(["git", "describe", "--tags", "--always", "--dirty"], cwd=here, capture_output=True,
                             text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().lstrip("v")
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


__version__ = _version()

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
    "↑": "..#.. .###. #.#.# ..#.. ..#.. ..#.. ..#..", "↓": "..#.. ..#.. ..#.. ..#.. #.#.# .###. ..#..",
    "→": "..... ..#.. ...#. ##### ...#. ..#.. .....", "←": "..... ..#.. .#... ##### .#... ..#.. .....",
    "↗": "..... ..### ...## ..#.# .#... #.... .....", "↘": "..... #.... .#... ..#.# ...## ..### .....",
    "↙": "..... ....# ...#. #.#.. ##... ###.. .....", "↖": "..... ###.. ##... #.#.. ...#. ....# .....",
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


def _gif_image_block(image, left, top):
    """GIF image descriptor plus LZW data for one paletted image, compressed by Pillow."""
    buf = BytesIO()
    image.save(buf, format="GIF", optimize=False, interlace=False)
    data = buf.getvalue()
    i = 13 + (3 << ((data[10] & 7) + 1) if data[10] & 0x80 else 0)   # past header and global colour table
    while data[i] == 0x21:                                            # past any extensions
        i += 2
        while data[i]:
            i += data[i] + 1
        i += 1
    packed = data[i + 9]
    body = data[i + 10 + (3 << ((packed & 7) + 1) if packed & 0x80 else 0):-1]   # LZW data, without the trailer
    return struct.pack("<BHHHHB", 0x2C, left, top, image.width, image.height, 0) + body


def encode_gif(frames, frame_ms):
    """Encode frames the way the Pixoo plays hosted GIFs cleanly: one global palette,
    no transparency, later frames cropped to what changed (disposal 1), one delay, looping.

    Written by hand because Pillow merges repeated frames into one with a longer delay;
    a repeat here is a 1x1 frame that changes nothing, so every frame keeps the same delay."""
    strip = Image.new("RGB", (64, 64 * len(frames)))
    for i, frame in enumerate(frames):
        strip.paste(frame, (0, 64 * i))
    palette = strip.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    used = palette.getpalette()[:768]
    bits = max(0, (len(used) // 3 - 1).bit_length() - 1)             # table holds 2 << bits colours
    colors = bytes(used).ljust(3 * (2 << bits), b"\0")
    delay = max(1, round(frame_ms / 10))   # GIF delays are in hundredths of a second

    out = BytesIO()
    out.write(b"GIF89a" + struct.pack("<HHBBB", 64, 64, 0xF0 | bits, 0, 0) + colors)   # global colour table
    out.write(b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00")              # loop forever
    previous = None
    for frame in frames:
        frame = frame.convert("RGB")
        if previous is None:
            box = (0, 0, 64, 64)
        else:
            box = ImageChops.difference(frame, previous).getbbox() or (0, 0, 1, 1)
        previous = frame
        indexed = frame.crop(box).quantize(palette=palette, dither=Image.Dither.NONE)
        out.write(struct.pack("<BBBBHBB", 0x21, 0xF9, 4, 0x04, delay, 0, 0))   # disposal 1, no transparency
        out.write(_gif_image_block(indexed, box[0], box[1]))
    out.write(b"\x3b")
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
tr.pick{cursor:pointer}tr.pick:hover td,tr.pick:focus td{background:#1f1f23;outline:none}
.tag.pin{background:#fff}button{font:inherit;color:#000;background:var(--accent);border:0;border-radius:8px;
padding:6px 12px;margin-top:12px;cursor:pointer}
.scroll{overflow-x:auto}.dim{color:var(--dim);font-size:13px}
.stats{display:flex;gap:20px;flex-wrap:wrap;margin:6px 0 10px;font-size:22px}.stats .dim{font-size:14px}
</style>
<main>
  <div class="card"><img id="screen" src="latest.gif" alt="What the Pixoo is showing"></div>
  <div class="card" id="flight"><p class="dim">Waiting for the first flight…</p></div>
  <div class="card wide" id="today" hidden></div>
  <div class="card wide"><div class="dim" style="margin-bottom:8px">Nearby traffic · click a flight to show it
    on the Pixoo, click it again to go back to automatic</div>
    <div class="scroll"><table id="traffic"></table></div></div>
  <p class="dim wide" id="updated"></p>
</main>
<script>
const $ = (tag, text, cls) => { const e = document.createElement(tag);
  if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const km = d => d == null ? "" : (d < 10 ? d.toFixed(1) : Math.round(d)) + " km";
const ft = a => a == null ? "" : a <= 0 ? "ground" : a.toLocaleString() + " ft";
const arrows = "↑↗→↘↓↙←↖";
const tags = (el, list) => (list || []).forEach(t => el.append($("span", t === "military" ? "MIL" : "RARE", "tag " + t)));
let shown = null;
function render(s) {
  if (s.screen !== shown) { shown = s.screen; document.getElementById("screen").src = "latest.gif?" + s.screen; }
  const box = document.getElementById("flight"), f = s.flight;
  if (f) {
    box.replaceChildren();
    const h = $("h1", f.flight || "?"); tags(h, f.tags);
    if (f.pinned) h.append($("span", "PINNED", "tag pin"));
    box.append(h);
    box.append($("div", f.airline || "", "airline"));
    box.append($("div", (f.origin || "---") + " → " + (f.destination || "---"), "route"));
    const dl = $("dl");
    for (const [k, v] of [["Altitude", f.altitude_text], ["Speed", f.speed ? f.speed + " kt" : ""],
                          ["Distance", km(f.distance_km) + " " + f.direction], ["Heading", f.heading_text],
                          ["Aircraft", f.aircraft],
                          ["Registration", f.registration]]) { dl.append($("dt", k), $("dd", v || "—")); }
    box.append(dl);
  }
  if (s.pinned) {
    const auto = $("button", "Back to auto");
    auto.onclick = () => pick(null);
    if (!f || !f.pinned) box.append($("p", "Waiting for " + s.pinned.flight + "…", "dim"));
    box.append(auto);
  }
  const t = document.getElementById("traffic"); t.replaceChildren();
  const head = $("tr");
  ["Flight", "Type", "Route", "Altitude", "Distance", "", "Heading"].forEach(c => head.append($("th", c)));
  t.append(head);
  for (const r of s.traffic || []) {
    const pinnedRow = s.pinned && (s.pinned.hex ? s.pinned.hex === r.hex : s.pinned.callsign === r.callsign);
    const tr = $("tr", null, "pick" + (f && r.flight === f.flight ? " on" : ""));
    tr.tabIndex = 0;
    tr.title = pinnedRow ? "Click to go back to automatic" : "Click to show " + r.flight + " on the Pixoo";
    tr.onclick = () => pick(pinnedRow ? null : r);
    tr.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); tr.onclick(); } };
    const name = $("td", r.flight); tags(name, r.tags); if (pinnedRow) name.append($("span", "PINNED", "tag pin"));
    tr.append(name, $("td", r.aircraft || ""), $("td", (r.origin || "") + "–" + (r.destination || "")),
              $("td", ft(r.altitude)), $("td", km(r.distance_km)), $("td", r.direction),
              $("td", r.heading == null ? "" : arrows[Math.round(r.heading / 45) % 8] + " " + Math.round(r.heading) + "°"));
    t.append(tr);
  }
  if (!(s.traffic || []).length) { const tr = $("tr"); tr.append($("td", s.note || "No traffic yet")); t.append(tr); }
  const today = document.getElementById("today"), st = s.stats;
  today.hidden = !st;
  if (st) {
    today.replaceChildren($("div", "Seen today", "dim"));
    const nums = $("div", null, "stats");
    for (const [k, v] of [["aircraft", st.today], ["military", st.military_today], ["rare", st.rare_today]]) {
      const b = $("span"); b.append($("strong", String(v)), $("span", " " + k + "  ", "dim")); nums.append(b);
    }
    today.append(nums);
    for (const r of st.recent_highlights || []) {
      const line = $("div", (r.flight || r.callsign || r.hex) + " · " + (r.aircraft || "?") + " · " +
                    km(r.closest_km) + " · " + (r.last_seen || "").slice(11, 16) + " UTC", "dim");
      tags(line, r.tags); today.append(line);
    }
  }
  document.getElementById("updated").textContent = s.age_seconds == null ? "Starting…" :
    "Updated " + s.age_seconds + " s ago · " + (s.mode || "") + (s.source ? " · data: " + s.source : "") +
    " · Pixoo Radar " + (s.version || "");
}
async function pick(r) {
  try {
    await fetch("api/pin", r ? {method: "POST", headers: {"Content-Type": "application/json"},
                                body: JSON.stringify({hex: r.hex, callsign: r.callsign, flight: r.flight})}
                             : {method: "DELETE"});
  } catch (e) {}
  tick(); setTimeout(tick, 2000); setTimeout(tick, 5000);
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
        self.pin = None                    # {"hex", "callsign", "flight", "since"} picked on the web page
        self.wake = threading.Event()      # set to cut the main loop's wait short
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
                elif path == "api/history":
                    query = dict(p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)
                    limit = int(query["limit"]) if query.get("limit", "").isdigit() else 100
                    rows = history.entries(limit=min(limit, 5000)) if history else []
                    data, ctype = json.dumps(rows).encode(), "application/json"
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

            def _json(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                """POST /api/pin {"hex": ..., "callsign": ...}: show this aircraft instead of the automatic pick."""
                if self.path.split("?")[0] != "/api/pin":
                    return self.send_error(404)
                # JSON only: a browser won't send that cross-site without asking first (no CORS here)
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    return self._json(415, {"error": "send JSON"})
                try:
                    body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 4096)))
                    pin = host.set_pin(body.get("hex"), body.get("callsign"), body.get("flight"))
                except (ValueError, AttributeError) as exc:
                    return self._json(400, {"error": str(exc)})
                self._json(200, {"pinned": pin})

            def do_DELETE(self):
                """DELETE /api/pin: back to the automatic pick."""
                if self.path.split("?")[0] != "/api/pin":
                    return self.send_error(404)
                host.clear_pin("unpinned on the web page")
                self._json(200, {"pinned": None})

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
                         "showing": self.latest is not None, "version": __version__}

    def set_pin(self, hex_, callsign, flight=None):
        hex_ = str(hex_ or "").strip().lower()
        callsign = str(callsign or "").strip().upper()
        if hex_ and not re.fullmatch(r"[0-9a-f]{6}", hex_):
            raise ValueError("hex must be a 6-digit aircraft address")
        if callsign and not re.fullmatch(r"[A-Z0-9-]{1,10}", callsign):
            raise ValueError("callsign must be letters and digits")
        if not (hex_ or callsign):
            raise ValueError("give the aircraft's hex address or callsign")
        self.pin = {"hex": hex_, "callsign": callsign, "flight": str(flight or callsign or hex_)[:12],
                    "since": _iso(time.time())}
        log.info("Pinned %s from the web page", self.pin["flight"])
        self.wake.set()
        return self.pin

    def clear_pin(self, reason):
        if self.pin is not None:
            log.info("Back to automatic: %s %s", self.pin["flight"], reason)
            self.pin = None
            self.wake.set()

    def wait(self, seconds):
        """Sleep, unless the web page changes what's pinned."""
        self.wake.wait(max(0.0, seconds))
        self.wake.clear()

    def set_status(self, **status):
        self.status, self.status_time = status, time.time()

    def current(self):
        """Status for the web page: the flight on screen, nearby traffic, and which GIF is current."""
        age = round(time.time() - self.status_time) if self.status_time else None
        return {**self.status, "screen": self.count, "age_seconds": age, "pinned": self.pin, "version": __version__}

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
# Flight data: sources, classification and lookups
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


def arrow(degrees):
    """Arrow pointing the way a plane on this heading is flying (north up)."""
    return "↑↗→↘↓↙←↖"[round(degrees / 45) % 8]


USER_AGENT = {"User-Agent": f"pixoo-radar/{__version__} (+https://github.com/haythamelkhoja/pixoo-radar-enhanced)"}
AIRLINE_CALLSIGN = re.compile(r"^[A-Z]{3}\d[A-Z0-9]{0,4}$")   # e.g. UAE29, BAW135, EZY97ND


@dataclass
class Aircraft:
    """One aircraft from the live feed, whichever source it came from."""
    source: str
    hex: str                      # 24-bit ICAO address, lower-case hex
    callsign: str
    latitude: float
    longitude: float
    altitude: int = None          # feet
    speed: int = None             # knots
    vspeed: int = None            # feet per minute
    heading: float = None         # track over the ground, degrees from north
    on_ground: bool = False
    flight: str = None            # flight number, e.g. EK29
    airline_flight: bool = False  # flown by an airline (has a flight number or an airline callsign)
    airline: str = None
    airline_iata: str = None
    airline_icao: str = None
    aircraft: str = None          # ICAO type designator, e.g. A388
    registration: str = None
    origin: str = None            # IATA airport codes
    destination: str = None
    military: bool = False        # flagged as military by the source itself
    rotorcraft: bool = False      # flagged as a helicopter by the source itself
    ref: object = None            # the source's own record, for enrich()


# Best-effort classification (ICAO type designators, address blocks, callsigns)
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


def is_helicopter(a):
    return a.rotorcraft or str(a.aircraft or "").upper() in HELICOPTER_TYPES


def is_military(a):
    if a.military:
        return True
    try:
        address = int(a.hex, 16)
    except (TypeError, ValueError):
        address = None
    if address is not None and any(lo <= address <= hi for lo, hi in MILITARY_HEX):
        return True
    callsign, operator = str(a.callsign or "").lower(), str(a.airline_icao or "").lower()
    return (str(a.aircraft or "").upper() in MILITARY_TYPES or operator in MILITARY_CALLSIGNS
            or any(callsign.startswith(prefix) for prefix in MILITARY_CALLSIGNS))


def is_rare(a):
    return (str(a.aircraft or "").lower() in RARE_TYPES
            or str(a.registration or "").lower().replace("-", "") in {r.replace("-", "") for r in RARE_REGISTRATIONS})


def highlights(a):
    """("military",), ("rare",), both or neither, as far as the highlight settings allow."""
    return tuple(tag for tag, on, test in (("military", HIGHLIGHT_MILITARY, is_military), ("rare", HIGHLIGHT_RARE, is_rare))
                 if on and test(a))


class FR24Source:
    """FlightRadar24 via the unofficial FlightRadarAPI package: positions, routes, types."""
    name = "fr24"

    def fetch(self):
        bounds = fr24.get_bounds_by_point(HOME_LAT, HOME_LON, RADAR_RADIUS_KM * 1000)
        return [self.aircraft(f) for f in fr24.get_flights(bounds=bounds) or []
                if number(f.latitude) is not None and number(f.longitude) is not None]

    @staticmethod
    def aircraft(f):
        on_ground = f.on_ground in (1, True, "1") or (
            number(f.altitude) in (0, None) and number(f.ground_speed) in (0, None))
        return Aircraft(
            source="fr24", hex=str(first(f.icao_24bit) or "").lower(), callsign=str(first(f.callsign) or "").upper(),
            latitude=f.latitude, longitude=f.longitude, altitude=number(f.altitude), speed=number(f.ground_speed),
            vspeed=number(f.vertical_speed), heading=number(f.heading), on_ground=on_ground,
            flight=first(f.number), airline_flight=not is_missing(f.airline_iata),
            airline_iata=first(f.airline_iata), airline_icao=first(f.airline_icao), aircraft=first(f.aircraft_code),
            registration=first(f.registration), origin=first(f.origin_airport_iata),
            destination=first(f.destination_airport_iata), ref=f,
        )

    def enrich(self, a):
        try:
            details = fr24.get_flight_details(a.ref) or {}
        except Exception as exc:
            log.warning("Details unavailable for %s (%s); using live feed only", a.callsign, exc)
            details = {}

        def get(*keys):
            return dig(details, *keys)

        a.flight = first(get("identification", "number", "default"), a.flight, a.callsign)
        a.airline = first(get("airline", "name"))
        a.airline_iata = first(get("airline", "code", "iata"), a.airline_iata)
        a.airline_icao = first(get("airline", "code", "icao"), a.airline_icao)
        a.origin = first(get("airport", "origin", "code", "iata"), a.origin)
        a.destination = first(get("airport", "destination", "code", "iata"), a.destination)
        a.aircraft = first(get("aircraft", "model", "code"), a.aircraft)
        a.registration = first(get("aircraft", "registration"), a.registration)


class ADSBSource:
    """Any readsb-style JSON feed: adsb.lol (default), or your own receiver's aircraft.json."""
    name = "adsb"

    def fetch(self):
        url = ADSB_URL.format(lat=HOME_LAT, lon=HOME_LON, radius_km=RADAR_RADIUS_KM,
                              radius_nm=min(250, math.ceil(RADAR_RADIUS_KM / 1.852)))
        resp = requests.get(url, timeout=HTTP_TIMEOUT, headers=USER_AGENT)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("ac", data.get("aircraft")) if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError(f"{url} didn't return a readsb aircraft list")
        return [a for a in map(self.aircraft, rows) if a is not None]

    @staticmethod
    def aircraft(r):
        if not isinstance(r, dict):
            return None
        lat, lon = number(r.get("lat")), number(r.get("lon"))
        if lat is None or lon is None:
            return None
        callsign = str(r.get("flight") or "").strip().upper()
        airline_style = bool(AIRLINE_CALLSIGN.match(callsign))
        ground = r.get("alt_baro") == "ground"
        altitude = 0 if ground else first(number(r.get("alt_baro")), number(r.get("alt_geom")))
        flags = number(r.get("dbFlags")) or 0
        return Aircraft(
            source="adsb", hex=str(r.get("hex") or "").lstrip("~").lower(), callsign=callsign,
            latitude=lat, longitude=lon, altitude=altitude, speed=number(r.get("gs")),
            vspeed=first(number(r.get("baro_rate")), number(r.get("geom_rate"))),
            heading=first(number(r.get("track")), number(r.get("true_heading"))), on_ground=ground,
            airline_flight=airline_style, airline_icao=callsign[:3] if airline_style else None,
            aircraft=first(r.get("t")), registration=first(r.get("r")),
            military=bool(int(flags) & 1), rotorcraft=r.get("category") == "A7",
        )

    def enrich(self, a):
        lookup_adsbdb(a)


class OpenSkySource:
    """OpenSky Network state vectors: positions only, so the rest comes from adsbdb.
    Anonymous use allows ~400 requests a day, so it's polled at most every 4 minutes
    (every 30 s with OPENSKY_CLIENT_ID/SECRET from an OpenSky account)."""
    name = "opensky"
    URL = "https://opensky-network.org/api/states/all"
    TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"

    def __init__(self):
        self.token, self.token_expires = None, 0.0
        self.cached, self.fetched_at = None, 0.0

    @property
    def min_interval(self):
        return 30 if OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET else 240

    def _headers(self):
        if not (OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET):
            return dict(USER_AGENT)
        if self.token is None or time.time() >= self.token_expires - 60:
            resp = requests.post(self.TOKEN_URL, timeout=HTTP_TIMEOUT, data={
                "grant_type": "client_credentials", "client_id": OPENSKY_CLIENT_ID,
                "client_secret": OPENSKY_CLIENT_SECRET})
            resp.raise_for_status()
            body = resp.json()
            self.token, self.token_expires = body["access_token"], time.time() + float(body.get("expires_in", 1800))
        return {**USER_AGENT, "Authorization": f"Bearer {self.token}"}

    def fetch(self):
        if self.cached is not None and time.monotonic() - self.fetched_at < self.min_interval:
            return self.cached
        dlat = RADAR_RADIUS_KM / 111.32
        dlon = RADAR_RADIUS_KM / (111.32 * max(0.01, math.cos(math.radians(HOME_LAT))))
        params = {"lamin": max(-90, HOME_LAT - dlat), "lamax": min(90, HOME_LAT + dlat),
                  "lomin": max(-180, HOME_LON - dlon), "lomax": min(180, HOME_LON + dlon), "extended": 1}
        resp = requests.get(self.URL, params=params, headers=self._headers(), timeout=HTTP_TIMEOUT)
        if resp.status_code == 429:
            raise RuntimeError("OpenSky's daily request limit is used up "
                               f"(retry in {resp.headers.get('X-Rate-Limit-Retry-After-Seconds', '?')}s)")
        resp.raise_for_status()
        states = (resp.json() or {}).get("states") or []
        self.cached, self.fetched_at = [a for a in map(self.aircraft, states) if a is not None], time.monotonic()
        return self.cached

    @staticmethod
    def aircraft(s):
        if not isinstance(s, list) or len(s) < 12:
            return None
        lat, lon = number(s[6]), number(s[5])
        if lat is None or lon is None:
            return None
        callsign = str(s[1] or "").strip().upper()
        airline_style = bool(AIRLINE_CALLSIGN.match(callsign))
        metres = first(number(s[7]), number(s[13]) if len(s) > 13 else None)
        ground = bool(s[8])
        return Aircraft(
            source="opensky", hex=str(s[0] or "").lower(), callsign=callsign, latitude=lat, longitude=lon,
            altitude=0 if ground else (round(metres * 3.28084) if metres is not None else None),
            speed=round(s[9] * 1.94384) if number(s[9]) is not None else None,
            vspeed=round(s[11] * 196.85) if number(s[11]) is not None else None,
            heading=number(s[10]), on_ground=ground,
            airline_flight=airline_style, airline_icao=callsign[:3] if airline_style else None,
            rotorcraft=len(s) > 17 and s[17] == 8,
        )

    def enrich(self, a):
        lookup_adsbdb(a)


SOURCES = {source.name: source for source in (FR24Source(), ADSBSource(), OpenSkySource())}

_lookups = {}   # adsbdb path -> (response or None, expires_at)


def _adsbdb(path):
    """GET https://api.adsbdb.com/v0/<path>, cached (a day for hits, 6 h for misses, 10 min for errors)."""
    cached = _lookups.get(path)
    if cached and cached[1] > time.time():
        return cached[0]
    try:
        resp = requests.get(f"https://api.adsbdb.com/v0/{path}", timeout=HTTP_TIMEOUT, headers=USER_AGENT)
        if resp.status_code == 404:
            data, ttl = None, 6 * 3600
        else:
            resp.raise_for_status()
            data = dig(resp.json(), "response")
            data, ttl = (data, 86400) if isinstance(data, dict) else (None, 6 * 3600)
    except Exception as exc:
        log.debug("adsbdb %s failed: %s", path, exc)
        data, ttl = None, 600
    if len(_lookups) > 5000:
        now = time.time()
        for key in [k for k, (_, expires) in _lookups.items() if expires <= now]:
            del _lookups[key]
    _lookups[path] = (data, time.time() + ttl)
    return data


def lookup_adsbdb(a):
    """Fill in flight number, airline, route, type and registration from adsbdb.com."""
    if not LOOKUP_ADSBDB:
        return
    route = dig(_adsbdb(f"callsign/{a.callsign}"), "flightroute") if AIRLINE_CALLSIGN.match(a.callsign or "") else None
    if route:
        a.flight = first(route.get("callsign_iata"), a.flight)
        a.airline = first(dig(route, "airline", "name"), a.airline)
        a.airline_iata = first(dig(route, "airline", "iata"), a.airline_iata)
        a.airline_icao = first(dig(route, "airline", "icao"), a.airline_icao)
        a.origin = first(dig(route, "origin", "iata_code"), a.origin)
        a.destination = first(dig(route, "destination", "iata_code"), a.destination)
    if a.hex and re.fullmatch(r"[0-9a-f]{6}", a.hex) and (is_missing(a.aircraft) or is_missing(a.registration)):
        craft = dig(_adsbdb(f"aircraft/{a.hex}"), "aircraft")
        if craft:
            a.aircraft = first(a.aircraft, craft.get("icao_type"))
            a.registration = first(a.registration, craft.get("registration"))


def fetch_traffic():
    """Aircraft from the first DATA_SOURCE that answers."""
    error = None
    for name in DATA_SOURCE:
        try:
            return SOURCES[name].fetch()
        except Exception as exc:
            error = exc
            if len(DATA_SOURCE) > 1:
                log.warning("%s unavailable (%s)%s", name, exc, "; trying the next source" if name != DATA_SOURCE[-1] else "")
    raise error


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
    bearing: float = 0.0                            # from home to the plane
    heading: float = None                           # the way the plane is flying
    tags: tuple = ()                                # "military" and/or "rare"
    callsign: str = ""
    hex: str = ""
    source: str = ""
    pinned: bool = False                            # picked on the web page rather than chosen automatically
    traffic: list = field(default_factory=list)     # everything else in range, for the web page


MAX_LOOKUPS = 5   # candidates enriched per update when SKIP_UNKNOWN_DESTINATION needs routes


def _candidates(aircraft):
    """(aircraft, tags, distance) for everything worth showing within RADAR_RADIUS_KM, closest first."""
    found = []
    for a in aircraft:
        distance = haversine_km(HOME_LAT, HOME_LON, a.latitude, a.longitude)
        if distance > RADAR_RADIUS_KM:   # sources search a box; keep the circle
            continue
        if (not SHOW_GROUND_TRAFFIC and a.on_ground) or (IGNORE_HELICOPTERS and is_helicopter(a)):
            continue
        tags = highlights(a)
        if not tags:   # ordinary traffic: airline flights only (and no military unless highlighted)
            if not a.airline_flight or is_military(a):
                continue
            if SKIP_UNKNOWN_DESTINATION and a.source == "fr24" and is_missing(a.destination):
                continue
        found.append((a, tags, distance))
    return sorted(found, key=lambda c: c[2])


def _traffic_row(a, tags, distance):
    return {
        "flight": str(first(a.flight, a.callsign) or "").upper(),
        "callsign": a.callsign,
        "hex": a.hex,
        "aircraft": first(a.aircraft),
        "registration": first(a.registration),
        "origin": first(a.origin),
        "destination": first(a.destination),
        "altitude": a.altitude,
        "speed": a.speed,
        "heading": a.heading,
        "distance_km": round(distance, 1),
        "direction": compass(bearing_deg(HOME_LAT, HOME_LON, a.latitude, a.longitude)),
        "tags": list(tags),
    }


def is_pinned(a, pin):
    """Whether aircraft a is the one picked on the web page (by address, else by callsign)."""
    if pin.get("hex"):
        return a.hex == pin["hex"]
    return bool(pin.get("callsign")) and a.callsign == pin["callsign"]


def _pick(candidates):
    """The automatic choice: closest first; with PRIORITIZE_HIGHLIGHTS a military or rare
    aircraft anywhere in range wins. Returns (aircraft, tags, distance) or None."""
    ordered = sorted(candidates, key=lambda c: (PRIORITIZE_HIGHLIGHTS and not c[1], c[2]))
    for a, tags, distance in ordered[:MAX_LOOKUPS]:
        SOURCES[a.source].enrich(a)
        tags = tuple(dict.fromkeys(tags + highlights(a)))   # the lookup may reveal a rare type
        # Other sources only learn the destination from the lookup, so check it now
        if not (SKIP_UNKNOWN_DESTINATION and not tags and is_missing(a.destination)):
            return a, tags, distance
    return None


def fetch_nearest(pin=None):
    """The flight to show: the pinned one while it's still around (airborne), else the automatic pick."""
    aircraft = fetch_traffic()
    candidates = _candidates(aircraft)
    if history:
        history.observe(candidates)
    chosen, pinned = None, False
    if pin:
        match = next((a for a in aircraft if is_pinned(a, pin) and (SHOW_GROUND_TRAFFIC or not a.on_ground)), None)
        if match:
            SOURCES[match.source].enrich(match)
            chosen = (match, highlights(match), haversine_km(HOME_LAT, HOME_LON, match.latitude, match.longitude))
            pinned = True
    if chosen is None:
        chosen = _pick(candidates)
    if chosen is None:
        return None
    a, tags, distance = chosen

    return Nearby(
        flight=str(first(a.flight, a.callsign) or "").upper(),
        airline=str(a.airline or ""),
        airline_iata=a.airline_iata,
        airline_icao=a.airline_icao,
        origin=a.origin,
        destination=a.destination,
        aircraft=a.aircraft,
        registration=a.registration,
        altitude=a.altitude,
        speed=a.speed,
        vspeed=a.vspeed,
        distance_km=distance,
        bearing=bearing_deg(HOME_LAT, HOME_LON, a.latitude, a.longitude),
        heading=a.heading,
        tags=tags,
        callsign=a.callsign,
        hex=a.hex,
        source=a.source,
        pinned=pinned,
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
    if not n.speed:
        return "---"
    return f"{int(n.speed)}KT" + (arrow(n.heading) if SHOW_HEADING and n.heading is not None else "")


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
FLAP_DRUM = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-./+:?▲▼"   # anything else (heading arrows) changes directly
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


def _draw_highlight(img, tags, step, pinned=False):
    if not tags:
        if pinned:   # picked on the web page: a steady white badge
            fill(img, 0, 0, text_width("PIN", F35) + 3, 7, COLOR_WHITE)
            draw_text(img, (2, 1), "PIN", F35, "#000000")
        return
    if step % HIGHLIGHT_PERIOD >= 6:
        return
    color, badge = (COLOR_MILITARY, "MIL") if "military" in tags else (COLOR_RARE, "RARE")
    ImageDraw.Draw(img).rectangle((0, 0, 63, 63), outline=rgb(color))
    fill(img, 0, 0, text_width(badge, F35) + 3, 7, color)
    draw_text(img, (2, 1), badge, F35, "#000000")


def radar_page(pin=None):
    n = fetch_nearest(pin)
    if n is None:
        raise LookupError("no airborne flights nearby")

    logo = airline_logo(n.airline_iata, n.airline_icao)
    origin = str(n.origin or "---").upper()[:3]
    dest = str(n.destination or "---").upper()[:3]
    panels = [
        (("FLT", n.flight), ("ALT", _fmt_altitude(n))),
        (("TYPE", n.aircraft), ("REG", n.registration)),
        (("SPD", _fmt_speed(n)), (compass(n.bearing) if SHOW_BEARING else "DIST", _fmt_distance(n))),
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
        _draw_highlight(img, n.tags, step, n.pinned)
        frames.append(img)

    summary = (f"{n.flight} {origin}->{dest} {n.aircraft or ''} {_fmt_altitude(n)} {_fmt_distance(n)} "
               f"{compass(n.bearing)}" + "".join(f" [{tag.upper()}]" for tag in n.tags) + (" [PINNED]" if n.pinned else ""))
    return frames, FRAME_MS // sub, summary, n


# =============================================================================
# History: every aircraft that passed through the radius (SQLite or CSV)
# =============================================================================

def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _epoch(iso):
    return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))


class History:
    """One entry per visit: an aircraft that stays in range is one entry, written once
    it's been out of sight for GAP seconds (or at shutdown). Entries older than
    HISTORY_RETENTION_DAYS are removed every HISTORY_TRIM_HOURS."""

    FIELDS = ("first_seen", "last_seen", "flight", "callsign", "hex", "airline", "aircraft", "registration",
              "origin", "destination", "closest_km", "lowest_ft", "tags", "shown", "source")
    GAP = 600
    STATS_TTL = 60

    def __init__(self, kind, directory, retention_days=None, trim_hours=None):
        os.makedirs(directory, exist_ok=True)
        self.kind = kind
        self.retention_days = HISTORY_RETENTION_DAYS if retention_days is None else retention_days
        self.trim_hours = HISTORY_TRIM_HOURS if trim_hours is None else trim_hours
        self.path = os.path.join(directory, "history.sqlite" if kind == "sqlite" else "history.csv")
        self.lock = threading.RLock()
        self.active = {}                 # key -> entry, for aircraft currently in range
        self.trimmed_at, self._stats = 0.0, (0.0, None)
        if kind == "sqlite":
            self.db = sqlite3.connect(self.path, check_same_thread=False)
            columns = ", ".join(f"{f} {'REAL' if f in ('closest_km', 'lowest_ft') else 'INTEGER' if f == 'shown' else 'TEXT'}"
                                for f in self.FIELDS)
            self.db.execute(f"CREATE TABLE IF NOT EXISTS sightings ({columns})")
            self.db.execute("CREATE INDEX IF NOT EXISTS sightings_last_seen ON sightings (last_seen)")
            self.db.commit()
        elif not os.path.exists(self.path) or os.path.getsize(self.path) == 0:
            with open(self.path, "w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(self.FIELDS)
        self.trim()
        self._resume(time.time())

    def _resume(self, now):
        """Take back visits written at the last shutdown that may still be going on, so a
        restart doesn't count the aircraft in range twice."""
        cutoff = _iso(now - self.GAP)
        with self.lock:
            recent = [r for r in self._stored() if (r.get("last_seen") or "") >= cutoff]
            if not recent:
                return
            if self.kind == "sqlite":
                self.db.execute("DELETE FROM sightings WHERE last_seen >= ?", (cutoff,))
                self.db.commit()
            else:
                self._rewrite_csv([r for r in self._stored() if (r.get("last_seen") or "") < cutoff])
            for r in recent:
                key = self._key(r.get("hex") or "", r.get("callsign") or "")
                if not key:
                    continue
                e = {f: (r.get(f) or None) for f in self.FIELDS}
                lowest = r.get("lowest_ft")
                e.update(first_seen=_epoch(r["first_seen"]), last_seen=_epoch(r["last_seen"]),
                         shown=str(r.get("shown")) in ("1", "True"), closest_km=float(r["closest_km"]),
                         tags={t for t in str(r.get("tags") or "").split(";") if t},
                         lowest_ft=int(float(lowest)) if lowest not in (None, "") else None)
                self.active[key] = e

    def _rewrite_csv(self, rows):
        tmp = self.path + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, self.path)

    @staticmethod
    def _key(hex_, callsign):
        return hex_ or callsign

    def observe(self, candidates, now=None):
        """Record everything in range this update; write out visits that have ended."""
        now = time.time() if now is None else now
        with self.lock:
            for a, tags, distance in candidates:
                key = self._key(a.hex, a.callsign)
                if not key:
                    continue
                e = self.active.setdefault(key, {"first_seen": now, "shown": False, "tags": set(),
                                                 "closest_km": distance, "lowest_ft": None})
                e["last_seen"] = now
                e["closest_km"] = min(e["closest_km"], distance)
                if a.altitude is not None:
                    e["lowest_ft"] = a.altitude if e["lowest_ft"] is None else min(e["lowest_ft"], a.altitude)
                e["tags"].update(tags)
                for f in ("flight", "callsign", "hex", "airline", "aircraft", "registration", "origin", "destination", "source"):
                    e[f] = first(getattr(a, f), e.get(f))
            ended = [k for k, e in self.active.items() if now - e["last_seen"] > self.GAP]
            self._write([self.active.pop(k) for k in ended])
            if self.trim_hours > 0 and now - self.trimmed_at >= self.trim_hours * 3600:
                self.trim(now)

    def mark_shown(self, n):
        """The flight went on screen: flag it, and keep the details the lookup found."""
        with self.lock:
            e = self.active.get(self._key(n.hex, n.callsign))
            if e is None:
                return
            e["shown"] = True
            e["tags"].update(n.tags)
            for f in ("flight", "airline", "aircraft", "registration", "origin", "destination"):
                e[f] = first(getattr(n, f), e.get(f))

    def _row(self, e):
        row = {f: e.get(f) for f in self.FIELDS}
        row.update(first_seen=_iso(e["first_seen"]), last_seen=_iso(e["last_seen"]),
                   closest_km=round(e["closest_km"], 1), tags=";".join(sorted(e["tags"])), shown=int(bool(e["shown"])))
        return row

    def _write(self, entries):
        if not entries:
            return
        rows = [self._row(e) for e in entries]
        if self.kind == "sqlite":
            self.db.executemany(f"INSERT INTO sightings VALUES ({', '.join('?' * len(self.FIELDS))})",
                                [tuple(r[f] for f in self.FIELDS) for r in rows])
            self.db.commit()
        else:
            with open(self.path, "a", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerows([[("" if r[f] is None else r[f]) for f in self.FIELDS] for r in rows])
        self._stats = (0.0, None)

    def _stored(self):
        if self.kind == "sqlite":
            cur = self.db.execute(f"SELECT {', '.join(self.FIELDS)} FROM sightings ORDER BY last_seen")
            return [dict(zip(self.FIELDS, r)) for r in cur.fetchall()]
        with open(self.path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def trim(self, now=None):
        now = time.time() if now is None else now
        self.trimmed_at = now
        if self.retention_days <= 0:
            return
        cutoff = _iso(now - self.retention_days * 86400)
        with self.lock:
            if self.kind == "sqlite":
                removed = self.db.execute("DELETE FROM sightings WHERE last_seen < ?", (cutoff,)).rowcount
                self.db.commit()
            else:
                rows = self._stored()
                keep = [r for r in rows if (r.get("last_seen") or "") >= cutoff]
                removed = len(rows) - len(keep)
                if removed:
                    self._rewrite_csv(keep)
        if removed:
            log.info("History: removed %s entries older than %s days", removed, self.retention_days)
            self._stats = (0.0, None)

    def entries(self, limit=100):
        """Most recent visits first, including ones still in progress."""
        with self.lock:
            rows = [{**self._row(e), "active": True} for e in self.active.values()]
            rows += [{**r, "active": False} for r in self._stored()]
        rows.sort(key=lambda r: r["last_seen"] or "", reverse=True)
        for r in rows:
            r["shown"] = str(r.get("shown")) in ("1", "True")
            r["tags"] = [t for t in str(r.get("tags") or "").split(";") if t]
            for f in ("closest_km", "lowest_ft"):
                r[f] = float(r[f]) if r.get(f) not in (None, "") else None
        return rows[:limit]

    def stats(self):
        """Visits since local midnight, cached for a minute."""
        cached_at, value = self._stats
        if value is not None and time.time() - cached_at < self.STATS_TTL:
            return value
        midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        today = [r for r in self.entries(limit=10**9) if (r["first_seen"] or "") >= _iso(midnight)]
        value = {
            "today": len(today),
            "military_today": sum("military" in r["tags"] for r in today),
            "rare_today": sum("rare" in r["tags"] for r in today),
            "recent_highlights": [r for r in today if r["tags"]][:10],
        }
        self._stats = (time.time(), value)
        return value

    def close(self):
        """Write out the visits in progress."""
        with self.lock:
            self._write(list(self.active.values()))
            self.active.clear()
            if self.kind == "sqlite":
                self.db.close()


history = None   # set by run() when HISTORY is sqlite or csv


# =============================================================================
# MQTT (optional): state for Home Assistant and anything else
# =============================================================================

class Mqtt:
    """Publishes <topic>/state (retained JSON), <topic>/availability and, when a military
    or rare aircraft comes on screen, <topic>/spotted. Registers Home Assistant sensors."""

    SENSORS = (   # key, name, template on value_json, unit, device class, icon
        ("flight", "Flight", "value_json.flight.flight", None, None, "mdi:airplane"),
        ("airline", "Airline", "value_json.flight.airline", None, None, "mdi:airplane-marker"),
        ("route", "Route", "(value_json.flight.origin or '---') ~ ' → ' ~ (value_json.flight.destination or '---')",
         None, None, "mdi:map-marker-path"),
        ("aircraft", "Aircraft type", "value_json.flight.aircraft", None, None, "mdi:airplane-cog"),
        ("registration", "Registration", "value_json.flight.registration", None, None, "mdi:card-text"),
        ("altitude", "Altitude", "value_json.flight.altitude", "ft", "distance", "mdi:arrow-expand-vertical"),
        ("speed", "Speed", "value_json.flight.speed", "kn", "speed", "mdi:speedometer"),
        ("distance", "Distance", "value_json.flight.distance_km | round(1)", "km", "distance", "mdi:map-marker-distance"),
        ("direction", "Direction", "value_json.flight.direction", None, None, "mdi:compass"),
        ("heading", "Heading", "value_json.flight.heading | round(0) if value_json.flight.heading is not none else None",
         "°", None, "mdi:navigation"),
        ("highlight", "Highlight", "value_json.flight.tags | join(', ') or 'none'", None, None, "mdi:star"),
    )

    def __init__(self):
        import paho.mqtt.client as paho   # only needed when MQTT is on

        self.topic = MQTT_TOPIC.strip("/") or "pixoo_radar"
        self.node = re.sub(r"[^A-Za-z0-9_-]", "_", self.topic)
        self.last_state = None
        self.client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=f"{self.node}-{secrets.token_hex(3)}")
        if MQTT_USERNAME:
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD or None)
        if MQTT_TLS:
            self.client.tls_set()
        self.client.will_set(f"{self.topic}/availability", "offline", retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.reconnect_delay_set(min_delay=1, max_delay=120)
        self.client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("MQTT broker %s:%s refused the connection: %s", MQTT_HOST, MQTT_PORT, reason_code)
            return
        log.info("MQTT connected to %s:%s, publishing to %s/state", MQTT_HOST, MQTT_PORT, self.topic)
        client.publish(f"{self.topic}/availability", "online", retain=True)
        if MQTT_DISCOVERY:
            for config_topic, config in self.discovery():
                client.publish(config_topic, json.dumps(config), retain=True)
        if self.last_state is not None:
            client.publish(f"{self.topic}/state", self.last_state, retain=True)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("MQTT connection lost (%s); reconnecting", reason_code)

    def discovery(self):
        """(topic, config) pairs for Home Assistant MQTT discovery."""
        device = {"identifiers": [self.node], "name": "Pixoo Radar", "manufacturer": "pixoo-radar-enhanced",
                  "model": "Pixoo Radar", "sw_version": __version__}
        common = {"state_topic": f"{self.topic}/state", "availability_topic": f"{self.topic}/availability",
                  "device": device}
        configs = []
        for key, name, expression, unit, device_class, icon in self.SENSORS:
            config = {**common, "name": name, "unique_id": f"{self.node}_{key}", "icon": icon,
                      "value_template": f"{{{{ ({expression}) if value_json.flight else None }}}}"}
            if key == "flight":
                config["json_attributes_topic"] = f"{self.topic}/state"
                config["json_attributes_template"] = "{{ (value_json.flight or {}) | tojson }}"
            if unit:
                config.update(unit_of_measurement=unit, state_class="measurement")
            if device_class:
                config["device_class"] = device_class
            configs.append((f"{MQTT_DISCOVERY_PREFIX}/sensor/{self.node}/{key}/config", config))
        configs.append((f"{MQTT_DISCOVERY_PREFIX}/sensor/{self.node}/nearby/config", {
            **common, "name": "Aircraft nearby", "unique_id": f"{self.node}_nearby", "icon": "mdi:radar",
            "value_template": "{{ value_json.traffic | length }}", "state_class": "measurement"}))
        configs.append((f"{MQTT_DISCOVERY_PREFIX}/sensor/{self.node}/seen_today/config", {
            **common, "name": "Aircraft seen today", "unique_id": f"{self.node}_seen_today", "icon": "mdi:counter",
            "value_template": "{{ value_json.stats.today if value_json.stats else None }}",
            "state_class": "total"}))
        return configs

    def publish_state(self, state):
        payload = json.dumps(state, sort_keys=True)
        if payload != self.last_state:
            self.last_state = payload
            self.client.publish(f"{self.topic}/state", payload, retain=True)

    def spotted(self, flight):
        self.client.publish(f"{self.topic}/spotted", json.dumps(flight))

    def close(self):
        try:
            self.client.publish(f"{self.topic}/availability", "offline", retain=True).wait_for_publish(3)
        except Exception:
            pass   # not connected: the broker sends our will instead
        self.client.disconnect()
        self.client.loop_stop()


# =============================================================================
# Main loop
# =============================================================================

def _flight_status(n):
    flight = {k: v for k, v in asdict(n).items() if k != "traffic"}
    return {**flight, "direction": compass(n.bearing), "altitude_text": _fmt_altitude(n),
            "heading_text": f"{arrow(n.heading)} {round(n.heading)}°" if n.heading is not None else None}


def run():
    global history
    pixoo = Pixoo(PIXOO_HOST)
    log.info("Pixoo at %s | home %.4f,%.4f | radius %skm | checking every %ss | data from %s",
             PIXOO_HOST, HOME_LAT, HOME_LON, RADAR_RADIUS_KM, REFRESH_SECONDS, ", ".join(DATA_SOURCE))
    if HISTORY in ("sqlite", "csv"):
        history = History(HISTORY, HISTORY_DIR)
        log.info("Recording history to %s (kept %s days)", history.path, HISTORY_RETENTION_DAYS or "forever")
    mqtt = Mqtt() if MQTT_HOST else None
    try:
        _loop(pixoo, mqtt)
    finally:
        if history:
            history.close()
        if mqtt:
            mqtt.close()


def _loop(pixoo, mqtt):
    # The web server always runs (its root page shows the current screen); the Pixoo
    # only fetches from it when GIF_BASE_URL is set and reachable.
    use_url = bool(GIF_BASE_URL) and "CHANGE-ME" not in GIF_BASE_URL
    base_url = with_ip(GIF_BASE_URL) if use_url else f"http://localhost:{WEB_PORT}"
    gif_host = GifHost(base_url, WEB_PORT)
    hosted = False
    if use_url:
        log.info("Serving animations on port %s; the Pixoo fetches them from %s%s", WEB_PORT, base_url,
                 f" ({GIF_BASE_URL})" if base_url != GIF_BASE_URL else "")
        hosted = gif_host.self_check()
    else:
        log.info("GIF_BASE_URL not set: pushing frames (the Pixoo shows Loading.. on each update); "
                 "current screen on port %s", WEB_PORT)

    if 0 <= PIXOO_BRIGHTNESS <= 100:
        try:
            pixoo.set_brightness(PIXOO_BRIGHTNESS)
        except Exception as exc:
            log.warning("Could not set brightness: %s", exc)

    on_screen, failures, retry_hosted_at, spotted, pin_misses = None, 0, None, None, 0
    PIN_RELEASE_AFTER = 2   # updates the pinned aircraft may be missing (gone, or landed) before going back to auto

    def pinned_missing(pin):
        nonlocal pin_misses
        if pin is None or gif_host.pin is not pin:
            pin_misses = 0
            return
        pin_misses += 1
        if pin_misses >= PIN_RELEASE_AFTER:
            gif_host.clear_pin("isn't in the data any more (out of range or landed)")
            pin_misses = 0

    def publish_status(**status):
        if history:
            status["stats"] = history.stats()
        gif_host.set_status(**status)
        if mqtt:   # the flight closest now: none while nothing is in range (the screen keeps the last one)
            mqtt.publish_state({**status, "flight": status.get("flight") if status.get("traffic") else None,
                                "updated": _iso(time.time())})

    while True:
        started = gif_host.heartbeat = time.monotonic()

        # A Pixoo that couldn't fetch may just have been waiting on something that has
        # since come up, so look again now and then rather than pushing frames forever.
        if use_url and not hosted and retry_hosted_at and started >= retry_hosted_at:
            hosted = gif_host.self_check(wait_seconds=0)
            retry_hosted_at = None if hosted else started + HOSTED_RETRY_SECONDS
            if hosted:
                log.info("Trying hosted GIFs again")

        pin = gif_host.pin
        try:
            frames, frame_ms, summary, n = radar_page(pin)
            failures = 0
        except LookupError as exc:  # nothing airborne nearby: keep the last flight on screen
            log.info("%s", exc)
            pinned_missing(pin)
            publish_status(**{**gif_host.status, "traffic": [], "note": str(exc)})
            gif_host.wait(REFRESH_SECONDS)
            continue
        except Exception as exc:
            failures += 1
            log.warning("Flight data unavailable (%s)", exc)
            gif_host.wait(min(300, REFRESH_SECONDS * failures))
            continue
        if n.pinned:
            pin_misses = 0
        else:
            pinned_missing(pin)

        flight = _flight_status(n)
        publish_status(flight=flight, traffic=n.traffic, mode="hosted GIF" if hosted else "pushed frames",
                       source=n.source)
        if history:
            history.mark_shown(n)
        if n.tags and spotted != (n.hex or n.flight):
            spotted = n.hex or n.flight
            log.info("Spotted %s aircraft: %s", " and ".join(n.tags), summary)
            if mqtt:
                mqtt.spotted(flight)

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

        gif_host.wait(REFRESH_SECONDS - (time.monotonic() - started))


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


def _startup_problems(preview=False):
    """Everything that would stop the app from working, as messages."""
    required = ("HOME_LAT", "HOME_LON") if preview else ("PIXOO_HOST", "HOME_LAT", "HOME_LON")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    problems = [f"Missing required setting(s): {', '.join(missing)}. Set them as environment variables, "
                "e.g. PIXOO_HOST=192.168.1.50 HOME_LAT=51.4700 HOME_LON=-0.4543"] if missing else []
    problems += SETTING_PROBLEMS
    if MQTT_HOST and not preview:
        try:
            import paho.mqtt.client  # noqa: F401
        except ImportError:
            problems.append("MQTT_HOST is set but paho-mqtt isn't installed (pip install -r requirements.txt)")
    return problems


def _check_frame():
    img = Image.new("RGB", (64, 64))
    for y, text, color in ((10, "PIXOO", COLOR_WHITE), (20, "RADAR", COLOR_WHITE), (38, "CHECK", COLOR_TEXT),
                           (48, "OK", COLOR_TEXT)):
        draw_text(img, ((64 - text_width(text, F57)) // 2, y), text, F57, color)
    return img


def check():
    """--check: try every part of the setup and print what works. Returns the exit code."""
    failed = []

    def report(ok, text):
        if ok is False:
            failed.append(text)
        print({True: "✓", False: "✗", None: "-"}[ok], text, flush=True)

    print(f"Pixoo Radar {__version__}\n")
    problems = _startup_problems()
    report(not problems, "Settings are valid" if not problems else "Settings:\n    " + "\n    ".join(problems))
    for old, new in RENAMED_SETTINGS:
        report(None, f"{old} is now called {new}; the old name still works")

    pixoo = None
    if PIXOO_HOST:
        started = time.monotonic()
        try:
            pixoo = Pixoo(PIXOO_HOST)
            pixoo._post("Channel/GetIndex")
            report(True, f"Pixoo at {PIXOO_HOST} answers ({(time.monotonic() - started) * 1000:.0f} ms)")
        except Exception as exc:
            pixoo = None
            report(False, f"Pixoo at {PIXOO_HOST} doesn't answer: {exc}")

    closest = None
    for name in [n for n in DATA_SOURCE if n in SOURCES]:
        try:
            aircraft = SOURCES[name].fetch()
            candidates = _candidates(aircraft)
            report(True, f"{name}: {len(aircraft)} aircraft in the area, {len(candidates)} to choose from "
                         f"within {RADAR_RADIUS_KM} km")
            closest = closest or (candidates[0] if candidates else None)
        except Exception as exc:
            report(False, f"{name}: {exc}")
    if closest:
        a, tags, distance = closest
        report(None, f"Closest: {first(a.flight, a.callsign)} {first(a.aircraft) or ''} {distance:.1f} km "
                     f"{compass(bearing_deg(HOME_LAT, HOME_LON, a.latitude, a.longitude))}")
    if LOOKUP_ADSBDB and any(n in ("adsb", "opensky") for n in DATA_SOURCE):
        try:
            status = requests.get("https://api.adsbdb.com/v0/callsign/BAW1", timeout=HTTP_TIMEOUT,
                                  headers=USER_AGENT).status_code
            report(status in (200, 404), "adsbdb.com is reachable" if status in (200, 404) else
                   f"adsbdb.com answered HTTP {status}")
        except Exception as exc:
            report(False, f"adsbdb.com: {exc}")

    if GIF_BASE_URL and "CHANGE-ME" not in GIF_BASE_URL:
        try:
            host = GifHost(with_ip(GIF_BASE_URL), WEB_PORT)
        except OSError:
            report(None, f"Port {WEB_PORT} is in use (Pixoo Radar already running?), so the hosted-GIF test is "
                         "skipped; its log says whether the Pixoo can fetch")
        else:
            try:
                reachable = host.self_check(wait_seconds=0)
                report(reachable, f"{GIF_BASE_URL} reaches this app over plain http" if reachable else
                       f"{GIF_BASE_URL} doesn't reach this app (see the warning above)")
                if reachable and pixoo:
                    name, url = host.publish(encode_gif([_check_frame()], 1000))
                    pixoo.play_gif_url(url)
                    fetched = host.fetched[name].wait(15)
                    report(fetched, f"The Pixoo downloaded a test GIF from {url} (it shows CHECK OK)" if fetched else
                           f"The Pixoo didn't download {url} within 15 s: it can't reach this address (firewall or "
                           "DNS on its network?). It may be unresponsive for a minute.")
            finally:
                host.server.shutdown()
                host.server.server_close()
    else:
        report(None, "GIF_BASE_URL isn't set, so frames are pushed (the Pixoo shows Loading.. on each update)")

    if HISTORY in ("sqlite", "csv"):
        try:
            h = History(HISTORY, HISTORY_DIR)
            h.close()
            report(True, f"History can be written to {h.path}")
        except Exception as exc:
            report(False, f"History can't be written to {HISTORY_DIR}: {exc}")

    if MQTT_HOST:
        try:
            import paho.mqtt.client as paho
            answered, outcome = threading.Event(), {}
            client = paho.Client(paho.CallbackAPIVersion.VERSION2)
            if MQTT_USERNAME:
                client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD or None)
            if MQTT_TLS:
                client.tls_set()
            client.on_connect = lambda c, u, f, rc, p: (outcome.update(rc=rc), answered.set())
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
            client.loop_start()
            answered.wait(10)
            client.disconnect()
            client.loop_stop()
            rc = outcome.get("rc")
            report(rc is not None and not rc.is_failure,
                   f"MQTT broker {MQTT_HOST}:{MQTT_PORT} " + ("accepted the connection" if rc is not None and
                   not rc.is_failure else f"refused the connection: {rc}" if rc else "didn't answer"))
        except Exception as exc:
            report(False, f"MQTT broker {MQTT_HOST}:{MQTT_PORT}: {exc}")

    print("\nAll good." if not failed else f"\n{len(failed)} problem(s) found.")
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description="Closest-flight radar for the Divoom Pixoo64")
    parser.add_argument("--preview", metavar="DIR", help="render the current flight to PNGs in DIR and exit")
    parser.add_argument("--check", action="store_true", help="test the Pixoo, data sources, GIF_BASE_URL, history "
                        "and MQTT, then exit (briefly shows a test screen on the Pixoo)")
    parser.add_argument("--version", action="version", version=f"Pixoo Radar {__version__}")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    if args.check:
        logging.getLogger().setLevel(logging.WARNING)
        sys.exit(check())
    problems = _startup_problems(preview=bool(args.preview))
    if problems:
        sys.exit("\n".join(problems))
    for old, new in RENAMED_SETTINGS:
        log.warning("%s has been renamed to %s; please update your settings (the old name still works for now)",
                    old, new)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # PID 1 in Docker ignores SIGTERM otherwise

    try:
        preview(args.preview) if args.preview else run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
