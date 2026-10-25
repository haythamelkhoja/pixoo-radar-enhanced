# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-25

### Added
- `SPLIT_FLAP=all|digits|off`: flight values on departures-board tiles that flip to each
  new value, either every character or digits only.
- Compass direction to the plane in place of the "DIST" label (`SHOW_DIRECTION`).
- Military and rare aircraft: flashing border and MIL/RARE badge, shown ahead of closer
  ordinary flights (`HIGHLIGHT_MILITARY`, `HIGHLIGHT_RARE`, `PRIORITIZE_HIGHLIGHTS`,
  `RARE_TYPES`, `RARE_REGISTRATIONS`, `MILITARY_CALLSIGNS`, `COLOR_MILITARY`, `COLOR_RARE`).
- `IGNORE_HELICOPTERS` (on by default) and `SKIP_UNKNOWN_DESTINATION`.
- Live web page: current screen, flight details and nearby traffic, updating every 5 s,
  backed by a new `/api/current` JSON endpoint.

### Changed
- Military aircraft are now shown (highlighted) by default; set `HIGHLIGHT_MILITARY=false`
  to skip them as before. Helicopters with a flight number are now skipped by default.
- `--preview` PNGs show each page once its values have settled.

## [0.1.3] - 2026-10-23

### Added
- Pushing a version tag also publishes the GitHub Release, with the matching changelog
  section as its notes. Release steps are in `docs/releasing.md`.

### Changed
- Docs and `stack.yml` no longer pin a specific version; they point to the Releases page.

## [0.1.2] - 2026-10-22

### Changed
- `stack.yml` no longer ships Traefik labels or an external `public` network. The Pixoo
  has to bypass a reverse proxy anyway (no TLS, no redirects), so the stack now just
  publishes port 8765 in ingress mode and serves both the GIFs and the browser view
  from there. Nothing external has to exist before deploying.
- Docs: the Swarm section covers putting a proxy in front of the browser view only,
  while leaving `GIF_BASE_URL` on the node's own port.

## [0.1.1] - 2026-10-22

### Fixed
- Hosted GIFs falling back to pushed frames ("Loading..") on Docker Swarm. Swarm
  doesn't add a task to the ingress routing mesh until its healthcheck passes, so the
  app's own published address refused connections during start-up and the self-check
  gave up at t=0. The self-check now waits for the address to come up
  (`SELF_CHECK_WAIT`, 120 s) and the image's healthcheck runs every 10 s instead of 30 s
  so the task is routed sooner.

### Changed
- A Pixoo that fails to fetch a hosted GIF no longer pins the app to pushed frames until
  restart: the URL is re-tested every `HOSTED_RETRY_SECONDS` (30 min) and hosted GIFs
  resume on their own once it works.

### Added
- `SELF_CHECK_WAIT` and `HOSTED_RETRY_SECONDS` settings.

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

[Unreleased]: https://github.com/haythamelkhoja/pixoo-radar-enhanced/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/haythamelkhoja/pixoo-radar-enhanced/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/haythamelkhoja/pixoo-radar-enhanced/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/haythamelkhoja/pixoo-radar-enhanced/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/haythamelkhoja/pixoo-radar-enhanced/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/haythamelkhoja/pixoo-radar-enhanced/releases/tag/v0.1.0
