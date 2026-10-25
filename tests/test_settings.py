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
def test_bad_number_exits_with_message(pr, monkeypatch, raw):
    monkeypatch.setenv("RADAR_RADIUS_KM", raw)
    with pytest.raises(SystemExit) as exc:
        pr._setting("RADAR_RADIUS_KM", 100)
    assert "RADAR_RADIUS_KM" in str(exc.value) and "whole number" in str(exc.value)


@pytest.mark.parametrize("raw, mode", [("", "off"), ("off", "off"), ("false", "off"), ("true", "all"), ("ON", "all"),
                                       ("all", "all"), ("digits", "digits"), ("DIGITS", "digits"), ("nonsense", "off")])
def test_split_flap_modes(load_module, raw, mode):
    assert load_module(SPLIT_FLAP=raw).SPLIT_FLAP == mode


def test_defaults(load_module, monkeypatch):
    for name in ("DATA_SOURCE", "HISTORY", "MQTT_HOST", "SPLIT_FLAP"):
        monkeypatch.delenv(name, raising=False)
    m = load_module()
    assert m.DATA_SOURCE == ("fr24",) and m.HISTORY == "off" and m.MQTT_HOST == "" and m.SPLIT_FLAP == "off"
    assert m.IGNORE_HELICOPTERS and m.HIGHLIGHT_MILITARY and m.HIGHLIGHT_RARE and m.SHOW_HEADING


def run_main(module, monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["pixoo_radar.py", *argv])
    with pytest.raises(SystemExit) as exc:
        module.main()
    return str(exc.value)


def test_main_rejects_bad_choices(load_module, monkeypatch):
    m = load_module(DATA_SOURCE="fr24,flightaware", HISTORY="json")
    message = run_main(m, monkeypatch)
    assert "flightaware" in message and "HISTORY='json'" in message


def test_main_requires_location(load_module, monkeypatch):
    monkeypatch.delenv("HOME_LAT")
    message = run_main(load_module(), monkeypatch)
    assert "HOME_LAT" in message
