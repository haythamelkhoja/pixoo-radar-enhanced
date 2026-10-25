from conftest import make_aircraft


def candidates(pr, *aircraft):
    return [(a.callsign, tags) for a, tags, _ in pr._candidates(aircraft)]


def test_filters(pr):
    ok = make_aircraft(pr)
    far_corner = make_aircraft(pr, callsign="BAW1", latitude=51.47 + 0.7, longitude=-0.45 + 1.1)   # ~100+ km
    ground = make_aircraft(pr, callsign="BAW2", on_ground=True)
    heli_type = make_aircraft(pr, callsign="HLE1", aircraft="EC35")
    heli_flag = make_aircraft(pr, callsign="HLE2", aircraft=None, rotorcraft=True)
    private = make_aircraft(pr, callsign="GABCD", airline_flight=False)
    assert candidates(pr, ok, far_corner, ground, heli_type, heli_flag, private) == [("UAE29", ())]


def test_filter_switches(pr, monkeypatch):
    ground = make_aircraft(pr, callsign="BAW2", on_ground=True)
    heli = make_aircraft(pr, callsign="HLE1", aircraft="EC35")
    monkeypatch.setattr(pr, "SHOW_GROUND_TRAFFIC", True)
    monkeypatch.setattr(pr, "IGNORE_HELICOPTERS", False)
    assert {c for c, _ in candidates(pr, ground, heli)} == {"BAW2", "HLE1"}


def test_radius_is_a_circle(pr, monkeypatch):
    monkeypatch.setattr(pr, "RADAR_RADIUS_KM", 10)
    inside = make_aircraft(pr, latitude=51.47 + 0.08)                        # ~8.9 km N
    corner = make_aircraft(pr, callsign="BAW1", latitude=51.47 + 0.08, longitude=-0.45 + 0.13)   # ~12.6 km NE
    assert candidates(pr, inside, corner) == [("UAE29", ())]


def test_military(pr, monkeypatch):
    by_hex = make_aircraft(pr, callsign="RCH123", hex="ae1234", airline_flight=True, aircraft="C17")
    by_flag = make_aircraft(pr, callsign="XXX", hex="111111", airline_flight=False, military=True, aircraft=None)
    by_callsign = make_aircraft(pr, callsign="GAF611", hex="3f0000", airline_flight=False, aircraft=None)
    found = dict(candidates(pr, by_hex, by_flag, by_callsign))
    assert all(tags == ("military",) for tags in found.values()) and len(found) == 3
    monkeypatch.setattr(pr, "HIGHLIGHT_MILITARY", False)
    assert candidates(pr, by_hex, by_flag, by_callsign) == []   # skipped, even with an airline-style callsign


def test_rare(pr, monkeypatch):
    jumbo = make_aircraft(pr, aircraft="B744")
    assert candidates(pr, jumbo) == [("UAE29", ("rare",))]
    monkeypatch.setattr(pr, "RARE_REGISTRATIONS", ("a6-eua",))
    assert pr.is_rare(make_aircraft(pr, registration="A6EUA"))     # dashes don't matter
    monkeypatch.setattr(pr, "HIGHLIGHT_RARE", False)
    assert candidates(pr, jumbo) == [("UAE29", ())]


def fetch_with(pr, monkeypatch, aircraft, enrich=None):
    monkeypatch.setattr(pr, "fetch_traffic", lambda: aircraft)
    for source in pr.SOURCES.values():
        monkeypatch.setattr(source, "enrich", enrich or (lambda a: None))
    return pr.fetch_nearest()


def test_priority(pr, monkeypatch):
    near = make_aircraft(pr)
    far_rare = make_aircraft(pr, callsign="QFA1", flight="QF1", aircraft="B744", latitude=51.30, longitude=-0.70)
    n = fetch_with(pr, monkeypatch, [far_rare, near])
    assert n.flight == "QF1" and n.tags == ("rare",)
    assert [r["flight"] for r in n.traffic] == ["EK29", "QF1"]   # traffic stays closest-first
    monkeypatch.setattr(pr, "PRIORITIZE_HIGHLIGHTS", False)
    assert fetch_with(pr, monkeypatch, [far_rare, near]).flight == "EK29"


def test_nearby_fields(pr, monkeypatch):
    n = fetch_with(pr, monkeypatch, [make_aircraft(pr)])
    assert (n.flight, n.callsign, n.hex, n.source, n.heading) == ("EK29", "UAE29", "4010ee", "adsb", 275.0)
    assert pr.compass(n.bearing) == "NE" and 6 < n.distance_km < 7
    assert fetch_with(pr, monkeypatch, []) is None


