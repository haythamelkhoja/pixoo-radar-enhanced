#!/usr/bin/env bash
# Pixoo Radar installer for Debian, Ubuntu, Raspberry Pi OS and Proxmox LXC containers.
#
# Installs the app into /opt/pixoo-radar (own Python venv), settings in /etc/pixoo-radar.env,
# and runs it as the "pixoo-radar" systemd service. Running it again upgrades in place and
# keeps your settings.
#
#   curl -fsSL https://raw.githubusercontent.com/haythamelkhoja/pixoo-radar-enhanced/main/install.sh | sudo bash
#   sudo ./install.sh                 # from a clone: installs the files next to this script
#   sudo ./install.sh --uninstall     # removes the service and app (keeps /etc/pixoo-radar.env)
#
# PIXOO_RADAR_VERSION=vX.Y.Z picks a release (default: the latest one).

set -euo pipefail

REPO="haythamelkhoja/pixoo-radar-enhanced"
APP_DIR=/opt/pixoo-radar
ENV_FILE=/etc/pixoo-radar.env
SERVICE=pixoo-radar
USER_NAME=pixoo-radar

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root (sudo $0)"
command -v systemctl >/dev/null || die "systemd is required"

if [ "${1:-}" = "--uninstall" ]; then
    systemctl disable --now "$SERVICE" 2>/dev/null || true
    rm -f "/etc/systemd/system/$SERVICE.service"
    systemctl daemon-reload
    rm -rf "$APP_DIR"
    userdel "$USER_NAME" 2>/dev/null || true
    say "Removed. Your settings are still in $ENV_FILE"
    exit 0
fi

# --- Dependencies ---------------------------------------------------------------
if command -v apt-get >/dev/null; then
    say "Installing python3, venv and curl"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv curl ca-certificates >/dev/null
fi
command -v python3 >/dev/null || die "python3 (3.10 or newer) is required"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "python3 is too old; 3.10 or newer is required"

# --- App files ------------------------------------------------------------------
src_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
mkdir -p "$APP_DIR"
if [ -n "$src_dir" ] && [ -f "$src_dir/src/pixoo_radar.py" ]; then
    say "Installing from $src_dir"
    install -m 644 "$src_dir/src/pixoo_radar.py" "$src_dir/requirements.txt" "$APP_DIR/"
    git -C "$src_dir" describe --tags --always --dirty 2>/dev/null >"$APP_DIR/VERSION" || echo local >"$APP_DIR/VERSION"
else
    version="${PIXOO_RADAR_VERSION:-}"
    if [ -z "$version" ]; then
        latest="$(curl -fsSIo /dev/null -w '%{redirect_url}' "https://github.com/$REPO/releases/latest" || true)"
        version="${latest##*/}"
        case "$version" in v*) ;; *) version=main ;; esac
    fi
    say "Downloading $REPO $version"
    for file in src/pixoo_radar.py requirements.txt; do
        curl -fsSL "https://raw.githubusercontent.com/$REPO/$version/$file" -o "$APP_DIR/${file##*/}" \
            || die "couldn't download $file ($version)"
    done
    echo "$version" >"$APP_DIR/VERSION"
fi

say "Installing Python packages"
[ -x "$APP_DIR/venv/bin/python" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --disable-pip-version-check --upgrade -r "$APP_DIR/requirements.txt"

id "$USER_NAME" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$USER_NAME"

# --- Settings -------------------------------------------------------------------
my_ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
if [ ! -f "$ENV_FILE" ]; then
    pixoo_host="" home_lat="" home_lon=""
    gif_base_url="${my_ip:+http://$my_ip:8765}"
    if [ -r /dev/tty ]; then
        say "First install: a few questions (edit $ENV_FILE later to change anything)"
        read -rp "  Pixoo64 IP or hostname: " pixoo_host </dev/tty
        read -rp "  Your latitude  (e.g. 51.4700): " home_lat </dev/tty
        read -rp "  Your longitude (e.g. -0.4543): " home_lon </dev/tty
        read -rp "  Address the Pixoo can reach this machine on [$gif_base_url]: " answer </dev/tty
        gif_base_url="${answer:-$gif_base_url}"
    fi
    cat >"$ENV_FILE" <<EOF
# Pixoo Radar settings. Full reference: docs/configuration.md
# Apply changes with: sudo systemctl restart $SERVICE
PIXOO_HOST=${pixoo_host}
HOME_LAT=${home_lat}
HOME_LON=${home_lon}
GIF_BASE_URL=${gif_base_url}
#RADAR_RADIUS_KM=100
#REFRESH_SECONDS=60
#SHOW_GROUND_TRAFFIC=false
#IGNORE_HELICOPTERS=true
#SKIP_UNKNOWN_DESTINATION=false
#HIGHLIGHT_MILITARY=true
#HIGHLIGHT_RARE=true
#PRIORITIZE_HIGHLIGHTS=true
#RARE_REGISTRATIONS=
#FRAME_MS=300
#SHOW_BEARING=true
#SPLIT_FLAP=off
#PIXOO_BRIGHTNESS=-1
#LOGO_SOURCES=fr24,avcodes
#COLOR_TEXT="#FFFF00"
#COLOR_BOX="#2B2B2B"
#WEB_PORT=8765
#SHOW_HEADING=true
# Data: fr24, adsb (adsb.lol or your receiver's .../data/aircraft.json), opensky; several = fallbacks
#DATA_SOURCE=fr24
#ADSB_URL=https://api.adsb.lol/v2/point/{lat}/{lon}/{radius_nm}
#LOOKUP_ADSBDB=true
#OPENSKY_CLIENT_ID=
#OPENSKY_CLIENT_SECRET=
# History of every aircraft seen: off, sqlite or csv
#HISTORY=off
#HISTORY_RETENTION_DAYS=30
#HISTORY_TRIM_HOURS=1
# MQTT / Home Assistant (off unless MQTT_HOST is set)
#MQTT_HOST=
#MQTT_PORT=1883
#MQTT_USERNAME=
#MQTT_PASSWORD=
#MQTT_TLS=false
#MQTT_TOPIC=pixoo_radar
#MQTT_DISCOVERY=true
#MQTT_DISCOVERY_PREFIX=homeassistant
EOF
    chmod 600 "$ENV_FILE"   # may hold MQTT/OpenSky passwords; systemd reads it as root
    say "Wrote $ENV_FILE"
fi

# --- Service --------------------------------------------------------------------
cat >"/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Pixoo Radar (closest flight on a Divoom Pixoo64)
Wants=network-online.target
After=network-online.target

[Service]
User=$USER_NAME
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
Environment=HISTORY_DIR=/var/lib/pixoo-radar
StateDirectory=pixoo-radar
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/pixoo_radar.py
Restart=always
RestartSec=30
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1

if grep -Eq '^(PIXOO_HOST|HOME_LAT|HOME_LON)=$' "$ENV_FILE"; then
    say "Set PIXOO_HOST, HOME_LAT and HOME_LON in $ENV_FILE, then: sudo systemctl restart $SERVICE"
else
    systemctl restart "$SERVICE"
    say "Running. Logs: journalctl -u $SERVICE -f"
    if [ -n "$my_ip" ]; then say "Current screen: http://$my_ip:8765"; fi
fi
say "Test the setup any time: sudo systemd-run --pty --quiet -p EnvironmentFile=$ENV_FILE -p User=$USER_NAME $APP_DIR/venv/bin/python $APP_DIR/pixoo_radar.py --check"
