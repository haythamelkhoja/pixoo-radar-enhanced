import pytest


def test_haversine_london_paris(pr):
    assert pr.haversine_km(51.5074, -0.1278, 48.8566, 2.3522) == pytest.approx(343.5, abs=1)


@pytest.mark.parametrize("lat, lon, expected", [(52.47, -0.45, 0), (51.47, 1.0, 90), (50.47, -0.45, 180),
                                                (51.47, -2.0, 270)])
def test_bearing_cardinal(pr, lat, lon, expected):
    assert pr.bearing_deg(51.47, -0.45, lat, lon) == pytest.approx(expected, abs=1.5)


@pytest.mark.parametrize("deg, point, glyph", [(0, "N", "↑"), (22, "N", "↑"), (23, "NE", "↗"), (90, "E", "→"),
                                                (135, "SE", "↘"), (180, "S", "↓"), (225, "SW", "↙"),
                                                (270, "W", "←"), (315, "NW", "↖"), (359.9, "N", "↑")])
def test_compass_and_arrow(pr, deg, point, glyph):
    assert pr.compass(deg) == point
    assert pr.arrow(deg) == glyph
