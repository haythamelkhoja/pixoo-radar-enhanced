# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-22

First public release, extracted from a single-file Docker Swarm stack.

### Added
- `src/pixoo_radar.py`: closest airborne flight on a Divoom Pixoo64 (airline logo,
  route with animated plane, split-flap panel with flight, altitude, type,
  registration, speed and distance).
- Hosted-GIF mode (`GIF_BASE_URL`): the Pixoo fetches each animation itself via
  `Device/PlayTFGif`, so there's no "Loading.." screen on updates.
- `/health` endpoint on `GIF_PORT` (200 while the main loop runs, 503 if it stalls).
- Docker image (`python:3.13-slim`, non-root, `HEALTHCHECK`), multi-arch
  (amd64 + arm64) on ghcr.io, built by GitHub Actions on version tags.
- Deployment options: Docker Compose (`compose.yaml` + `.env.example`), Docker Swarm
  (`stack.yml`, with Traefik labels), Portainer, and `install.sh` for systemd hosts
  (Debian/Ubuntu, Raspberry Pi OS, Proxmox LXC).
- Docs: configuration, deployment, how it works, troubleshooting.

### Changed
- `PIXOO_HOST`, `HOME_LAT` and `HOME_LON` no longer have built-in defaults; the app
  exits with a clear message if they're missing.

### Fixed
- Climb/descent arrows (▲/▼) after the altitude, which had been lost from the font.

[Unreleased]: https://github.com/haythamelkhoja/pixoo-radar-enhanced/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/haythamelkhoja/pixoo-radar-enhanced/releases/tag/v0.1.0
