# Configuration

Pixoo Radar is configured entirely with environment variables. Set them however your
deployment does it: `.env` for Docker Compose, `environment:` in `stack.yml`,
`-e` for `docker run`, *Environment variables* in Portainer, or `/etc/pixoo-radar.env`
for the systemd service installed by `install.sh`.

Values are read once at start-up; restart the app after changing them.

Every value is checked at start-up. If anything is wrong (a typo in `true`, a colour
like `#2B2B2`, a latitude of 95, an `https://` `GIF_BASE_URL`...) the app lists **all**
the problems and exits, rather than running with a setting quietly ignored.
`python pixoo_radar.py --check` shows the same list without exiting, plus a test of each
part of the setup.

### Renamed settings

| Old name | New name | Since |
|---|---|---|
| `GIF_PORT` | `WEB_PORT` | 0.4.0 |
| `SHOW_DIRECTION` | `SHOW_BEARING` | 0.4.0 |

The old names still work and log a warning; they'll be removed in 1.0.

## Required

| Variable | Example | Notes |
|---|---|---|
| `PIXOO_HOST` | `192.168.1.50` | IP or hostname of the Pixoo64, as reachable **from the app**. Find the IP in the Divoom app or your router's DHCP list, and give the Pixoo a DHCP reservation so it doesn't change. A port can be added (`192.168.1.50:80`). Not needed for `--preview`. |
| `HOME_LAT` | `51.4700` | Latitude in decimal degrees (north positive). |
| `HOME_LON` | `-0.4543` | Longitude in decimal degrees (east positive, west negative). |

Tip: right-click a spot in Google Maps or OpenStreetMap to copy its coordinates. You
don't need your exact address; a point a few hundred metres away gives the same result.

If any required variable is missing the app exits straight away and tells you which.

## Display

