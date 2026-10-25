# Pixoo Radar

Shows the airborne flight closest to your home on a
[Divoom Pixoo64](https://divoom.com/products/pixoo-64): airline logo, route with an
animated plane, and a split-flap panel cycling flight number, altitude, aircraft type,
registration, speed and distance.

<p align="center">
  <img src="docs/images/preview.gif" width="320" alt="Animated Pixoo Radar screen">
</p>

![The three info panels](docs/images/preview-panels.png)

*Rendered with `--preview`. Airline logos are switched off in these images
(`LOGO_SOURCES=none`); on the device the airline name is replaced by its logo.*

## Features

- **Closest flight, live**: polls every `REFRESH_SECONDS` within `RADAR_RADIUS_KM` of
  your location and shows the nearest airborne aircraft.
- **Your choice of data**: FlightRadar24, [adsb.lol](https://adsb.lol), your own ADS-B
  receiver (readsb/tar1090/dump1090), or [OpenSky](https://opensky-network.org), with
  fallbacks. Routes and types for the free sources come from [adsbdb.com](https://www.adsbdb.com).
- **No "Loading.." screen**: with `GIF_BASE_URL` set, the app serves each animation as a
  GIF and the Pixoo fetches and plays it itself, so the picture just swaps in.
  Without it, frames are pushed over the Pixoo's API (works anywhere, shows "Loading..").
- **Airline logos** fitted to the 64×20 strip: backgrounds keyed out, thin strokes
  firmed up, dark logos lifted so they're visible on black.
- **Climb/descent arrows**, flight levels above 10,000 ft, km distance, the compass
  direction to look in (`SW  26KM`) and an arrow for the way the plane is heading (`480KT←`).
- **Military and rare aircraft** get a flashing border and a MIL/RARE badge, and are shown
  ahead of closer ordinary flights. You can edit the list of rare aircraft types.
- **Split-flap option**: the flight values on departures-board tiles that flip, for every
  character or digits only.
- **Helicopters ignored** by default, optionally anything without a known destination.
- **Live web page**: open `http://<host>:8765` for the current screen, the flight's
  details, a table of nearby traffic and today's counts, updating every few seconds.
- **History** (optional): every aircraft that passes, in SQLite or CSV, trimmed after
  `HISTORY_RETENTION_DAYS`.
- **MQTT and Home Assistant** (optional): sensors appear by themselves, plus a
  "spotted" event for military and rare aircraft.
- **Self-diagnosing**: checks that the Pixoo can reach it, falls back to pushing frames
  if not, and says why in the logs.
- **`/health` endpoint** and a Docker `HEALTHCHECK`.
- **Runs anywhere**: multi-arch image (amd64 + arm64: PCs, NAS, Raspberry Pi), Compose,
  Swarm, Portainer, or a plain systemd service via `install.sh` (LXC, Pi, VM).

## Quick start

You need a Pixoo64 on your network and its IP address (Divoom app → device settings).

```sh
mkdir pixoo-radar && cd pixoo-radar
curl -fsSLO https://raw.githubusercontent.com/haythamelkhoja/pixoo-radar-enhanced/main/compose.yaml
curl -fsSL  https://raw.githubusercontent.com/haythamelkhoja/pixoo-radar-enhanced/main/.env.example -o .env
nano .env                  # set PIXOO_HOST, HOME_LAT, HOME_LON, GIF_BASE_URL
docker compose up -d
docker compose logs -f
```

Not using Docker? On Debian, Ubuntu, Raspberry Pi OS or a Proxmox LXC container:

```sh
curl -fsSL https://raw.githubusercontent.com/haythamelkhoja/pixoo-radar-enhanced/main/install.sh | sudo bash
```

Just want to try it without a Pixoo?

```sh
pip install -r requirements.txt
HOME_LAT=51.4700 HOME_LON=-0.4543 python src/pixoo_radar.py --preview ./preview
```

## Configuration

Everything is an environment variable. Only the first three are required.

| Variable | Default | Description |
|---|---|---|
| `PIXOO_HOST` | *(required)* | IP or hostname of the Pixoo64. Not needed for `--preview`. |
| `HOME_LAT` | *(required)* | Your latitude in decimal degrees, e.g. `51.4700`. |
| `HOME_LON` | *(required)* | Your longitude in decimal degrees, e.g. `-0.4543`. |
| `GIF_BASE_URL` | *(empty)* | Plain-`http` address where the **Pixoo** can reach this app, e.g. `http://192.168.1.20:8765`. Enables hosted GIFs (no "Loading.."). Empty = push frames. |
| `GIF_PORT` | `8765` | Port of the built-in web server (hosted GIFs, browser view, `/health`). |
| `RADAR_RADIUS_KM` | `100` | Search radius around home, whole kilometres. |
| `REFRESH_SECONDS` | `60` | How often to look for the closest flight. |
| `SHOW_GROUND_TRAFFIC` | `false` | `true` to include parked and taxiing aircraft. |
| `DATA_SOURCE` | `fr24` | `fr24`, `adsb` or `opensky`; a list like `adsb,fr24` falls back in order. |
| `ADSB_URL` | adsb.lol API | readsb-style JSON for `adsb`, e.g. your receiver's `http://pi.local/tar1090/data/aircraft.json`. |
| `LOOKUP_ADSBDB` | `true` | Route, airline, type and registration from adsbdb.com for `adsb`/`opensky`. |
| `OPENSKY_CLIENT_ID` / `OPENSKY_CLIENT_SECRET` | *(empty)* | Optional OpenSky API client, for 30 s updates instead of 4 min. |
| `IGNORE_HELICOPTERS` | `true` | Skip helicopters (their routes are almost never known). |
| `SKIP_UNKNOWN_DESTINATION` | `false` | `true` skips flights with no known destination. |
| `HIGHLIGHT_MILITARY` | `true` | Show military aircraft (they usually have no flight number) with a red border and MIL badge. |
| `HIGHLIGHT_RARE` | `true` | Blue border and RARE badge for types in `RARE_TYPES` and registrations in `RARE_REGISTRATIONS`. |
| `PRIORITIZE_HIGHLIGHTS` | `true` | A highlighted aircraft anywhere in range is shown before closer ordinary flights. |
| `RARE_TYPES` | 747s, A340s, MD-11, An-124, Beluga… | Comma-separated ICAO type codes that count as rare. Replaces the default list. |
| `RARE_REGISTRATIONS` | *(empty)* | Comma-separated registrations that count as rare, e.g. special liveries. |
| `MILITARY_CALLSIGNS` | `rch,cnv,pat,…` | Callsign prefixes / operator codes treated as military (on top of US/UK military addresses and military types). |
| `FRAME_MS` | `300` | Animation frame time in milliseconds. |
| `SHOW_DIRECTION` | `true` | Show the compass direction to the plane (`NE`) in place of the "DIST" label. |
| `SHOW_HEADING` | `true` | Arrow after the speed for the way the plane is flying. |
| `SPLIT_FLAP` | `off` | `all` puts the flight values on departures-board tiles that flip to each new value; `digits` flips only tiles showing a number. |
| `PIXOO_BRIGHTNESS` | `-1` | Set brightness `0`–`100` at start-up; `-1` leaves it alone. |
| `LOGO_SOURCES` | `fr24,avcodes` | Where to fetch airline logos, tried in order. `none` shows the airline name instead. |
| `COLOR_TEXT` | `#FFFF00` | Colour of flight details. |
| `COLOR_BOX` | `#2B2B2B` | Colour of the panels behind the text. |
| `COLOR_MILITARY` | `#FF3030` | Border and badge colour for military aircraft. |
| `COLOR_RARE` | `#00C8FF` | Border and badge colour for rare aircraft. |
| `HISTORY` | `off` | `sqlite` or `csv`: log every aircraft that passes. |
| `HISTORY_DIR` | `/data` (Docker) | Where the history file goes. |
| `HISTORY_RETENTION_DAYS` | `30` | Remove entries older than this (`0` keeps all). |
| `HISTORY_TRIM_HOURS` | `1` | How often old entries are removed. |
| `MQTT_HOST` | *(empty)* | MQTT broker; setting it turns MQTT on. |
| `MQTT_PORT` | `1883` | Broker port. |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | *(empty)* | Broker login. |
| `MQTT_TLS` | `false` | Connect with TLS. |
| `MQTT_TOPIC` | `pixoo_radar` | Base topic. |
| `MQTT_DISCOVERY` / `MQTT_DISCOVERY_PREFIX` | `true` / `homeassistant` | Home Assistant discovery. |
| `TZ` | `UTC` | Timezone for log timestamps (Docker). |

More detail, examples and edge cases: [docs/configuration.md](docs/configuration.md).

## Deployment

All options in detail: [docs/deployment.md](docs/deployment.md).

### Docker

```sh
docker run -d --name pixoo-radar --restart unless-stopped -p 8765:8765 \
  -e PIXOO_HOST=192.168.1.50 -e HOME_LAT=51.4700 -e HOME_LON=-0.4543 \
  -e GIF_BASE_URL=http://192.168.1.20:8765 \
  ghcr.io/haythamelkhoja/pixoo-radar-enhanced:latest
```

### Docker Compose

[`compose.yaml`](compose.yaml) + [`.env.example`](.env.example), see Quick start.

### Docker Swarm

[`stack.yml`](stack.yml) publishes port 8765 in ingress mode, so it answers on every
node — that's where the Pixoo fetches its GIFs and where the browser view lives. No
reverse proxy, on purpose: the Pixoo can't do TLS or follow redirects.

```sh
docker stack deploy -c stack.yml pixooradar
```

### Portainer

*Stacks → Add stack → Repository*, URL `https://github.com/haythamelkhoja/pixoo-radar-enhanced`,
compose path `compose.yaml` (standalone) or `stack.yml` (Swarm). Set the variables under
*Environment variables*. Or paste either file into the *Web editor*.

### Bare metal / LXC / Raspberry Pi

[`install.sh`](install.sh) installs a venv in `/opt/pixoo-radar`, a settings file in
`/etc/pixoo-radar.env` and a `pixoo-radar` systemd service. Re-run it to upgrade.

## The Pixoo can't do HTTPS (or follow redirects)

The Pixoo64 fetches hosted GIFs over **plain http only**. It doesn't speak TLS, and it
**doesn't follow redirects**, so an `http → https` redirect (Traefik, Caddy, Nginx
Proxy Manager, Cloudflare...) makes the fetch fail.

So point `GIF_BASE_URL` at the **app's own port** (`http://<host>:8765`), not at your
reverse proxy. The proxy can still serve the browser view over HTTPS; the Pixoo just
goes around it. The app tests its own `GIF_BASE_URL` at start-up and warns you if it
redirects. See [troubleshooting](docs/troubleshooting.md#the-pixoo-cant-follow-http--https-redirects).

## Development

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
```

The tests need no network or Pixoo: they cover every data source's parsing, the
filters and highlights, rendering (all split-flap modes), the GIF encoder, history,
the web server and MQTT (against a real in-process broker), plus end-to-end runs of
the app against a fake Pixoo. They run on every push and before each release build.

## Documentation

- [Configuration](docs/configuration.md)
- [Deployment](docs/deployment.md)
- [How it works](docs/how-it-works.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Releasing](docs/releasing.md)

## Disclaimer

- By default, flight data comes from **FlightRadar24's unofficial, undocumented web endpoints**
  via the [FlightRadarAPI](https://github.com/JeanExtreme002/FlightRadarAPI) package.
  This project isn't affiliated with or endorsed by FlightRadar24. Keep it to
  **personal, non-commercial use**, respect their terms, and don't lower
  `REFRESH_SECONDS` aggressively. The endpoints can change or stop working at any time.
- The other sources have their own terms: [adsb.lol](https://adsb.lol) (free, data under
  ODbL), [OpenSky Network](https://opensky-network.org) (non-commercial use) and
  [adsbdb.com](https://www.adsbdb.com) (free API). Be gentle with them; the app caches
  lookups and rate-limits OpenSky.
- **Airline logos are fetched at runtime** (from FlightRadar24 and
  [Jxck-S/airline-logos](https://github.com/Jxck-S/airline-logos)), cached in memory
  only, and **never stored in or distributed with this repository**. They're
  trademarks of their respective owners.
- Not affiliated with Divoom.

## Credits

- Inspired by [chrivoge/PixooRadar](https://github.com/chrivoge/PixooRadar).
- [FlightRadarAPI](https://github.com/JeanExtreme002/FlightRadarAPI) by JeanExtreme002.
- Airline banners from [Jxck-S/airline-logos](https://github.com/Jxck-S/airline-logos).
- Open flight data from [adsb.lol](https://adsb.lol), [OpenSky Network](https://opensky-network.org)
  and [adsbdb.com](https://www.adsbdb.com); MQTT via [Eclipse Paho](https://eclipse.dev/paho/).

## License

[MIT](LICENSE)
