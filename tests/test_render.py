import io

import pytest
from PIL import Image
from conftest import make_aircraft


@pytest.fixture
def page(pr, monkeypatch):
    def render(**aircraft):
        monkeypatch.setattr(pr, "fetch_traffic", lambda: [make_aircraft(pr, **aircraft)])
        for source in pr.SOURCES.values():
            monkeypatch.setattr(source, "enrich", lambda a: None)
        return pr.radar_page()
    return render


@pytest.mark.parametrize("mode, count, ms", [("off", 27, 300), ("all", 54, 150), ("digits", 54, 150)])
def test_frame_counts(pr, page, monkeypatch, mode, count, ms):
    monkeypatch.setattr(pr, "SPLIT_FLAP", mode)
    frames, frame_ms, summary, n = page()
    assert len(frames) == count and frame_ms == ms and all(f.size == (64, 64) for f in frames)
    assert summary.startswith("EK29 DXB->LHR A388 FL360") and summary.endswith(" NE")


def test_no_flights(pr, monkeypatch):
    monkeypatch.setattr(pr, "fetch_traffic", lambda: [])
    with pytest.raises(LookupError):
        pr.radar_page()


def test_formatting(pr, monkeypatch):
    n = pr.Nearby("EK29", "", "EK", "UAE", "DXB", "LHR", "A388", "A6-EUA", 36975, 488, 1200, 7.44, heading=46)
    assert (pr._fmt_altitude(n), pr._fmt_speed(n), pr._fmt_distance(n)) == ("FL369▲", "488KT↗", "7.4KM")
    n.vspeed, n.altitude, n.distance_km, n.heading = -800, 9500, 12.6, None
    assert (pr._fmt_altitude(n), pr._fmt_speed(n), pr._fmt_distance(n)) == ("9500FT▼", "488KT", "13KM")
    n.altitude, n.speed = 0, None
    assert (pr._fmt_altitude(n), pr._fmt_speed(n)) == ("GND", "---")
    n.speed, n.heading = 488, 180
    monkeypatch.setattr(pr, "SHOW_HEADING", False)
    assert pr._fmt_speed(n) == "488KT"


def test_every_drum_character_has_a_glyph(pr):
    for char in pr.FLAP_DRUM + "↑↗→↘↓↙←↖▲▼":
        glyph = pr.F57[char]
        assert len(glyph) == 7 and len({len(row) for row in glyph}) == 1 and len(glyph[0]) <= 5


def hinge_pixels(pr, img, top):
    """How many tiles show the dark hinge line (only while mid-flip)."""
    y = top + 4 + 3
    return sum(img.getpixel((63 - (pr.FLAP_SLOTS - k) * 6 + 2, y)) == pr.rgb(pr.COLOR_HINGE) for k in range(pr.FLAP_SLOTS))


@pytest.mark.parametrize("mode, flipping", [("all", 1), ("digits", 0)])
def test_digits_mode_only_flips_numbers(pr, monkeypatch, mode, flipping):
    monkeypatch.setattr(pr, "SPLIT_FLAP", mode)
    img = Image.new("RGB", (64, 64))
    pr._draw_flap_field(img, 33, "REG", "G-XLEA", "G-XLEB", 0)
    assert hinge_pixels(pr, img, 33) == flipping          # only a letter changes
    img = Image.new("RGB", (64, 64))
    pr._draw_flap_field(img, 33, "SPD", "450KT", "488KT", 0)
    assert hinge_pixels(pr, img, 33) == 1                 # a digit flips in both modes


def test_flap_settles_on_new_value(pr, monkeypatch):
    monkeypatch.setattr(pr, "SPLIT_FLAP", "all")
    settled, plain = Image.new("RGB", (64, 64)), Image.new("RGB", (64, 64))
    pr._draw_flap_field(settled, 33, "REG", "A6-EUA", "G-XLEB", 100)
    pr._draw_flap_field(plain, 33, "REG", "G-XLEB", "G-XLEB", 0)
    assert settled.tobytes() == plain.tobytes()


def test_flap_sequence(pr):
    assert pr._flap_sequence("A", "A") == []
    assert pr._flap_sequence("A", "D") == ["B", "C", "D"]
    assert pr._flap_sequence("A", "B") == ["B"]
    assert pr._flap_sequence("9", "1")[-1] == "1"        # wraps round the drum
    assert pr._flap_sequence("é", "A") == ["A"]          # not on the drum: straight change
    assert pr._flap_sequence("T", "↗") == ["↗"]          # heading arrows aren't on the drum


def test_highlight_flashes(pr, page):
    frames, *_ = page(aircraft="B748")
    red_or_blue = [f.getpixel((63, 40)) == pr.rgb(pr.COLOR_RARE) for f in frames]
    assert red_or_blue == [step % 9 < 6 for step in range(27)]
    frames, *_ = page(hex="ae0001", callsign="RCH1", aircraft="C17")
    assert frames[0].getpixel((63, 40)) == pr.rgb(pr.COLOR_MILITARY)
    frames, *_ = page()
    assert all(f.getpixel((63, 40)) != pr.rgb(pr.COLOR_RARE) for f in frames)


def test_direction_label(pr, page, monkeypatch):
    frames, *_ = page()
    with_direction = frames[-1].crop((0, 49, 20, 64)).tobytes()
    monkeypatch.setattr(pr, "SHOW_BEARING", False)
    frames, *_ = page()
    assert frames[-1].crop((0, 49, 20, 64)).tobytes() != with_direction


def test_gif_roundtrip(pr, page, monkeypatch):
    monkeypatch.setattr(pr, "SPLIT_FLAP", "all")
    frames, frame_ms, *_ = page(aircraft="B748")
    gif = Image.open(io.BytesIO(pr.encode_gif(frames, frame_ms)))
    assert gif.n_frames == 54 and gif.info.get("loop") == 0       # repeated frames are kept, not merged
    for k, frame in enumerate(frames):
        gif.seek(k)
        assert gif.info["duration"] == 150                          # one delay for every frame
        assert gif.convert("RGB").tobytes() == frame.tobytes()      # lossless: few colours, one palette


def test_preview(pr, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pr, "fetch_traffic", lambda: [make_aircraft(pr)])
    monkeypatch.setattr(pr.SOURCES["adsb"], "enrich", lambda a: None)
    pr.preview(str(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["radar.gif", "radar1.png", "radar2.png", "radar3.png"]
    assert "EK29" in capsys.readouterr().out


def test_pin_badge(pr, page, monkeypatch):
    monkeypatch.setattr(pr.SOURCES["adsb"], "enrich", lambda a: None)
    monkeypatch.setattr(pr, "fetch_traffic", lambda: [make_aircraft(pr)])
    frames, *_ = pr.radar_page({"hex": "4010ee"})
    assert all(f.getpixel((0, 0)) == pr.rgb(pr.COLOR_WHITE) for f in frames)   # steady, not flashing
    frames, *_ = pr.radar_page()
    assert frames[0].getpixel((0, 0)) != pr.rgb(pr.COLOR_WHITE)
    *_, summary, n = pr.radar_page({"hex": "4010ee"})
    assert summary.endswith("[PINNED]") and n.pinned


def test_check_frame(pr):
    img = pr._check_frame()
    assert img.size == (64, 64) and img.getbbox() is not None