| Variable | Default | Notes |
|---|---|---|
| `COLOR_TEXT` | `#FFFF00` | Flight details (values on the right). `#RRGGBB`. |
| `COLOR_BOX` | `#2B2B2B` | Panels behind the text. `#000000` for pure black. |
| `FRAME_MS` | `300` | Milliseconds per animation frame. One plane pass is 27 frames, so the default loop is about 8 s. |
| `SPLIT_FLAP` | `off` | `all` (or `true`) shows the flight values on a board of 7 character tiles. Each time the panel turns to its next page, the tiles that change flip through a few characters, left to right, like a departures board. The logo, route and labels don't change. The animation has twice as many frames (54), each `FRAME_MS / 2` long, so the plane moves at the same speed. `digits` uses the same tiles but only flips those showing a number; letters change straight away. |
| `SHOW_BEARING` | `true` | (Was `SHOW_DIRECTION`.) Replaces the "DIST" label with the 8-point compass direction from your home to the plane (`N`, `NE`, `E`…), so the last page reads e.g. `SW  26KM`. |
| `SHOW_HEADING` | `true` | Adds an arrow after the speed showing which way the plane is flying, north up (`480KT←` is heading west). This is the plane's own track, different from `SHOW_BEARING` (where it is from you). |
| `PIXOO_BRIGHTNESS` | `-1` | `0`–`100` sets the brightness once at start-up. `-1` leaves the device's setting alone. |
| `LOGO_SOURCES` | `fr24,avcodes` | Comma-separated list of logo sources, tried in order: `fr24` (FlightRadar24) and `avcodes` ([Jxck-S/airline-logos](https://github.com/Jxck-S/airline-logos) banners). Any other value, such as `none`, disables logos and shows the airline name in text. |

In `.env` and `/etc/pixoo-radar.env`, quote colours: `COLOR_BOX="#000000"` (an
unquoted `#` can be read as a comment).

## Flights

| Variable | Default | Notes |
|---|---|---|
| `RADAR_RADIUS_KM` | `100` | Search radius around home, **whole kilometres**. Smaller radii mean closer flights but more "no flights nearby" periods, during which the last flight stays on screen. |
| `REFRESH_SECONDS` | `60` | How often to look for the closest flight (at least 10). The screen is only re-sent when something visible changed. Please don't go much lower: the data source is unofficial (see the disclaimer in the README). |
| `SHOW_GROUND_TRAFFIC` | `false` | `true`/`1`/`yes`/`on` includes aircraft that are parked or taxiing. Handy near an airport if you want to see departures before take-off. |

| `IGNORE_HELICOPTERS` | `true` | Skips helicopters, recognised by their ICAO type code (EC35, H145, AW139, R44…). Their routes are almost never known. |
| `SKIP_UNKNOWN_DESTINATION` | `false` | `true` skips flights whose destination FlightRadar24 doesn't know (positioning, training and some cargo flights). Highlighted aircraft are always shown. |

Apart from highlighted aircraft (below), only flights with an airline flight number are
considered; private traffic without one is skipped.

## Military and rare aircraft

| Variable | Default | Notes |
|---|---|---|
| `HIGHLIGHT_MILITARY` | `true` | Shows military aircraft, which usually have no flight number, with a flashing border and a `MIL` badge. Set `false` to skip them as before. |
| `HIGHLIGHT_RARE` | `true` | Flashing border and `RARE` badge for aircraft matching `RARE_TYPES` or `RARE_REGISTRATIONS`. |
| `PRIORITIZE_HIGHLIGHTS` | `true` | A highlighted aircraft anywhere within `RADAR_RADIUS_KM` is shown before closer ordinary flights (the closest highlighted one wins). `false` keeps strict closest-first and only adds the border. |
| `RARE_TYPES` | see below | Comma-separated ICAO type designators, as FlightRadar24 shows them (`B744`, `A388`…). Setting it **replaces** the default list. |
| `RARE_REGISTRATIONS` | *(empty)* | Comma-separated registrations, e.g. `G-XLEA,A6-EEU` for special liveries you want to catch. |
| `MILITARY_CALLSIGNS` | see below | Comma-separated callsign prefixes or operator ICAO codes treated as military. Setting it replaces the default list. |
| `COLOR_MILITARY` | `#FF3030` | Border and badge colour for military aircraft. |
| `COLOR_RARE` | `#00C8FF` | Border and badge colour for rare aircraft. |

Default `RARE_TYPES`: Boeing 747 variants (`B741`, `B742`, `B743`, `B74S`, `B744`, `B748`),
Airbus A340s (`A342`, `A343`, `A345`, `A346`), `MD11`, `DC10`, `L101`, `A124`, `A225`,
`AN22`, `IL76`, `IL96`, `IL62`, Belugas (`A3ST`, `A337`), the Dreamlifter (`BLCF`),
`CONC`, `DC3`, `DC6`. Rare is local: at a hub where the A380 is everyday it isn't
listed, but you can add it (`RARE_TYPES=a388,b748,...`).

Military detection is best effort, from the live feed:

- the aircraft's 24-bit address is in the US (`AE0000`–`AFFFFF`) or UK (`43C000`–`43CFFF`)
  military blocks,
- its callsign or operator starts with one of `MILITARY_CALLSIGNS` (default: `RCH`
  Reach, `CNV` US Navy, `PAT` US Army, `DUKE`, `RRR` RAF, `CFC` Canada, `GAF` Germany,
  `CTM` France, `BAF` Belgium, `NAF` Netherlands, `IAM` Italy, `AME` Spain, `HAF`
  Greece, `PLF` Poland, `SVF` Sweden, `ASY` Australia, `KIW` New Zealand, `NATO`),
- or its type is a military one (C-17, C-130, KC-135, A400M, E-3, P-8, fighters…).

FlightRadar24 hides many military flights, so expect to see transports and tankers
rather than fighters.

## Data sources

| Variable | Default | Notes |
|---|---|---|
| `DATA_SOURCE` | `fr24` | Where positions come from: `fr24`, `adsb` or `opensky`. A comma-separated list is tried in order, so `adsb,fr24` uses adsb.lol and falls back to FlightRadar24 when it's down. |
| `ADSB_URL` | `https://api.adsb.lol/v2/point/{lat}/{lon}/{radius_nm}` | For `adsb`: any readsb-style JSON. The default is the free [adsb.lol](https://adsb.lol) API. Point it at your own receiver to use your own antenna: `http://pi.local/tar1090/data/aircraft.json` (readsb, tar1090, dump1090-fa all work). `{lat}`, `{lon}`, `{radius_nm}` and `{radius_km}` are filled in if present. |
| `LOOKUP_ADSBDB` | `true` | For `adsb` and `opensky`: look up the flight on screen at [adsbdb.com](https://www.adsbdb.com) for its flight number, airline, route, type and registration (cached for a day). |
| `OPENSKY_CLIENT_ID` | *(empty)* | Optional [OpenSky](https://opensky-network.org) API client (from your OpenSky account page). Without one, OpenSky allows ~400 requests a day, so the app asks at most every 4 minutes; with one, every 30 seconds. |
| `OPENSKY_CLIENT_SECRET` | *(empty)* | The client's secret. |

What each source gives you:

| | `fr24` | `adsb` (adsb.lol) | `adsb` (your receiver) | `opensky` |
|---|---|---|---|---|
| Positions | ✓ | ✓ | ✓ (your antenna's range) | ✓ (can be up to 4 min old without an account) |
| Type, registration | ✓ | ✓ | ✓ with its aircraft database | via adsbdb |
| Route, airline, flight number | ✓ | via adsbdb | via adsbdb | via adsbdb |
| Military flag | heuristic | ✓ (database) + heuristic | ✓ with database + heuristic | heuristic |
| Helicopter flag | by type | ✓ (category) + type | ✓ (category) + type | ✓ (category) + type |
| Terms | unofficial, personal use | free, ODbL data | yours | free for non-commercial use |

Routes from adsbdb come from a callsign database, so they're right for scheduled
flights and can be missing or out of date for charters and new routes. The flight
number shown is the callsign-based one (e.g. `VY26CC`), which can differ from the
marketed number FlightRadar24 shows (`VY7504`).

## History

| Variable | Default | Notes |
|---|---|---|
| `HISTORY` | `off` | `sqlite` or `csv` records every aircraft that passes through your radius: one entry per visit with first and last seen times (UTC), flight, callsign, address, airline, type, registration, route, closest distance, lowest altitude, MIL/RARE tags, whether it was on screen, and the data source. |
| `HISTORY_DIR` | `data` (`/data` in Docker, `/var/lib/pixoo-radar` with install.sh) | Folder for `history.sqlite` or `history.csv`. |
| `HISTORY_RETENTION_DAYS` | `30` | Entries older than this are removed. `0` keeps everything. |
| `HISTORY_TRIM_HOURS` | `1` | How often old entries are removed. |

A visit is written once the aircraft has been out of range for 10 minutes, and any
visits in progress are written when the app stops. The live page shows counts for
today, and `GET /api/history?limit=100` returns the most recent visits as JSON
(including ones still in progress).

In Docker, mount a volume at `/data` to keep the history across container updates.
`compose.yaml` already does this. A bind mount must be writable by the container's
user (uid 10001): `sudo chown 10001 ./data`.

The CSV opens straight in Excel, Numbers or Google Sheets. The SQLite file works
with any SQLite tool, e.g. the busiest types this week:

```sql
SELECT aircraft, COUNT(*) FROM sightings
WHERE first_seen >= date('now', '-7 days') GROUP BY aircraft ORDER BY 2 DESC LIMIT 10;
```

## MQTT and Home Assistant

Off unless `MQTT_HOST` is set.

| Variable | Default | Notes |
|---|---|---|
| `MQTT_HOST` | *(empty)* | Broker address, e.g. `192.168.1.10` or `core-mosquitto`. |
| `MQTT_PORT` | `1883` | `8883` is usual with TLS. |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | *(empty)* | Broker login, if it needs one. |
| `MQTT_TLS` | `false` | `true` connects with TLS (system CA certificates). |
| `MQTT_TOPIC` | `pixoo_radar` | Base topic. |
| `MQTT_DISCOVERY` | `true` | Publish Home Assistant discovery so the sensors appear by themselves. |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | Home Assistant's discovery prefix. |

Topics:

| Topic | Retained | Content |
|---|---|---|
| `pixoo_radar/state` | yes | JSON: `flight` (the closest flight now, or `null` when nothing is in range), `traffic` (up to 15 aircraft), `stats` (with history on), `mode`, `source`, `updated` |
| `pixoo_radar/availability` | yes | `online` / `offline` (also sent by the broker if the app disappears) |
| `pixoo_radar/spotted` | no | JSON of a military or rare flight, once, when it comes on screen |

Home Assistant gets one device, **Pixoo Radar**, with sensors for flight, airline,
route, aircraft type, registration, altitude, speed, distance, direction, heading,
highlight, aircraft nearby and (with history) aircraft seen today. The Flight sensor
carries every field as attributes.

Example automation: announce rare and military aircraft.

```yaml
automation:
  - alias: Plane spotting
    trigger:
      - platform: mqtt
        topic: pixoo_radar/spotted
    action:
      - service: notify.mobile_app_phone
        data:
          title: "{{ trigger.payload_json.tags | join(' & ') | upper }} overhead"
          message: >-
            {{ trigger.payload_json.flight }} {{ trigger.payload_json.aircraft }},
            {{ trigger.payload_json.distance_km | round(0) }} km {{ trigger.payload_json.direction }}
```

## Hosted GIFs (no "Loading.." screen)

| Variable | Default | Notes |
|---|---|---|
| `GIF_BASE_URL` | *(empty)* | Base URL **the Pixoo** uses to download animations from this app, e.g. `http://192.168.1.20:8765`. Must be plain `http` and must not redirect. Point it at the app's own port, not a reverse proxy. A hostname is resolved by the app and handed to the Pixoo as an IP, so the Pixoo doesn't need working DNS for it. Empty means push frames instead (shows "Loading.." on each update). |
| `WEB_PORT` | `8765` | (Was `GIF_PORT`.) Port the built-in web server listens on. It serves the hosted GIFs, the live web page at `/`, the API and `/health`. If you change it, change the published port (`-p`, `ports:`) and `GIF_BASE_URL` to match. |
| `SELF_CHECK_WAIT` | `120` | Seconds to wait at start-up for `GIF_BASE_URL` to start answering before giving up on testing it. Docker Swarm doesn't route a published port to a task until the task's healthcheck passes, so the app's own address refuses connections for the first half-minute. Lower it only if start-up feels slow. |
| `HOSTED_RETRY_SECONDS` | `1800` | After the Pixoo fails to fetch a hosted GIF the app pushes frames instead, then re-tests the URL this often and returns to hosted GIFs when it works. Raise it if a blocked Pixoo makes the screen stutter every half hour. |

Examples:

| Setup | `GIF_BASE_URL` |
|---|---|
| Docker / Compose on a host at 192.168.1.20 | `http://192.168.1.20:8765` |
| Swarm, port 8765 published on every node | `http://any-node-name:8765` |
| `install.sh` on an LXC container at 192.168.1.30 | `http://192.168.1.30:8765` |
| Docker with `-p 9000:8765` | `http://192.168.1.20:9000` |

Not `https://...`, and not `http://radar.example.com` if your proxy redirects that to HTTPS.

## Other

| Variable | Default | Notes |
|---|---|---|
| `TZ` | `UTC` | Timezone for log timestamps, e.g. `Europe/London`. The Docker image includes tzdata. |

## Command line

```text
python pixoo_radar.py                    run
python pixoo_radar.py --preview DIR      render the current closest flight to DIR and exit
```

`--preview` writes `radar1.png`–`radar3.png` (one per info panel, scaled to 512×512) and
`radar.gif` (exactly what the Pixoo would play). It needs `HOME_LAT` and `HOME_LON`
but no Pixoo. With Docker:

```sh
docker run --rm -e HOME_LAT=51.47 -e HOME_LON=-0.4543 -v "$PWD/preview:/preview" \
  --user "$(id -u)" ghcr.io/haythamelkhoja/pixoo-radar-enhanced:latest \
  python /app/pixoo_radar.py --preview /preview
```
