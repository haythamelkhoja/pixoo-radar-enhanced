# How it works

Everything is in one file, [`src/pixoo_radar.py`](../src/pixoo_radar.py), using only
Pillow, requests and FlightRadarAPI.

```text
          every REFRESH_SECONDS
 ┌──────────────┐   flights in radius   ┌──────────────────┐
 │ FlightRadar24│ ────────────────────▶ │                  │   Device/PlayTFGif (URL)  ┌─────────┐
 └──────────────┘                       │   pixoo_radar    │ ────────────────────────▶ │ Pixoo64 │
 ┌──────────────┐   airline logo        │                  │                           │         │
 │ logo sources │ ────────────────────▶ │  render 27 frames│ ◀──── GET /<token>/N.gif ─│         │
 └──────────────┘   (cached in memory)  │  encode GIF      │                           └─────────┘
                                        │  web server :8765│ ◀──── browser: /  /api  
                                        └──────────────────┘
```

## 1. Find the closest flight

1. Ask FlightRadar24 for all flights in a box `RADAR_RADIUS_KM` around home.
2. Drop aircraft without a position, on the ground (unless `SHOW_GROUND_TRAFFIC`), and
   helicopters (unless `IGNORE_HELICOPTERS=false`).
3. Tag military and rare aircraft (see [configuration](configuration.md#military-and-rare-aircraft)).
   Untagged aircraft also need an airline flight number (and, with
   `SKIP_UNKNOWN_DESTINATION`, a known destination).
4. Pick the one with the smallest great-circle (haversine) distance from home. With
   `PRIORITIZE_HIGHLIGHTS`, the closest *tagged* aircraft wins over any untagged one.
   The bearing from home gives the compass direction.
5. Fetch its details (airline name, route, aircraft model, registration). If that call
   fails, fall back to what the live feed already had.

If nothing is airborne nearby, the last flight stays on screen until something shows up.
If FlightRadar24 errors, the app backs off (up to 5 minutes) and retries.

## 2. Draw the screen

The 64×64 screen is drawn pixel by pixel with two built-in bitmap fonts (5×7 and 3×5):

| Rows | Content |
|---|---|
| 0–19 | Airline logo, or the airline name if there's no logo |
| 21–31 | Origin → destination, with a plane moving along a dotted route |
| 33–63 | Two fields at a time: FLT/ALT, then TYPE/REG, then SPD and distance (labelled with the compass direction) |

Military and rare aircraft get a 1-pixel border and a `MIL`/`RARE` badge in the top-left
corner, on for 6 of every 9 plane steps so they flash.

With `SPLIT_FLAP`, each value sits on 7 dark tiles. When the panel turns a page, every
tile that changes shows up to three drum positions (the next character's top half over
the previous one's bottom half, split by a dark hinge line) before settling. Tiles start
one frame apart, left to right. The animation then has 54 frames at `FRAME_MS / 2`.

One animation is 27 frames: one full pass of the plane across the route, during which
the info panel steps through its three pages.

### Logos

Logos are fetched once per airline from each `LOGO_SOURCES` entry in turn, then fitted
to the 64×20 strip: a solid background (detected from the corners) is made
transparent, margins are trimmed, the logo is scaled (integer nearest-neighbour when
enlarging, Lanczos when shrinking), thin strokes are firmed up, black or grey ink is
turned light, and dark logos are brightened so they show on black.

They're cached **in memory only**: 7 days for a hit, 6 hours for a miss. Nothing is
written to disk.

## 3. Send it to the Pixoo

The Pixoo64 has a local HTTP API (`POST http://<pixoo>/post` with a JSON command).
There are two ways to get an animation onto it:

### Hosted GIF (`GIF_BASE_URL` set)

1. Encode the frames as a GIF the Pixoo plays cleanly: one global 256-colour palette,
   no transparency, later frames cropped to what changed (disposal 1), one frame delay,
   looping.
2. Publish it on the built-in web server under a fresh, unguessable name
   (`/<random token>/<counter>.gif`; the token changes on each start, and only the last
   three GIFs are kept).
3. Send `Device/PlayTFGif` with `FileType: 2` and the GIF's URL. The Pixoo downloads it
   and plays it.

The Pixoo swaps straight to the new picture: **no "Loading.." screen**.

The app double-checks each step:

- **At start-up** it fetches `GIF_BASE_URL/<token>/check` itself, the way the Pixoo
  will. A redirect, or an answer that didn't come from this app (wrong proxy route),
  means the Pixoo can't use it either, so it switches to pushing frames. If the check
  can't connect at all (e.g. hairpin NAT inside a container), it tries the Pixoo anyway.
- **After each PlayTFGif** it waits up to 15 s for the Pixoo to actually download the
  GIF. If it doesn't, the Pixoo can't reach the address (DNS, firewall). A failed fetch
  leaves the Pixoo unresponsive for about a minute, so the app waits 60 s and pushes
  frames from then until it's restarted.
- `GIF_BASE_URL`'s hostname is resolved by the app and the Pixoo gets an IP, because
  the Pixoo's DNS often can't resolve local names.

### Pushed frames (fallback)

`Draw/SendHttpGif` uploads each 64×64 frame as base64 RGB. This always works but the
Pixoo shows "Loading.." while it receives them. The animation id counter is reset every
32 uploads (`Draw/ResetHttpGifId`), because the Pixoo freezes if it grows too large.

### Not re-sending the same thing

A hash of the frames is compared with what's on screen. If the closest flight and every
displayed value are unchanged, nothing is sent: re-uploading identical content only
wears on the Pixoo.

## 4. The web server

Always running on `GIF_PORT` (default 8765), plain HTTP, no dependencies:

| Path | What |
|---|---|
| `/` | Live page: the current screen, the flight's details and nearby traffic, polling `/api/current` every 5 s |
| `/api/current` | JSON: the flight on screen, up to 15 nearby aircraft (distance, direction, tags), mode, age |
| `/latest.gif` | The current animation |
| `/<token>/<n>.gif` | Hosted GIFs for the Pixoo |
| `/<token>/check` | Start-up self-check |
| `/health` | JSON health status (200 / 503) |

All responses are `Cache-Control: no-store`. It's meant for your LAN: there's no
authentication, and the only content is the picture on your Pixoo and public flight data.
