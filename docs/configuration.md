# Configuration

Pixoo Radar is configured entirely with environment variables. Set them however your
deployment does it: `.env` for Docker Compose, `environment:` in `stack.yml`,
`-e` for `docker run`, *Environment variables* in Portainer, or `/etc/pixoo-radar.env`
for the systemd service installed by `install.sh`.

Values are read once at start-up; restart the app after changing them.

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
| `SHOW_DIRECTION` | `true` | Replaces the "DIST" label with the 8-point compass direction from your home to the plane (`N`, `NE`, `E`…), so the last page reads e.g. `SW  26KM`. |
| `PIXOO_BRIGHTNESS` | `-1` | `0`–`100` sets the brightness once at start-up. `-1` leaves the device's setting alone. |
| `LOGO_SOURCES` | `fr24,avcodes` | Comma-separated list of logo sources, tried in order: `fr24` (FlightRadar24) and `avcodes` ([Jxck-S/airline-logos](https://github.com/Jxck-S/airline-logos) banners). Any other value, such as `none`, disables logos and shows the airline name in text. |

In `.env` and `/etc/pixoo-radar.env`, quote colours: `COLOR_BOX="#000000"` (an
unquoted `#` can be read as a comment).

## Flights

| Variable | Default | Notes |
|---|---|---|
| `RADAR_RADIUS_KM` | `100` | Search radius around home, **whole kilometres**. Smaller radii mean closer flights but more "no flights nearby" periods, during which the last flight stays on screen. |
| `REFRESH_SECONDS` | `60` | How often to look for the closest flight. The screen is only re-sent when something visible changed. Please don't go much lower: the data source is unofficial (see the disclaimer in the README). |
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

## Hosted GIFs (no "Loading.." screen)

| Variable | Default | Notes |
|---|---|---|
| `GIF_BASE_URL` | *(empty)* | Base URL **the Pixoo** uses to download animations from this app, e.g. `http://192.168.1.20:8765`. Must be plain `http` and must not redirect. Point it at the app's own port, not a reverse proxy. A hostname is resolved by the app and handed to the Pixoo as an IP, so the Pixoo doesn't need working DNS for it. Empty means push frames instead (shows "Loading.." on each update). |
| `GIF_PORT` | `8765` | Port the built-in web server listens on. It serves the hosted GIFs, the live web page at `/`, `/api/current` and `/health`. If you change it, change the published port (`-p`, `ports:`) and `GIF_BASE_URL` to match. |
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
