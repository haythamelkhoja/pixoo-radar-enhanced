import pytest
from conftest import FakeResponse, fr24_flight


# --- FlightRadar24 -------------------------------------------------------------

def test_fr24_conversion(pr):
    a = pr.FR24Source.aircraft(fr24_flight())
    assert (a.source, a.hex, a.callsign, a.flight, a.aircraft, a.heading) == ("fr24", "896177", "UAE29", "EK29", "A388", 275)
    assert a.airline_flight and not a.on_ground and (a.origin, a.destination) == ("DXB", "LHR")


def test_fr24_missing_values_and_ground(pr):
    a = pr.FR24Source.aircraft(fr24_flight(number="N/A", airline_iata="N/A", aircraft_code="N/A", altitude=0,
                                           ground_speed=0, icao_24bit="N/A", heading="N/A"))
    assert a.flight is None and a.aircraft is None and a.hex == "" and a.heading is None
    assert a.on_ground and not a.airline_flight
    assert pr.FR24Source.aircraft(fr24_flight(on_ground=1)).on_ground


def test_fr24_fetch_skips_positionless(pr, monkeypatch):
    monkeypatch.setattr(pr.fr24, "get_bounds_by_point", lambda *a: "bounds")
    monkeypatch.setattr(pr.fr24, "get_flights", lambda bounds: [fr24_flight(), fr24_flight(latitude=None)])
    assert len(pr.SOURCES["fr24"].fetch()) == 1


def test_fr24_enrich(pr, monkeypatch):
    details = {"identification": {"number": {"default": "EK30"}}, "airline": {"name": "Emirates",
               "code": {"iata": "EK", "icao": "UAE"}}, "airport": {"origin": {"code": {"iata": "DWC"}}},
               "aircraft": {"model": {"code": "A38X"}}}
    monkeypatch.setattr(pr.fr24, "get_flight_details", lambda f: details)
    a = pr.FR24Source.aircraft(fr24_flight())
    pr.SOURCES["fr24"].enrich(a)
    assert (a.flight, a.airline, a.origin, a.destination, a.aircraft) == ("EK30", "Emirates", "DWC", "LHR", "A38X")


def test_fr24_enrich_failure_keeps_feed(pr, monkeypatch):
    monkeypatch.setattr(pr.fr24, "get_flight_details", lambda f: (_ for _ in ()).throw(RuntimeError("blocked")))
    a = pr.FR24Source.aircraft(fr24_flight())
    pr.SOURCES["fr24"].enrich(a)
    assert a.flight == "EK29" and a.airline is None and a.destination == "LHR"


# --- readsb / adsb.lol ---------------------------------------------------------

ADSB_ROW = {"hex": "406d8e", "flight": "EZY97ND ", "r": "G-EZOI", "t": "A320", "alt_baro": 20975, "gs": 470.2,
            "track": 158.02, "baro_rate": 1088, "category": "A3", "lat": 51.647, "lon": -1.023}


def test_adsb_conversion(pr):
    a = pr.ADSBSource.aircraft(ADSB_ROW)
    assert (a.hex, a.callsign, a.registration, a.aircraft, a.altitude, a.heading, a.vspeed) == \
        ("406d8e", "EZY97ND", "G-EZOI", "A320", 20975, 158.02, 1088)
    assert a.airline_flight and a.airline_icao == "EZY" and not a.military and not a.rotorcraft


@pytest.mark.parametrize("row, check", [
    ({**ADSB_ROW, "alt_baro": "ground"}, lambda a: a.on_ground and a.altitude == 0),
    ({**ADSB_ROW, "alt_baro": None, "alt_geom": 1500}, lambda a: a.altitude == 1500),
    ({**ADSB_ROW, "hex": "~2a0001"}, lambda a: a.hex == "2a0001"),
    ({**ADSB_ROW, "dbFlags": 1}, lambda a: a.military),
    ({**ADSB_ROW, "dbFlags": 2}, lambda a: not a.military),
    ({**ADSB_ROW, "category": "A7"}, lambda a: a.rotorcraft),
    ({**ADSB_ROW, "flight": "GEZOI"}, lambda a: not a.airline_flight and a.airline_icao is None),
    ({**ADSB_ROW, "track": None, "true_heading": 12}, lambda a: a.heading == 12),
])
def test_adsb_variants(pr, row, check):
    assert check(pr.ADSBSource.aircraft(row))


