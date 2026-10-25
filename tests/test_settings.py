import pytest


def test_setting_types(pr, monkeypatch):
    monkeypatch.setenv("X_BOOL", "Yes")
    monkeypatch.setenv("X_INT", " 42 ")
    monkeypatch.setenv("X_FLOAT", "-0.4543")
    monkeypatch.setenv("X_TUPLE", "A388, b748,,")
    monkeypatch.setenv("X_STR", "hello")
    assert pr._setting("X_BOOL", False) is True
    assert pr._setting("X_INT", 1) == 42
    assert pr._setting("X_FLOAT", 0.0) == -0.4543
    assert pr._setting("X_TUPLE", ()) == ("a388", "b748")
    assert pr._setting("X_STR", "") == "hello"
    assert pr._setting("X_UNSET", 7) == 7


@pytest.mark.parametrize("raw", ["100.5", "abc"])
def test_bad_number_is_reported(load_module, raw):
    m = load_module(RADAR_RADIUS_KM=raw)
    assert m.RADAR_RADIUS_KM == 100                         # default kept so the rest can be checked too
    assert any("RADAR_RADIUS_KM" in p and "whole number" in p for p in m.SETTING_PROBLEMS)


@pytest.mark.parametrize("raw, mode", [("", "off"), ("off", "off"), ("false", "off"), ("true", "all"), ("ON", "all"),
                                       ("all", "all"), ("digits", "digits"), ("DIGITS", "digits")])
def test_split_flap_modes(load_module, raw, mode):
    m = load_module(SPLIT_FLAP=raw)
    assert m.SPLIT_FLAP == mode and m.SETTING_PROBLEMS == []


def test_split_flap_unknown(load_module):
    m = load_module(SPLIT_FLAP="nonsense")
    assert m.SPLIT_FLAP == "off" and m.SETTING_PROBLEMS == ["SPLIT_FLAP='nonsense': use off, all or digits"]


@pytest.mark.parametrize("raw, value", [("true", True), ("YES", True), ("1", True), ("on", True),
                                        ("false", False), ("No", False), ("0", False), ("off", False)])
def test_booleans(load_module, raw, value):
    m = load_module(IGNORE_HELICOPTERS=raw)
    assert m.IGNORE_HELICOPTERS is value and m.SETTING_PROBLEMS == []


def test_misspelt_boolean_is_reported(load_module):
    m = load_module(IGNORE_HELICOPTERS="ture", SHOW_HEADING="nope")
    assert m.IGNORE_HELICOPTERS is True and m.SHOW_HEADING is True   # defaults kept
    assert m.SETTING_PROBLEMS == ["IGNORE_HELICOPTERS='ture': use true or false",
                                  "SHOW_HEADING='nope': use true or false"]


@pytest.mark.parametrize("env, expected", [
    ({"HOME_LAT": "91"}, "HOME_LAT=91.0: must be between -90 and 90"),
    ({"HOME_LON": "-181"}, "HOME_LON=-181.0"),
    ({"RADAR_RADIUS_KM": "0"}, "RADAR_RADIUS_KM=0"),
    ({"REFRESH_SECONDS": "5"}, "REFRESH_SECONDS=5: must be at least 10"),
    ({"FRAME_MS": "5"}, "FRAME_MS=5"),
    ({"PIXOO_BRIGHTNESS": "150"}, "PIXOO_BRIGHTNESS=150"),
    ({"COLOR_BOX": "#2B2B2"}, "COLOR_BOX='#2B2B2'"),
    ({"COLOR_TEXT": "yellow"}, "COLOR_TEXT='yellow'"),
    ({"WEB_PORT": "70000"}, "WEB_PORT=70000"),
    ({"MQTT_PORT": "0"}, "MQTT_PORT=0"),
    ({"DATA_SOURCE": "fr24,flightaware"}, "unknown source 'flightaware'"),
    ({"DATA_SOURCE": ","}, "DATA_SOURCE is empty"),
    ({"LOGO_SOURCES": "fr24,logos"}, "LOGO_SOURCES: unknown source 'logos'"),
    ({"ADSB_URL": "api.adsb.lol/v2"}, "ADSB_URL="),
    ({"GIF_BASE_URL": "https://radar.example.com"}, "the Pixoo can't fetch https"),
    ({"GIF_BASE_URL": "radar.lan:8765"}, "must look like http://"),
    ({"HISTORY": "json"}, "HISTORY='json'"),
    ({"HISTORY_RETENTION_DAYS": "-1"}, "HISTORY_RETENTION_DAYS=-1"),
    ({"OPENSKY_CLIENT_ID": "id"}, "set both or neither"),
])
def test_range_checks(load_module, env, expected):
    problems = load_module(**env).SETTING_PROBLEMS
    assert len(problems) == 1 and expected in problems[0], problems


def test_good_values_pass(load_module):
    m = load_module(HOME_LAT="-33.94", HOME_LON="151.17", COLOR_BOX="000000", PIXOO_BRIGHTNESS="0",
                    GIF_BASE_URL="http://192.168.1.20:8765", DATA_SOURCE="adsb,opensky,fr24", LOGO_SOURCES="none")
    assert m.SETTING_PROBLEMS == []


def test_renamed_settings(load_module, monkeypatch):
    for name in ("WEB_PORT", "SHOW_BEARING"):
        monkeypatch.delenv(name, raising=False)
    m = load_module(GIF_PORT="9000", SHOW_DIRECTION="false")
    assert m.WEB_PORT == 9000 and m.SHOW_BEARING is False
    assert sorted(m.RENAMED_SETTINGS) == [("GIF_PORT", "WEB_PORT"), ("SHOW_DIRECTION", "SHOW_BEARING")]
    m = load_module(WEB_PORT="9100")                       # the new name wins
    assert m.WEB_PORT == 9100 and ("GIF_PORT", "WEB_PORT") not in m.RENAMED_SETTINGS


def test_version_sources(load_module, tmp_path, monkeypatch):
    import importlib.util
    import shutil

    assert load_module().__version__ not in ("", "unknown")          # git describe in this checkout
    copy = tmp_path / "app" / "pixoo_radar.py"                         # outside git
    copy.parent.mkdir()
    shutil.copy(load_module().__file__, copy)

    def version_of_copy():
        spec = importlib.util.spec_from_file_location("pixoo_radar_copy", copy)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.__version__

    monkeypatch.delenv("PIXOO_RADAR_VERSION", raising=False)
    assert version_of_copy() == "unknown"
    monkeypatch.setenv("PIXOO_RADAR_VERSION", "v9.9.9")
    assert version_of_copy() == "9.9.9"
    (copy.parent / "VERSION").write_text("v1.2.3\n")                  # written by the Dockerfile / install.sh
    assert version_of_copy() == "1.2.3"


def run_main(module, monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["pixoo_radar.py", *argv])
    with pytest.raises(SystemExit) as exc:
        module.main()
    return str(exc.value)


def test_main_rejects_bad_choices(load_module, monkeypatch):
    m = load_module(DATA_SOURCE="fr24,flightaware", HISTORY="json")
    message = run_main(m, monkeypatch)
    assert "flightaware" in message and "HISTORY='json'" in message


def test_version_flag(load_module, monkeypatch, capsys):
    m = load_module()
    assert run_main(m, monkeypatch, "--version") == "0"
    assert capsys.readouterr().out.strip() == f"Pixoo Radar {m.__version__}"


def test_main_requires_location(load_module, monkeypatch):
    monkeypatch.delenv("HOME_LAT")
    message = run_main(load_module(), monkeypatch)
    assert "HOME_LAT" in message
