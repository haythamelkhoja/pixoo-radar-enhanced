import importlib.util
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "pixoo_radar.py"

BASE_ENV = {"HOME_LAT": "51.47", "HOME_LON": "-0.45", "LOGO_SOURCES": "none", "PIXOO_HOST": "127.0.0.1:9"}
os.environ.update(BASE_ENV)
sys.path.insert(0, str(SCRIPT.parent))

import pixoo_radar  # noqa: E402


@pytest.fixture
def pr(monkeypatch):
    """The module, with any settings a test changes restored afterwards."""
    monkeypatch.setattr(pixoo_radar, "history", None)
    pixoo_radar._lookups.clear()
    pixoo_radar._logos.clear()
    return pixoo_radar


@pytest.fixture
def load_module(monkeypatch):
    """Import a fresh copy of the script under a given environment (settings are read at import)."""
    counter = iter(range(10**6))

    def load(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        spec = importlib.util.spec_from_file_location(f"pixoo_radar_{next(counter)}", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return load


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_aircraft(pr, **kw):
    """An airborne airline flight about 6.5 km NE of home, overridable field by field."""
    fields = dict(source="adsb", hex="4010ee", callsign="UAE29", latitude=51.52, longitude=-0.40, altitude=36000,
                  speed=480, vspeed=0, heading=275.0, airline_flight=True, airline_icao="UAE", aircraft="A388",
                  registration="A6-EUA", flight="EK29", origin="DXB", destination="LHR")
    fields.update(kw)
    return pr.Aircraft(**fields)


def fr24_flight(**kw):
    """A FlightRadarAPI Flight look-alike."""
    fields = dict(number="EK29", callsign="UAE29", aircraft_code="A388", registration="A6-EUA", latitude=51.52,
                  longitude=-0.40, altitude=36975, ground_speed=488, vertical_speed=1200, heading=275, on_ground=0,
                  icao_24bit="896177", airline_icao="UAE", airline_iata="EK", origin_airport_iata="DXB",
                  destination_airport_iata="LHR")
    fields.update(kw)
    return SimpleNamespace(**fields)


class FakeResponse:
    def __init__(self, payload=None, status=200, headers=None):
        self.payload, self.status_code, self.headers = payload, status, headers or {}
        self.ok = status < 400

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
