import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import free_port, make_aircraft


@pytest.fixture
def host(pr):
    port = free_port()
    h = pr.GifHost(f"http://127.0.0.1:{port}", port)
    h.url = f"http://127.0.0.1:{port}"
    yield h
    h.server.shutdown()
    h.server.server_close()


def get(url, method="GET"):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method=method), timeout=5) as resp:
            return resp.status, resp.headers.get("Content-Type"), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get("Content-Type"), err.read()


def test_pages(pr, host):
    status, ctype, body = get(host.url + "/")
    assert status == 200 and ctype.startswith("text/html") and b"api/current" in body
    assert get(host.url + "/latest.gif")[0] == 404
    name, url = host.publish(b"GIF89a-fake")
    assert get(url) == (200, "image/gif", b"GIF89a-fake") and host.fetched[name].is_set()
    assert get(host.url + "/latest.gif?3")[2] == b"GIF89a-fake"
    assert get(host.url + "/latest.gif", "HEAD")[:2] == (200, "image/gif")
    assert get(host.url + f"/{host.token}/check")[2] == host.check_body
    assert get(host.url + "/nope")[0] == 404


def test_keeps_last_three_gifs(pr, host):
    names = [host.publish(b"g%d" % i)[0] for i in range(5)]
    assert sorted(host.files) == sorted(names[-3:])


def test_health(pr, host, monkeypatch):
    status, _, body = get(host.url + "/health")
    assert status == 200 and json.loads(body)["status"] == "ok"
    host.heartbeat = time.monotonic() - (3 * pr.REFRESH_SECONDS + 601)
    status, _, body = get(host.url + "/health")
    assert status == 503 and json.loads(body)["status"] == "stalled"


def test_api_current(pr, host):
    body = json.loads(get(host.url + "/api/current")[2])
    assert body == {"screen": 0, "age_seconds": None, "pinned": None, "version": pr.__version__}
    host.set_status(flight={"flight": "EK29"}, traffic=[], mode="pushed frames")
    body = json.loads(get(host.url + "/api/current")[2])
    assert body["flight"]["flight"] == "EK29" and body["age_seconds"] == 0


def test_api_history(pr, host, monkeypatch, tmp_path):
    assert json.loads(get(host.url + "/api/history")[2]) == []
    monkeypatch.setattr(pr, "history", pr.History("sqlite", str(tmp_path)))
    pr.history.observe([(make_aircraft(pr, hex=f"{i:06x}"), (), 5.0) for i in range(5)])
    assert len(json.loads(get(host.url + "/api/history")[2])) == 5
    assert len(json.loads(get(host.url + "/api/history?limit=2")[2])) == 2
    assert len(json.loads(get(host.url + "/api/history?limit=abc")[2])) == 5


def test_status_is_json_serialisable(pr, monkeypatch):
    monkeypatch.setattr(pr, "fetch_traffic", lambda: [make_aircraft(pr, aircraft="B748")])
    monkeypatch.setattr(pr.SOURCES["adsb"], "enrich", lambda a: None)
    *_, n = pr.radar_page()
    flight = pr._flight_status(n)
    assert json.loads(json.dumps(flight))["tags"] == ["rare"] and flight["heading_text"] == "← 275°"
    n.heading = None
    assert pr._flight_status(n)["heading_text"] is None


def post(url, body, method="POST", ctype="application/json"):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method=method, headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        body = err.read()
        return err.code, json.loads(body) if body.startswith(b"{") else {}


def test_pin_api(pr, host):
    assert not host.wake.is_set()
    status, body = post(host.url + "/api/pin", {"hex": "406D8E", "callsign": "ezy97nd", "flight": "EZY97ND"})
    assert status == 200 and body["pinned"]["hex"] == "406d8e" and body["pinned"]["callsign"] == "EZY97ND"
    assert host.wake.is_set()                                   # the main loop gets woken up
    assert json.loads(get(host.url + "/api/current")[2])["pinned"]["flight"] == "EZY97ND"
    host.wait(0)
    assert not host.wake.is_set()
    req = urllib.request.Request(host.url + "/api/pin", method="DELETE")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert json.loads(resp.read()) == {"pinned": None}
    assert host.pin is None and host.wake.is_set()


@pytest.mark.parametrize("body, ctype, code", [({"hex": "zzzzzz"}, "application/json", 400),
                                               ({"callsign": "BAW 1"}, "application/json", 400),
                                               ({}, "application/json", 400),
                                               ({"hex": "406d8e"}, "text/plain", 415),
                                               ({"hex": "406d8e"}, "application/x-www-form-urlencoded", 415)])
def test_pin_api_refuses_bad_requests(pr, host, body, ctype, code):
    assert post(host.url + "/api/pin", body, ctype=ctype)[0] == code and host.pin is None


def test_pin_api_other_paths(pr, host):
    assert post(host.url + "/api/other", {"hex": "406d8e"})[0] == 404


def test_page_has_pick_controls(pr, host):
    page = get(host.url + "/")[2].decode()
    assert 'fetch("api/pin"' in page and "Back to auto" in page and "PINNED" in page


def test_health_reports_version(pr, host):
    assert json.loads(get(host.url + "/health")[2])["version"] == pr.__version__
