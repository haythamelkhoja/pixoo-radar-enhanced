# Deployment

Pick one. Run **only one instance per Pixoo**: two would fight over the screen.

| Method | Good for |
|---|---|
| [Docker Compose](#docker-compose) | Most people: any machine with Docker (NAS, mini PC, Pi). |
| [docker run](#docker-run) | One-liners and testing. |
| [Docker Swarm](#docker-swarm) | Existing Swarm clusters. |
| [Portainer](#portainer) | If you manage containers through Portainer's UI. |
| [install.sh (systemd)](#installsh-systemd-bare-metal-raspberry-pi) | No Docker: Debian/Ubuntu, Raspberry Pi OS. |
| [Proxmox LXC](#proxmox-lxc) | A tiny dedicated container on Proxmox. |
| [Plain Python](#plain-python) | Development. |

The image is `ghcr.io/haythamelkhoja/pixoo-radar-enhanced`, for `linux/amd64` and `linux/arm64`.
Tags: `latest`, each exact version (e.g. `1.4.2`), its minor series (`1.4`) and its major
series (`1`). Versions are listed under [Releases](https://github.com/haythamelkhoja/pixoo-radar-enhanced/releases).

Whatever you choose, the Pixoo's network must be able to reach the app on TCP 8765
(for hosted GIFs) and the app must be able to reach the Pixoo on TCP 80. See
[troubleshooting](troubleshooting.md#firewall-the-pixoos-network-must-reach-port-8765).

## Docker Compose

```sh
mkdir pixoo-radar && cd pixoo-radar
curl -fsSLO https://raw.githubusercontent.com/haythamelkhoja/pixoo-radar-enhanced/main/compose.yaml
curl -fsSL  https://raw.githubusercontent.com/haythamelkhoja/pixoo-radar-enhanced/main/.env.example -o .env
nano .env
docker compose up -d
```

- Logs: `docker compose logs -f`
- Update: `docker compose pull && docker compose up -d`
- Health: `docker compose ps` shows `healthy` once the loop is running.

## docker run

```sh
docker run -d --name pixoo-radar --restart unless-stopped -p 8765:8765 \
  -e PIXOO_HOST=192.168.1.50 \
  -e HOME_LAT=51.4700 -e HOME_LON=-0.4543 \
  -e GIF_BASE_URL=http://192.168.1.20:8765 \
  -e TZ=Europe/London \
  ghcr.io/haythamelkhoja/pixoo-radar-enhanced:latest
```

`--network host` also works (then no `-p` is needed).

## Docker Swarm

[`stack.yml`](../stack.yml) is a complete example:

- Port **8765 is published in ingress mode**, so it answers on every node. The Pixoo
  fetches GIFs from there, and so does the browser view.
- **No reverse proxy.** The Pixoo can't do TLS and won't follow redirects, so putting
  a proxy in the path breaks it (see [why](troubleshooting.md#the-pixoo-cant-follow-http--https-redirects)).
  Keeping the stack proxy-free means one less thing to get wrong.
- `replicas: 1` with `update_config.order: stop-first`, so there's never more than one
  instance driving the Pixoo.
- No external network is required; the stack's default overlay is enough. Add a
  `networks:` block only if you need it to join an existing one.

Swarm doesn't route a published port to a task until the task's healthcheck passes, so
the app's own address refuses connections for the first few seconds. The app waits that
out (`SELF_CHECK_WAIT`); see [troubleshooting](troubleshooting.md#swarm-connection-refused-on-your-own-published-port).

Edit the environment values and hostnames, then:

```sh
docker stack deploy -c stack.yml pixooradar
docker service logs -f pixooradar_radar
```

Update to a new release: `docker service update --image ghcr.io/haythamelkhoja/pixoo-radar-enhanced:<version> pixooradar_radar`,
with `<version>` from [Releases](https://github.com/haythamelkhoja/pixoo-radar-enhanced/releases)
(or deploy the stack again with `latest` and `--resolve-image always`).

`GIF_BASE_URL` can be any name resolving to a swarm node, with `http://` and `:8765`.
The app resolves it and hands the Pixoo the IP.

### Serving the browser view over HTTPS

If you want the browser view on a hostname with TLS, add your proxy's labels back and
point them at port 8765 — but leave `GIF_BASE_URL` on the node's own `:8765`. The proxy
then serves people; the Pixoo keeps going straight to the port. Pointing `GIF_BASE_URL`
at the proxy is the single most common way to end up back on "Loading..".

## Portainer

**From the repository** (gets updates with *Pull and redeploy*):

1. *Stacks → Add stack*, give it a name.
2. *Build method: Repository*. URL `https://github.com/haythamelkhoja/pixoo-radar-enhanced`,
   reference `refs/heads/main`.
3. Compose path: `compose.yaml` on a standalone Docker environment, `stack.yml` on Swarm.
4. *Environment variables*: add `PIXOO_HOST`, `HOME_LAT`, `HOME_LON`, `GIF_BASE_URL`
   (and any others from [configuration](configuration.md)).

   With `compose.yaml`, Portainer writes these into a file named `stack.env`, not
   `.env`, so either use the *Web editor* method below or change `env_file: .env` to
   `env_file: stack.env`. `stack.yml` sets its values inline; edit them in a copy.
5. *Deploy the stack*.

**Web editor**: paste this and edit the values:

```yaml
services:
  radar:
    image: ghcr.io/haythamelkhoja/pixoo-radar-enhanced:latest
    environment:
      PIXOO_HOST: 192.168.1.50
      HOME_LAT: "51.4700"
      HOME_LON: "-0.4543"
      GIF_BASE_URL: http://192.168.1.20:8765
      TZ: Europe/London
    ports:
      - "8765:8765"
    restart: unless-stopped
```

## install.sh (systemd: bare metal, Raspberry Pi)

For Debian, Ubuntu, Raspberry Pi OS and other systemd distributions with Python 3.10+.

```sh
curl -fsSL https://raw.githubusercontent.com/haythamelkhoja/pixoo-radar-enhanced/main/install.sh | sudo bash
```

(Read the script first if you like: it's short. Or clone the repo and run
`sudo ./install.sh` to install the files from the clone.)

It:

- installs `python3`, `python3-venv` and `curl` with apt (if apt is available),
- downloads the latest release into `/opt/pixoo-radar` with its own virtualenv,
- creates a `pixoo-radar` system user,
- on first install asks for the Pixoo address and your coordinates, and writes
  `/etc/pixoo-radar.env` (later runs keep it),
- installs and starts the `pixoo-radar` systemd service.

| Task | Command |
|---|---|
| Logs | `journalctl -u pixoo-radar -f` |
| Change settings | `sudo nano /etc/pixoo-radar.env && sudo systemctl restart pixoo-radar` |
| Upgrade | run the installer again |
| Specific version | `curl ... \| sudo PIXOO_RADAR_VERSION=vX.Y.Z bash` |
| Uninstall | `sudo ./install.sh --uninstall` (or pipe it with `bash -s -- --uninstall`) |

## Proxmox LXC

The app needs very little (one CPU core, 256 MB RAM, 2 GB disk), so a small
unprivileged LXC container is plenty.

1. In Proxmox: *Create CT* from a **Debian 12 or 13** template, unprivileged,
   1 core, 256–512 MB RAM, 2 GB disk, network on a bridge the Pixoo can reach
   (DHCP or a static IP; give it a fixed address either way).
2. Start it and open its console (or `pct enter <id>` on the host).
3. Inside the container:

   ```sh
   apt update && apt install -y curl
   curl -fsSL https://raw.githubusercontent.com/haythamelkhoja/pixoo-radar-enhanced/main/install.sh | bash
   ```

   (You're root in the container, so no `sudo`.)
4. When asked for the address the Pixoo can reach, accept the suggested
   `http://<container-ip>:8765`.

Prefer Docker? Enable *Options → Features → nesting* on the container, install Docker
inside it and use [Docker Compose](#docker-compose) instead.

## Plain Python

```sh
git clone https://github.com/haythamelkhoja/pixoo-radar-enhanced && cd pixoo-radar-enhanced
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
PIXOO_HOST=192.168.1.50 HOME_LAT=51.4700 HOME_LON=-0.4543 \
  GIF_BASE_URL=http://192.168.1.20:8765 python src/pixoo_radar.py
```

## Checking the setup

`--check` tests each part and prints a ✓/✗ list: settings, the Pixoo's API, each data
source (and adsbdb), `GIF_BASE_URL` (including a real download by the Pixoo, which shows
a *CHECK OK* screen for a moment), the history folder and the MQTT login. It exits with 1
if anything failed.

| Install | Command |
|---|---|
| Compose | `docker compose exec radar python /app/pixoo_radar.py --check` |
| docker run | `docker exec pixoo-radar python /app/pixoo_radar.py --check` |
| Swarm | `docker exec $(docker ps -qf name=pixooradar_radar) python /app/pixoo_radar.py --check` (on the node running it) |
| install.sh | `sudo systemd-run --pty --quiet -p EnvironmentFile=/etc/pixoo-radar.env -p User=pixoo-radar /opt/pixoo-radar/venv/bin/python /opt/pixoo-radar/pixoo_radar.py --check` |

Run inside a running app, the port is already taken, so the Pixoo-download test is
skipped (the running app's log reports that instead). `--version` prints the version;
it's also in the log, on the web page, in `/health` and on the Home Assistant device.

## Health check

`GET /health` on `WEB_PORT`:

```json
{"status": "ok", "last_loop_seconds_ago": 12, "showing": true}
```

It returns **200** while the main loop keeps running and **503** (`"status": "stalled"`)
if the loop hasn't checked in for `3 × REFRESH_SECONDS + 600` seconds. The margin is
there because the app deliberately backs off for several minutes when FlightRadar24 or
the Pixoo is unavailable; that's not a failure of the app.

The Docker image has a matching `HEALTHCHECK`. Swarm replaces unhealthy tasks
automatically; plain Docker only marks them `unhealthy`. Uptime Kuma and similar can
poll `/health` too.
