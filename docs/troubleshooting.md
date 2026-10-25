# Troubleshooting

Start with the logs (`docker compose logs -f`, `docker service logs -f pixooradar_radar`
or `journalctl -u pixoo-radar -f`). The app says what it's doing and why it falls back.

A healthy hosted-GIF start-up looks like:

```text
INFO Pixoo at 192.168.1.50 | home 51.4700,-0.4543 | radius 100km | checking every 60s
INFO Serving animations on port 8765; the Pixoo fetches them from http://192.168.1.20:8765
INFO http://192.168.1.20:8765 reaches this app over plain http
INFO Showing VS137 LHR->JFK A339 1925FT▲ 5.6KM
```

**Run `--check` first** (commands for each install type are in
[deployment](deployment.md#checking-the-setup)). It tests every part of the setup and
says which one is broken:

```text
Pixoo Radar 0.4.0

✓ Settings are valid
✓ Pixoo at 192.168.1.50 answers (41 ms)
✓ adsb: 63 aircraft in the area, 21 to choose from within 100 km
- Closest: BA505 A320 6.1 km NE
✓ http://192.168.1.20:8765 reaches this app over plain http
✗ The Pixoo didn't download http://192.168.1.20:8765/... within 15 s: it can't reach this
  address (firewall or DNS on its network?). It may be unresponsive for a minute.
✓ History can be written to /data/history.sqlite

1 problem(s) found.
```

## The Pixoo shows "Loading.." on every update

That's the **pushed-frames** mode. When frames are uploaded one by one over the Pixoo's
API (`Draw/SendHttpGif`), the Pixoo shows "Loading.." while it receives them. It works,
but it flashes on every change.

The fix is **hosted GIFs**: set `GIF_BASE_URL` to an address where the Pixoo can
download from the app. The app then sends `Device/PlayTFGif` with a URL, the Pixoo
fetches the GIF itself, and the new picture swaps in with no loading screen.

If `GIF_BASE_URL` *is* set and you still see "Loading..", the app fell back to pushing.
Look for one of these in the logs:

| Log message | Meaning | Fix |
|---|---|---|
| `GIF_BASE_URL not set: pushing frames` | Not configured. | Set `GIF_BASE_URL`. |
| `... redirects to https://..., which the Pixoo can't follow` | You pointed it at a reverse proxy. | [Use the app's own port](#the-pixoo-cant-follow-http--https-redirects). |
| `... answered HTTP 404 but not from this app (proxy routing?)` | Something else answers on that address. | Check host, port and port mapping. |
| `The Pixoo didn't fetch ...` | The app is fine but the Pixoo can't reach it. | [DNS / firewall](#firewall-the-pixoos-network-must-reach-port-8765). The app re-tests the URL every `HOSTED_RETRY_SECONDS` (30 min) and returns to hosted GIFs on its own; restart to pick it up at once. |
| `... isn't answering yet ...; waiting up to 120s` | Normal on Swarm: the published port isn't routed until the healthcheck passes. | Nothing, unless it never comes up — see [Swarm](#swarm-connection-refused-on-your-own-published-port). |

## The Pixoo can't follow http → https redirects

The Pixoo64 downloads hosted GIFs over **plain HTTP only**. It has no TLS support for
this, and it **does not follow redirects** at all. Most reverse proxies (Traefik with
an `http → https` redirect, Caddy, Nginx Proxy Manager, Cloudflare Tunnel, ...)
answer plain HTTP with a `301`/`308` to HTTPS, so the Pixoo's fetch fails.

**Point `GIF_BASE_URL` at the app's own port, bypassing the proxy:**

```text
GIF_BASE_URL=http://192.168.1.20:8765          ✓ the app itself
GIF_BASE_URL=http://radar.example.com:8765     ✓ same, by name (resolved to an IP by the app)
GIF_BASE_URL=https://radar.example.com         ✗ TLS
GIF_BASE_URL=http://radar.example.com          ✗ proxy redirects to https
```

and publish that port (`-p 8765:8765`, or `ports:` in Compose/Swarm). Your proxy can
keep serving the browser view over HTTPS; only the Pixoo goes around it.
[`stack.yml`](../stack.yml) is set up exactly like that.

You can check what the Pixoo would get from any machine on its network:

```sh
curl -sI http://192.168.1.20:8765/latest.gif   # want: HTTP/1.0 200 OK, image/gif. Not 301/302/308.
```

## Swarm: "Connection refused" on your own published port

```text
WARNING Couldn't test http://192.168.1.20:8765/abc123/check from inside the container
        (... [Errno 111] Connection refused ...); trying it with the Pixoo anyway
WARNING The Pixoo didn't fetch http://192.168.1.20:8765/abc123/1.gif ...
```

Both within the first 20 seconds of start-up, on an address you know is correct.

Docker Swarm **does not add a task to the ingress routing mesh until its healthcheck
passes**. The image has a `HEALTHCHECK`, so for the first several seconds the published
port refuses connections on every node, including from inside the app's own container.
A self-check at that moment sees a dead address even though nothing is wrong.

The app waits for this: it retries the self-check for `SELF_CHECK_WAIT` seconds (120 by
default) and only then falls back. A healthy start on Swarm looks like:

```text
INFO http://192.168.1.20:8765/abc123/check isn't answering yet (...); waiting up to 120s
INFO http://192.168.1.20:8765/abc123/check came up after the wait
INFO http://192.168.1.20:8765 reaches this app over plain http
```

If it still fails after the wait, the port genuinely isn't being published. Check:

```sh
docker service ps --no-trunc <stack>_<service>    # task running, not pending or restarting
docker service inspect -f '{{json .Endpoint.Ports}}' <stack>_<service>
docker node ls -q | xargs -I{} docker node inspect -f '{{.Description.Hostname}} {{.Status.Addr}}' {}
curl -sS -o /dev/null -w '%{http_code}\n' http://<node-ip>:8765/     # want 200
```

A common cause is another service already holding ingress port 8765, which leaves the
new task unable to publish. `docker service ls` shows who has it.

## Firewall: the Pixoo's network must reach port 8765

With hosted GIFs the Pixoo opens a connection **to** the app, the opposite direction
from normal API calls. Many people keep IoT devices on a separate VLAN or guest
network that isn't allowed to talk to servers. You need both directions:

| From | To | Port | Why |
|---|---|---|---|
| App host | Pixoo | TCP 80 | API commands (always) |
| Pixoo | App host (or every Swarm node) | TCP 8765 (`WEB_PORT`) | GIF downloads (hosted mode) |

For example on OPNsense/pfSense/UniFi, add an allow rule on the IoT network:
*source* = Pixoo's IP, *destination* = the Docker host (or the Swarm nodes),
*port* = 8765/TCP. Also check the host's own firewall (`ufw allow 8765/tcp`,
firewalld, Synology/QNAP firewall).

Test from a device on the Pixoo's network (a phone on the same Wi-Fi works: open
`http://<app-host>:8765` in a browser; you should see the current screen).

Symptoms when it's blocked: the log shows `The Pixoo didn't fetch ...` about 15 s after
start, the Pixoo is unresponsive for about a minute, and then
pushed frames take over.

### DNS

The app resolves the hostname in `GIF_BASE_URL` itself and gives the Pixoo an IP,
because the Pixoo often can't resolve local names. Make sure that name resolves **from
inside the container** to an IP the Pixoo can reach (not `127.0.0.1` and not a Docker
bridge IP like `172.17.x.x`). If in doubt, use an IP in `GIF_BASE_URL`.

## A clock is drawn over the hosted GIF (open issue)

On some Pixoo64s a clock overlay appears on top of the animation when it's played as a
hosted GIF (`Device/PlayTFGif`), while pushed frames show without it. The cause isn't
known yet and there's no API setting that has been found to turn it off, so this is an
**open issue**. Reports with your firmware version and what you see are welcome.

Things to try in the meantime:

- In the Divoom app, switch the Pixoo to a channel/face without a clock before starting
  the app, and turn off any clock or time-display options.
- If the overlay bothers you more than the "Loading.." flash, leave `GIF_BASE_URL`
  empty to use pushed frames.

## The app exits listing settings

Every value is checked at start-up, and all problems are listed at once, e.g.
`COLOR_BOX='#2B2B2': use a colour like #FFAA00`. Fix each line and start again.

## "Missing required setting(s): ..."

`PIXOO_HOST`, `HOME_LAT` and `HOME_LON` have no defaults. Set them; see
[configuration](configuration.md). In Compose, make sure the `.env` file is next to
`compose.yaml`. In Portainer, see the note about `stack.env` in
[deployment](deployment.md#portainer).

## "Pixoo at ... not responding"

The app can't reach the Pixoo's API.

- Is `PIXOO_HOST` right? `curl -X POST -d '{"Command":"Channel/GetIndex"}' http://192.168.1.50/post`
  should return JSON.
- Has its IP changed? Give it a DHCP reservation.
- Is it off or offline? The app retries every 30 s on its own.

## "Flight data unavailable" / "no airborne flights nearby"

- `no airborne flights nearby`: nothing with a flight number in `RADAR_RADIUS_KM`. Normal
  at night or far from airports. Increase the radius, or set `SHOW_GROUND_TRAFFIC=true`.
  The last flight stays on screen meanwhile.
- `Flight data unavailable (...)`: FlightRadar24 didn't answer or refused. The app backs
  off and retries. If it persists for hours, the unofficial endpoints may have changed:
  check for a newer release or a newer FlightRadarAPI version. Don't lower
  `REFRESH_SECONDS` to "fix" it; that makes rate limiting more likely. A second source
  as a fallback keeps the screen going: `DATA_SOURCE=fr24,adsb`.
- `OpenSky's daily request limit is used up`: anonymous OpenSky access allows about 400
  requests a day. The app already limits itself to one every 4 minutes, but other
  programs on the same IP address count too. Create a free OpenSky account and set
  `OPENSKY_CLIENT_ID` / `OPENSKY_CLIENT_SECRET`, or use `DATA_SOURCE=opensky,adsb`.
- `... didn't return a readsb aircraft list`: `ADSB_URL` doesn't point at readsb-style
  JSON. For tar1090 it's usually `http://<pi>/tar1090/data/aircraft.json`, for
  dump1090-fa `http://<pi>:8080/data/aircraft.json`.

## Wrong or missing route with adsb or OpenSky

Those sources don't know routes, so the app looks the callsign up at adsbdb.com.
That database maps callsigns to scheduled routes: charters, diversions and brand-new
routes can be missing (`--- → ---`) or out of date. FlightRadar24 (`DATA_SOURCE=fr24`)
knows the actual route.

## MQTT: nothing in Home Assistant

- Check the log for `MQTT connected to ...`. A `refused the connection` message gives
  the broker's reason, usually wrong `MQTT_USERNAME` / `MQTT_PASSWORD`.
- Home Assistant needs the MQTT integration set up, with discovery on (the default),
  using the same prefix as `MQTT_DISCOVERY_PREFIX`.
- `mosquitto_sub -h <broker> -t 'pixoo_radar/#' -v` shows what the app publishes.
- Sensors show *unknown* while nothing is in range; that's expected.

## History: "Permission denied" or no file

In Docker the history lives in `/data`, which must be a volume or a folder the container
user (uid 10001) can write: `sudo chown 10001 ./data` for a bind mount. With `install.sh`
it's in `/var/lib/pixoo-radar`. Entries appear once an aircraft has left the area for
10 minutes, or when the app stops; the live page's "seen today" counts include visits in
progress.

## No airline logo, just the name

The airline isn't in any logo source, or the download failed. Misses are retried
after 6 hours. Set `LOGO_SOURCES=none` if you prefer names for everyone.

## The container is "unhealthy"

`/health` returns 503 when the main loop hasn't run for `3 × REFRESH_SECONDS + 600`
seconds. Check the logs for what it's stuck on and open an issue with them.
`curl http://localhost:8765/health` shows the details.

## Picking a flight doesn't stick

A flight picked on the web page goes back to automatic when it's missing from two
updates in a row: it flew out of range or landed. A restart also returns to automatic.
The log says `Back to automatic: <flight> ...` with the reason.

## "GIF_PORT has been renamed to WEB_PORT"

Settings renamed in 0.4.0 still work, but the log reminds you to update them; see
[configuration](configuration.md#renamed-settings).

## The browser view is blank

It shows the last animation the app rendered. Right after start-up, nothing has been
rendered yet; give it one refresh cycle.