def test_skip_unknown_destination_uses_lookups(pr, monkeypatch):
    monkeypatch.setattr(pr, "SKIP_UNKNOWN_DESTINATION", True)
    unknown = make_aircraft(pr, callsign="BAW1", flight=None, destination=None, origin=None)
    known_later = make_aircraft(pr, callsign="BAW2", flight=None, destination=None, latitude=51.6)
    routes = {"BAW2": "JFK"}

    def enrich(a):
        a.destination = routes.get(a.callsign)
    n = fetch_with(pr, monkeypatch, [unknown, known_later], enrich)
    assert n.callsign == "BAW2" and n.destination == "JFK"
    assert fetch_with(pr, monkeypatch, [unknown], enrich) is None


def test_skip_unknown_destination_fr24_prefilter(pr, monkeypatch):
    monkeypatch.setattr(pr, "SKIP_UNKNOWN_DESTINATION", True)
    assert candidates(pr, make_aircraft(pr, source="fr24", destination=None)) == []
    # highlighted aircraft are always shown
    assert candidates(pr, make_aircraft(pr, source="fr24", destination=None, aircraft="B748"))[0][1] == ("rare",)


def test_lookup_revealing_rare_type_adds_badge(pr, monkeypatch):
    def enrich(a):
        a.aircraft = "A124"
    n = fetch_with(pr, monkeypatch, [make_aircraft(pr, aircraft=None)], enrich)
    assert n.tags == ("rare",)


def test_history_sees_every_candidate(pr, monkeypatch, tmp_path):
    monkeypatch.setattr(pr, "history", pr.History("sqlite", str(tmp_path)))
    fetch_with(pr, monkeypatch, [make_aircraft(pr), make_aircraft(pr, hex="400001", callsign="BAW1")])
    assert {e["callsign"] for e in pr.history.entries()} == {"UAE29", "BAW1"}


def test_pinned_flight_wins(pr, monkeypatch):
    near = make_aircraft(pr)
    far = make_aircraft(pr, hex="406d8e", callsign="EZY97ND", flight=None, aircraft="A320", latitude=51.70)
    n = fetch_with(pr, monkeypatch, [near, far])
    assert n.callsign == "UAE29" and not n.pinned
    n = pr.fetch_nearest({"hex": "406d8e", "callsign": "EZY97ND"})
    assert n.callsign == "EZY97ND" and n.pinned and [r["flight"] for r in n.traffic] == ["EK29", "EZY97ND"]


def test_pinned_by_callsign_when_no_address(pr, monkeypatch):
    plane = make_aircraft(pr, hex="", callsign="BAW1", latitude=51.6)
    monkeypatch.setattr(pr, "fetch_traffic", lambda: [make_aircraft(pr), plane])
    monkeypatch.setattr(pr.SOURCES["adsb"], "enrich", lambda a: None)
    assert pr.fetch_nearest({"hex": "", "callsign": "BAW1"}).callsign == "BAW1"


def test_pinned_ignores_filters_but_not_landing(pr, monkeypatch):
    monkeypatch.setattr(pr.SOURCES["adsb"], "enrich", lambda a: None)
    heli = make_aircraft(pr, hex="400001", callsign="HLE1", aircraft="EC35")     # filtered from auto, but pinnable
    monkeypatch.setattr(pr, "fetch_traffic", lambda: [make_aircraft(pr), heli])
    assert pr.fetch_nearest({"hex": "400001"}).callsign == "HLE1"
    landed = make_aircraft(pr, hex="400001", callsign="HLE1", on_ground=True)
    monkeypatch.setattr(pr, "fetch_traffic", lambda: [make_aircraft(pr), landed])
    n = pr.fetch_nearest({"hex": "400001"})
    assert n.callsign == "UAE29" and not n.pinned                                 # landed: auto pick instead


def test_pinned_alone_in_the_sky(pr, monkeypatch):
    monkeypatch.setattr(pr.SOURCES["adsb"], "enrich", lambda a: None)
    private = make_aircraft(pr, hex="400002", callsign="GABCD", airline_flight=False)   # no auto candidates at all
    monkeypatch.setattr(pr, "fetch_traffic", lambda: [private])
    assert pr.fetch_nearest() is None
    assert pr.fetch_nearest({"hex": "400002"}).callsign == "GABCD"
