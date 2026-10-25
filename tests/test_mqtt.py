import asyncio
import json
import threading
import time

import pytest
from conftest import free_port, make_aircraft

jinja2 = pytest.importorskip("jinja2")
paho = pytest.importorskip("paho.mqtt.client")


def render(template, payload):
    """Render a discovery value_template the way Home Assistant does (Jinja2, value_json)."""
    env = jinja2.Environment()
    return env.from_string(template).render(value_json=json.loads(json.dumps(payload)))


@pytest.fixture
def flight(pr, monkeypatch):
    monkeypatch.setattr(pr, "fetch_traffic", lambda: [make_aircraft(pr, aircraft="B748")])
    monkeypatch.setattr(pr.SOURCES["adsb"], "enrich", lambda a: None)
    *_, n = pr.radar_page()
    return pr._flight_status(n), n


class FakeClient:
    """Stands in for paho's Client to test discovery without a broker."""
    def __getattr__(self, name):
        return lambda *a, **k: None


def make_mqtt(pr, monkeypatch, host="127.0.0.1", port=1):
    monkeypatch.setattr(pr, "MQTT_HOST", host)
    monkeypatch.setattr(pr, "MQTT_PORT", port)
    return pr.Mqtt()


def test_discovery_templates_render(pr, monkeypatch, flight):
    flight_status, n = flight
    m = make_mqtt(pr, monkeypatch)
    m.close()
    configs = dict(m.discovery())
    assert len(configs) == len(pr.Mqtt.SENSORS) + 2
    assert all(c["unique_id"].startswith("pixoo_radar_") and c["state_topic"] == "pixoo_radar/state"
               for c in configs.values())
    state = {"flight": flight_status, "traffic": n.traffic, "stats": {"today": 12}}
    values = {topic.split("/")[-2]: render(c["value_template"], state) for topic, c in configs.items()}
    assert values["flight"] == "EK29" and values["route"] == "DXB → LHR" and values["distance"] == "6.5"
    assert values["direction"] == "NE" and values["heading"] == "275.0" and values["highlight"] == "rare"
    assert values["nearby"] == "1" and values["seen_today"] == "12" and values["altitude"] == "36000"
    attributes = configs["homeassistant/sensor/pixoo_radar/flight/config"]["json_attributes_template"]
    assert json.loads(render(attributes, state))["registration"] == "A6-EUA"

    # nothing in range, unknown heading, no history: every template must still render
    empty = {"flight": None, "traffic": [], "stats": None}
    assert all(render(c["value_template"], empty) == "None" for t, c in configs.items() if "nearby" not in t)
    no_heading = {**state, "flight": {**flight_status, "heading": None, "tags": []}}
    assert render(configs["homeassistant/sensor/pixoo_radar/heading/config"]["value_template"], no_heading) == "None"
    assert render(configs["homeassistant/sensor/pixoo_radar/highlight/config"]["value_template"], no_heading) == "none"
    assert json.loads(render(attributes, empty)) == {}


def test_custom_topic_is_sanitised(pr, monkeypatch):
    monkeypatch.setattr(pr, "MQTT_TOPIC", "home/pixoo radar/")
    m = make_mqtt(pr, monkeypatch)
    m.close()
    assert m.topic == "home/pixoo radar" and m.node == "home_pixoo_radar"


@pytest.fixture
def broker():
    amqtt = pytest.importorskip("amqtt.broker")
    port = free_port()
    loop = asyncio.new_event_loop()
    b = amqtt.Broker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
                      "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}}},
                     loop=loop)
    loop.run_until_complete(b.start())
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield port
    try:
        asyncio.run_coroutine_threadsafe(b.shutdown(), loop).result(3)
    except Exception:
        pass   # the test broker can be slow to shut down; it's a daemon thread anyway
    loop.call_soon_threadsafe(loop.stop)


def listen(port, topic="#"):
    messages = []
    c = paho.Client(paho.CallbackAPIVersion.VERSION2)
    c.on_message = lambda cl, u, msg: messages.append((msg.topic, msg.payload.decode(), msg.retain))
    c.connect("127.0.0.1", port)
    c.subscribe(topic)
    c.loop_start()
    return c, messages


def wait_for(check, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


def test_against_a_broker(pr, monkeypatch, broker, flight):
    flight_status, n = flight
    m = make_mqtt(pr, monkeypatch, port=broker)
    assert wait_for(lambda: m.client.is_connected())
    m.publish_state({"flight": flight_status, "traffic": n.traffic})
    m.publish_state({"flight": flight_status, "traffic": n.traffic})   # unchanged: not sent again
    m.spotted(flight_status)

    # a late subscriber (like Home Assistant starting up) gets the retained messages
    client, messages = listen(broker)
    assert wait_for(lambda: any(t == "pixoo_radar/state" for t, *_ in messages))
    by_topic = {t: (p, r) for t, p, r in messages}
    assert by_topic["pixoo_radar/availability"] == ("online", True)
    assert json.loads(by_topic["pixoo_radar/state"][0])["flight"]["flight"] == "EK29"
    assert "homeassistant/sensor/pixoo_radar/flight/config" in by_topic
    assert "pixoo_radar/spotted" not in by_topic            # not retained: only live listeners see it

    live_client, live = listen(broker, "pixoo_radar/spotted")
    time.sleep(0.3)
    m.spotted(flight_status)
    spotted = lambda: [json.loads(p) for t, p, _ in live if t == "pixoo_radar/spotted"]   # noqa: E731
    assert wait_for(lambda: spotted() and spotted()[0]["tags"] == ["rare"])

    m.close()
    assert wait_for(lambda: ("pixoo_radar/availability", "offline", False) in messages or
                    ("pixoo_radar/availability", "offline", True) in messages)
    assert sum(t == "pixoo_radar/state" for t, *_ in messages) == 1
    for c in (client, live_client):
        c.disconnect()
        c.loop_stop()


def test_broker_down_does_not_block(pr, monkeypatch):
    started = time.time()
    m = make_mqtt(pr, monkeypatch, port=free_port())   # nothing listening
    m.publish_state({"flight": None, "traffic": []})
    m.close()
    assert time.time() - started < 5