@pytest.mark.parametrize("row", [{**ADSB_ROW, "lat": None}, {"hex": "abc"}, "junk", None])
def test_adsb_unusable_rows(pr, row):
    assert pr.ADSBSource.aircraft(row) is None


def test_adsb_fetch(pr, monkeypatch):
    urls = []

    def get(url, **kw):
        urls.append(url)
        return FakeResponse({"ac": [ADSB_ROW, {"hex": "x"}]} if "adsb.lol" in url else {"aircraft": [ADSB_ROW]})
    monkeypatch.setattr(pr.requests, "get", get)
    monkeypatch.setattr(pr, "RADAR_RADIUS_KM", 1000)
    assert len(pr.SOURCES["adsb"].fetch()) == 1
    assert urls[-1] == "https://api.adsb.lol/v2/point/51.47/-0.45/250"   # capped at the API's maximum
    monkeypatch.setattr(pr, "ADSB_URL", "http://receiver/tar1090/data/aircraft.json")
    assert len(pr.SOURCES["adsb"].fetch()) == 1


def test_adsb_bad_payload(pr, monkeypatch):
    monkeypatch.setattr(pr.requests, "get", lambda url, **kw: FakeResponse({"msg": "rate limited"}))
    with pytest.raises(ValueError):
        pr.SOURCES["adsb"].fetch()


# --- OpenSky -------------------------------------------------------------------

STATE = ["46b828", "AEE2EG  ", "Greece", 1, 1, -0.0615, 51.5756, 2240.28, False, 149.36, 115.06, -2.6,
         None, 2202.18, "3127", False, 0, 3]


def test_opensky_conversion(pr):
    a = pr.OpenSkySource.aircraft(STATE)
    assert (a.hex, a.callsign, a.altitude, a.speed, a.vspeed, a.heading) == ("46b828", "AEE2EG", 7350, 290, -512, 115.06)
    assert a.airline_flight and a.airline_icao == "AEE" and not a.rotorcraft and not a.on_ground


def test_opensky_variants(pr):
    assert pr.OpenSkySource.aircraft(STATE[:12]) is not None                 # no extended fields
    assert pr.OpenSkySource.aircraft(STATE[:5]) is None
    assert pr.OpenSkySource.aircraft(STATE[:6] + [None] + STATE[7:]) is None   # no latitude
    assert pr.OpenSkySource.aircraft(STATE[:17] + [8]).rotorcraft
    ground = pr.OpenSkySource.aircraft(STATE[:7] + [None, True] + STATE[9:])
    assert ground.on_ground and ground.altitude == 0
    no_alt = pr.OpenSkySource.aircraft(STATE[:7] + [None] + STATE[8:13] + [None] + STATE[14:])
    assert no_alt.altitude is None and pr.OpenSkySource.aircraft(STATE[:9] + [None] + STATE[10:]).speed is None


