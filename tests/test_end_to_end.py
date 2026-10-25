"""Runs the real script as a subprocess against a fake Pixoo and a fake ADS-B receiver."""
import base64
import io
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image
from conftest import SCRIPT, free_port
from test_mqtt import broker, listen, wait_for  # noqa: F401  (broker is a fixture)

AIRCRAFT = {"hex": "4010ee", "flight": "UAE29  ", "r": "A6-EUA", "t": "B748", "alt_baro": 36000, "gs": 480,
            "track": 275, "baro_rate": 0, "category": "A5", "lat": 51.52, "lon": -0.40}


class Server:
    """A tiny HTTP server in a thread; handler(method, path, body) -> (status, content_type, bytes)."""
    def __init__(self, handler):
        outer = self

        class H(BaseHTTPRequestHandler):
            def _do(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                status, ctype, data = outer.handler(self.command, self.path, body)
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            do_GET = do_POST = _do

            def log_message(self, *a):
                pass

        self.handler, self.port = handler, free_port()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FakePixoo:
    """Answers the Pixoo API like the device and, like the device, downloads hosted GIFs."""
    def __init__(self):
        self.commands, self.downloads = [], []
        self.server = Server(self.handle)

    def handle(self, method, path, body):
        command = json.loads(body)
        self.commands.append(command)
        if command["Command"] == "Device/PlayTFGif":
            threading.Thread(target=self.download, args=(command["FileName"],), daemon=True).start()
        return 200, "application/json", b'{"error_code": 0}'

    def download(self, url):
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.downloads.append((url, resp.status, resp.headers["Content-Type"], resp.read()))

    def named(self, name):
        return [c for c in self.commands if c["Command"] == name]


@pytest.fixture
def world(tmp_path):
    feed = {"ac": [AIRCRAFT]}
    receiver = Server(lambda m, p, b: (200, "application/json", json.dumps(feed).encode()))
    pixoo = FakePixoo()
    procs = []

    def start(**env):
        gif_port = free_port()
        full_env = {**os.environ, "PIXOO_HOST": f"127.0.0.1:{pixoo.server.port}", "HOME_LAT": "51.47",
                    "HOME_LON": "-0.45", "DATA_SOURCE": "adsb", "LOOKUP_ADSBDB": "false", "LOGO_SOURCES": "none",
                    "ADSB_URL": f"http://127.0.0.1:{receiver.port}/data/aircraft.json", "GIF_PORT": str(gif_port),
                    "REFRESH_SECONDS": "2", "SELF_CHECK_WAIT": "5", "PYTHONUNBUFFERED": "1",
                    **{k: v.format(gif_port=gif_port) for k, v in env.items()}}
        proc = subprocess.Popen([sys.executable, str(SCRIPT)], env=full_env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        procs.append(proc)
        return proc, f"http://127.0.0.1:{gif_port}"

    yield feed, pixoo, start
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
    receiver.close()
    pixoo.server.close()


def api(base):
    try:
        with urllib.request.urlopen(base + "/api/current", timeout=2) as resp:
            return json.loads(resp.read())
    except OSError:
        return {}


def stop(proc):
    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=10)
    return proc.returncode, out


def test_hosted_gif_history_and_mqtt(world, broker, tmp_path):  # noqa: F811
    feed, pixoo, start = world
    client, messages = listen(broker, "pixoo_radar/#")
    proc, base = start(GIF_BASE_URL="http://127.0.0.1:{gif_port}", SPLIT_FLAP="all", HISTORY="sqlite",
                       HISTORY_DIR=str(tmp_path), MQTT_HOST="127.0.0.1", MQTT_PORT=str(broker))

    assert wait_for(lambda: pixoo.downloads, timeout=20), "the Pixoo never downloaded a GIF"
    url, status, ctype, data = pixoo.downloads[0]
    assert url.startswith(base + "/") and status == 200 and ctype == "image/gif"
    gif = Image.open(io.BytesIO(data))
    assert gif.n_frames == 54 and gif.info["duration"] == 150
    assert not pixoo.named("Draw/SendHttpGif")                       # hosted: nothing pushed

    assert wait_for(lambda: "flight" in api(base))
    current = api(base)
    assert current["flight"]["flight"] == "UAE29" and current["flight"]["tags"] == ["rare"]
    assert current["mode"] == "hosted GIF" and current["source"] == "adsb" and current["stats"]["today"] == 1
    assert wait_for(lambda: any(t == "pixoo_radar/spotted" for t, *_ in messages))
    assert wait_for(lambda: any(t == "pixoo_radar/state" and json.loads(p)["flight"] for t, p, _ in messages))

    feed["ac"] = []                                                   # sky empties
    assert wait_for(lambda: api(base).get("note") == "no airborne flights nearby", timeout=10)
    assert wait_for(lambda: json.loads([p for t, p, _ in messages if t == "pixoo_radar/state"][-1])["flight"] is None)

    code, out = stop(proc)
    assert code == 0, out
    assert "Traceback" not in out, out
    rows = sqlite3.connect(tmp_path / "history.sqlite").execute("SELECT callsign, tags, shown FROM sightings").fetchall()
    assert rows == [("UAE29", "rare", 1)]                            # written on shutdown
    assert wait_for(lambda: ("pixoo_radar/availability", "offline") in [(t, p) for t, p, _ in messages])
    client.disconnect()
    client.loop_stop()


def test_pushed_frames(world, tmp_path):
    feed, pixoo, start = world
    proc, base = start(HISTORY="csv", HISTORY_DIR=str(tmp_path))
    assert wait_for(lambda: len(pixoo.named("Draw/SendHttpGif")) >= 27, timeout=20)
    sends = pixoo.named("Draw/SendHttpGif")[:27]
    assert pixoo.named("Draw/ResetHttpGifId") and not pixoo.downloads
    assert [c["PicOffset"] for c in sends] == list(range(27))
    assert {(c["PicNum"], c["PicSpeed"], c["PicWidth"], c["PicID"]) for c in sends} == {(27, 300, 64, 1)}
    assert all(len(base64.b64decode(c["PicData"])) == 64 * 64 * 3 for c in sends)
    time.sleep(3)                                                    # unchanged picture: not re-sent
    assert len(pixoo.named("Draw/SendHttpGif")) == 27
    assert api(base)["mode"] == "pushed frames"
    code, out = stop(proc)
    assert code == 0 and "Traceback" not in out, out
    lines = (tmp_path / "history.csv").read_text().splitlines()
    assert len(lines) == 2 and lines[0].startswith("first_seen,") and ",UAE29,4010ee," in lines[1]


def test_bad_config_exits_cleanly(world):
    _, _, start = world
    proc, _ = start(DATA_SOURCE="adsb,nope", RADAR_RADIUS_KM="ten")
    out, _ = proc.communicate(timeout=10)
    assert proc.returncode == 1 and "RADAR_RADIUS_KM='ten'" in out and "Traceback" not in out
