# GPX → FIT

Turn a planned GPX route into a FIT activity file with **realistic pacing**:
slower on climbs, faster on gentle descents, slower on rough trails, with
stops where you actually stopped, and timestamps that match the photos you
took along the way.

A plain converter spreads the total time evenly over the distance, so every
kilometre takes the same time and the uploaded activity shows a flat pace
line. This one models how people actually move over terrain, then fits that
model to the times you know.

Everything runs in your browser. The conversion code is Python, executed
client-side through [Pyodide](https://pyodide.org/) (Python compiled to
WebAssembly). There is no backend, no account, and your files never leave
your device.

---

## Features

- **Gradient-aware pacing.** Speed follows the terrain, based on two
  published models: Minetti's energy-cost curve for running and Tobler's
  hiking function for walking. The app blends the two per activity,
  depending on how fast and how steep it is.
- **Anchors.** Click any point on the route and enter the time you were
  there. The pacing is fitted so every anchor is hit exactly. Out-and-back
  routes are handled: if a click lands on two passes of the route, you pick
  which one you meant.
- **Photo anchors.** Drop photos from the activity. Their GPS position and
  capture time (read from EXIF) become anchors automatically. Photos taken
  too far from the route, or outside the activity's time window, are flagged
  rather than used.
- **Stops.** Add real pauses, either as a duration ("20 minutes here") or as
  explicit arrival and departure times. They're written as timer pauses, so
  Strava counts them as stopped time instead of slow moving time.
- **Surface-aware pacing (optional).** Trail surface, path type and alpine
  difficulty (SAC scale) are fetched from OpenStreetMap data through the
  public Valhalla routing server. Gravel and scrambles are slower than
  asphalt.
- **Three ways to set the finish.** Total duration, end time, or average
  moving speed (km/h or min/km).
- **Pace smoothness.** A 1–10 slider that sets how strongly the pace varies
  over the terrain.
- **Activity profile.** After conversion, a chart shows elevation and the
  generated speed or pace over time or distance, with stops marked. Hovering
  the chart highlights the point on the map.
- **Strava-ready output.** Records, laps, session summary and timer events,
  written with a device ID Strava recognises.
- **Keyboard shortcuts** for the main actions, and light and dark themes.

Supported sports: **running** and **hiking**. Cycling needs a different
physical model and is planned.

## How it works

```
GPX file ──▶ parse route ──▶ gradient per leg (smoothed over 70 m)
                                    │
  start time + duration ──▶ anchors ┤◀── map anchors, photo anchors, stops
                                    │
   surface data (optional) ──▶ per-leg speed model (Minetti / Tobler blend,
                               × surface factor, soft-bounded speed swings)
                                    │
                  scale each anchor-to-anchor segment to its known duration
                                    │
                   timestamp every point ──▶ FIT file + activity profile
```

1. **Terrain.** Each leg's gradient is averaged over a 70 m window, so DEM
   elevation rounded to whole metres doesn't produce false spikes. The
   window never spans a hilltop or valley floor, so crests aren't flattened.
2. **Curve choice.** The activity is classified once, as a whole. The speed
   it would have on flat ground, and its mean steepness, decide how much of
   the walking curve to mix into the running curve. A slow hike on flat
   ground and a fast hike up 15% are told apart correctly.
3. **Speed per leg.** The blended curve gives each leg a speed relative to
   flat ground. That speed is multiplied by the surface factor, then pulled
   smoothly toward the typical pace, so no single leg comes out implausibly
   fast or slow.
4. **Fitting.** Between every pair of anchors, the modelled times are scaled
   so that the segment takes exactly the known time, with any stop time
   inside it removed first.
5. **Output.** Every point gets a timestamp. The FIT file gets records, timer
   pause events around each stop, and a lap/session summary with moving time
   and average speed.

The pacing constants are calibrated against real recorded activities using
the tuning harness in [`tuning/`](tuning/). The harness compares the model's
time-versus-distance curve with the athlete's actual one, and searches for
the constants that fit best.

For the code-level design, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
and, for the harness, [docs/TUNING_ARCHITECTURE.md](docs/TUNING_ARCHITECTURE.md).

## Privacy

Everything runs in the browser, so the GPX file, photos and generated FIT
file are never uploaded anywhere. The app makes three kinds of outbound
requests:

| Request | What it sends | When |
|---|---|---|
| Thunderforest map tiles | Which map area is being viewed | While the map is shown |
| Valhalla `trace_attributes` | The route's coordinates only (no times, photos or file name) | On conversion, if surface lookup is on (it can be turned off) |
| CDNs (Pyodide, Leaflet, exifr, PyPI packages) | Nothing user-specific | On page load |

The in-app About page (`about.html#privacy`) lists every request in detail.

## Running locally

Requirements: Python ≥ 3.14 with [uv](https://docs.astral.sh/uv/), Node.js
(for the JS tests only), and a free
[Thunderforest](https://www.thunderforest.com/) API key for the map tiles.

```bash
uv sync
cp src/gpx2fit/gui/js/config.example.js src/gpx2fit/gui/js/config.js
# put your Thunderforest key into config.js

python -m http.server          # from the repository root
```

Then open <http://localhost:8000/src/gpx2fit/gui/index.html>.

Serve the **repository root**, not the `gui/` folder. The browser fetches the
Python sources from `/src/gpx2fit/core/…` at startup, so opening
`index.html` directly from disk (`file://`) won't work. The first load takes
a few seconds while Pyodide and the Python packages download.

## Development

| Task | Command |
|---|---|
| Python tests | `uv run pytest` |
| Type check | `uv run pyrefly check` |
| JS tests (Node + jsdom) | `npm install`, then `npm test` |
| JS lint | `npm run lint` |
| Tune pacing constants | `uv run python -m tuning compare\|sweep\|fit\|check <corpus dir>` |

There is no build step. The frontend is plain ES modules, and the Python is
loaded into the browser as source.

The tuning harness needs a corpus of your own recorded `.fit` files (it's
gitignored, because real activities contain home locations and heart rate).

## Project layout

```
src/gpx2fit/core/     Python conversion engine (runs in the browser via Pyodide)
src/gpx2fit/core/pacing/  gradient, curve choice, surface, stops, anchors, fitting
src/gpx2fit/gui/      static web app: index.html, about.html, styles.css, js/
tuning/               dev-only calibration harness (reads real .fit files)
tests/                pytest (core/, tuning/) and Node tests (gui/)
docs/                 architecture documents
```

## Status

The full pipeline works end to end in the browser. Known limitations:

- The pacing constants are being calibrated against a growing corpus of real
  activities. The surface weight tables are still a first draft and haven't
  been tuned yet.
- Surface lookup depends on the public Valhalla demo server, which has no
  uptime guarantee. If the request fails, the conversion still completes,
  without surface-based pacing.
- Only running and hiking are supported.
- Photo anchors read EXIF from JPEG and HEIC images. Video files aren't
  supported.

## Tech stack

Python 3.14 · [gpxpy](https://github.com/tkrajina/gpxpy) ·
[fit-tool](https://pypi.org/project/fit-tool/) · Pyodide · Leaflet ·
[exifr](https://github.com/MikeKovarik/exifr) · Valhalla · vanilla
JavaScript (ES modules, no framework, no bundler) · pytest · Node's test
runner + jsdom · numpy (tuning harness only)