def test_opensky_rate_limiting_and_auth(pr, monkeypatch):
    source = pr.OpenSkySource()
    calls, posts, clock = [], [], [1000.0]
    monkeypatch.setattr(pr.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(pr.requests, "get", lambda url, **kw: calls.append(kw) or FakeResponse({"states": [STATE]}))
    assert len(source.fetch()) == 1 and len(source.fetch()) == 1 and len(calls) == 1   # cached within 240 s
    clock[0] += 241
    source.fetch()
    assert len(calls) == 2 and "Authorization" not in calls[-1]["headers"]

    monkeypatch.setattr(pr, "OPENSKY_CLIENT_ID", "id")
    monkeypatch.setattr(pr, "OPENSKY_CLIENT_SECRET", "secret")
    monkeypatch.setattr(pr.requests, "post", lambda url, **kw: posts.append(kw) or
                        FakeResponse({"access_token": "tok", "expires_in": 1800}))
    clock[0] += 31
    source.fetch()
    clock[0] += 31
    source.fetch()
    assert len(calls) == 4 and len(posts) == 1 and calls[-1]["headers"]["Authorization"] == "Bearer tok"


def test_opensky_quota_exhausted(pr, monkeypatch):
    monkeypatch.setattr(pr.requests, "get", lambda url, **kw: FakeResponse(
        {}, 429, {"X-Rate-Limit-Retry-After-Seconds": "3600"}))
    with pytest.raises(RuntimeError, match="3600"):
        pr.OpenSkySource().fetch()


def test_opensky_empty_sky(pr, monkeypatch):
    monkeypatch.setattr(pr.requests, "get", lambda url, **kw: FakeResponse({"time": 1, "states": None}))
    assert pr.OpenSkySource().fetch() == []


# --- adsbdb lookups ------------------------------------------------------------

ROUTE = {"response": {"flightroute": {"callsign_iata": "BA135", "airline": {"name": "British Airways", "iata": "BA",
         "icao": "BAW"}, "origin": {"iata_code": "LHR"}, "destination": {"iata_code": "BOM"}}}}
CRAFT = {"response": {"aircraft": {"icao_type": "B77W", "registration": "G-STBA"}}}


def test_adsbdb_fills_gaps(pr, monkeypatch):
    calls = []

    def get(url, **kw):
        calls.append(url)
        return FakeResponse(ROUTE if "/callsign/" in url else CRAFT)
    monkeypatch.setattr(pr.requests, "get", get)
    a = pr.OpenSkySource.aircraft(["400a0b", "BAW135", "", 1, 1, -0.4, 51.5, 3000, False, 200, 90, 0])
    pr.lookup_adsbdb(a)
    assert (a.flight, a.airline, a.airline_iata, a.origin, a.destination, a.aircraft, a.registration) == \
        ("BA135", "British Airways", "BA", "LHR", "BOM", "B77W", "G-STBA")
    pr.lookup_adsbdb(a)
    assert len(calls) == 2   # route cached; type/registration now known


def test_adsbdb_misses_and_errors_are_cached(pr, monkeypatch):
    calls = []
    monkeypatch.setattr(pr.requests, "get", lambda url, **kw: calls.append(url) or FakeResponse(
        {"response": "unknown callsign"}, 404))
    a = pr.OpenSkySource.aircraft(["400a0b", "BAW999", "", 1, 1, -0.4, 51.5, 3000, False, 200, 90, 0])
    pr.lookup_adsbdb(a)
    pr.lookup_adsbdb(a)
    assert len(calls) == 2 and a.flight is None   # one route miss, one aircraft miss, both cached

    pr._lookups.clear()
    monkeypatch.setattr(pr.requests, "get", lambda url, **kw: (_ for _ in ()).throw(OSError("offline")))
    pr.lookup_adsbdb(a)   # must not raise
    assert a.destination is None


def test_adsbdb_skips_private_callsigns_and_can_be_off(pr, monkeypatch):
    calls = []
    monkeypatch.setattr(pr.requests, "get", lambda url, **kw: calls.append(url) or FakeResponse(CRAFT))
    a = pr.ADSBSource.aircraft({**ADSB_ROW, "flight": "GEZOI", "t": None})
    pr.lookup_adsbdb(a)
    assert calls == ["https://api.adsbdb.com/v0/aircraft/406d8e"] and a.aircraft == "B77W"
    monkeypatch.setattr(pr, "LOOKUP_ADSBDB", False)
    pr.lookup_adsbdb(pr.ADSBSource.aircraft({**ADSB_ROW, "t": None}))
    assert len(calls) == 1


# --- falling back between sources --------------------------------------------

def test_fetch_traffic_falls_back(pr, monkeypatch):
    monkeypatch.setattr(pr, "DATA_SOURCE", ("adsb", "fr24"))
    monkeypatch.setattr(pr.SOURCES["adsb"], "fetch", lambda: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(pr.SOURCES["fr24"], "fetch", lambda: ["from fr24"])
    assert pr.fetch_traffic() == ["from fr24"]
    monkeypatch.setattr(pr.SOURCES["fr24"], "fetch", lambda: (_ for _ in ()).throw(OSError("also down")))
    with pytest.raises(OSError, match="also down"):
        pr.fetch_traffic()
