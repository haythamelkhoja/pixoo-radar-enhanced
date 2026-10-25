import csv
import sqlite3
import time

import pytest
from conftest import make_aircraft

NOW = time.time()


def visit(pr, **kw):
    return (make_aircraft(pr, **kw), (), 6.5)


@pytest.fixture(params=["sqlite", "csv"])
def history(pr, tmp_path, request):
    h = pr.History(request.param, str(tmp_path), retention_days=30, trim_hours=1)
    yield h
    if h.kind == "sqlite":
        try:
            h.db.close()
        except Exception:
            pass


def stored(h):
    return [r for r in h.entries(limit=1000) if not r["active"]]


def test_visit_lifecycle(pr, history):
    history.observe([visit(pr)], now=NOW)
    history.observe([(make_aircraft(pr, altitude=30000), ("rare",), 4.2)], now=NOW + 60)
    assert stored(history) == []                       # still in range: not written yet
    active = history.entries()[0]
    assert active["active"] and active["closest_km"] == 4.2 and active["lowest_ft"] == 30000 and active["tags"] == ["rare"]

    history.observe([], now=NOW + 60 + history.GAP + 1)   # out of sight long enough
    (row,) = stored(history)
    assert (row["flight"], row["callsign"], row["hex"], row["aircraft"], row["origin"], row["destination"]) == \
        ("EK29", "UAE29", "4010ee", "A388", "DXB", "LHR")
    assert row["first_seen"] < row["last_seen"] and row["closest_km"] == 4.2 and row["shown"] is False


def test_mark_shown_keeps_lookup_details(pr, history):
    history.observe([visit(pr, airline=None, flight=None)], now=NOW)
    n = pr.Nearby("EK29", "Emirates", "EK", "UAE", "DXB", "LHR", "A388", "A6-EUA", 36000, 480, 0, 6.5,
                  tags=("rare",), callsign="UAE29", hex="4010ee")
    history.mark_shown(n)
    history.close()
    (row,) = stored(history) if history.kind == "csv" else [None]
    if history.kind == "sqlite":
        db = sqlite3.connect(history.path)
        row = dict(zip(history.FIELDS, db.execute("SELECT * FROM sightings").fetchone(), strict=True))
        assert row["shown"] == 1 and row["tags"] == "rare"
    else:
        assert row["shown"] is True and row["tags"] == ["rare"]
    assert row["flight"] == "EK29" and row["airline"] == "Emirates"


def test_same_aircraft_new_visit_after_gap(pr, history):
    history.observe([visit(pr)], now=NOW)
    history.observe([], now=NOW + history.GAP + 1)
    history.observe([visit(pr)], now=NOW + history.GAP + 100)
    assert len(stored(history)) == 1 and len(history.entries()) == 2


def test_trim(pr, history):
    now = history.trimmed_at                             # trimmed when opened
    history.observe([visit(pr, hex="000001")], now=now - 40 * 86400)
    history.observe([visit(pr, hex="000002")], now=now - 86400)
    history.observe([], now=now + 60)                    # writes both; trimming isn't due yet
    assert len(stored(history)) == 2
    history.observe([], now=now + 3601)                  # an hour later the 40-day-old one goes
    assert [r["hex"] for r in stored(history)] == ["000002"]


def test_keep_forever(pr, tmp_path):
    h = pr.History("csv", str(tmp_path), retention_days=0)
    h.observe([visit(pr)], now=NOW - 400 * 86400)
    h.observe([], now=NOW)
    h.trim(NOW)
    assert len(stored(h)) == 1


def test_persists_across_restarts(pr, history, tmp_path):
    history.observe([visit(pr)], now=NOW)
    history.close()
    again = pr.History(history.kind, str(tmp_path))
    assert len(stored(again)) == 1
    if history.kind == "csv":
        with open(again.path, newline="") as fh:
            assert sum(1 for row in csv.reader(fh) if row[0] == "first_seen") == 1   # header written once
    again.close()


def test_stats(pr, history):
    history.observe([visit(pr), (make_aircraft(pr, hex="ae0001", callsign="RCH1"), ("military",), 20.0)], now=time.time())
    stats = history.stats()
    assert (stats["today"], stats["military_today"], stats["rare_today"]) == (2, 1, 0)
    assert [r["callsign"] for r in stats["recent_highlights"]] == ["RCH1"]


def test_aircraft_without_identity_is_skipped(pr, history):
    history.observe([visit(pr, hex="", callsign="")], now=NOW)
    assert history.entries() == []
