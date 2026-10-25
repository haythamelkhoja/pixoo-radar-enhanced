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
    assert body == {"screen": 0, "age_seconds": None}
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
