# Architecture

How the codebase is put together: which file owns what, how the files call
each other, and how a GPX file becomes a FIT file. This assumes you know what
the app does (see [README.md](../README.md)). The calibration harness in
`tuning/` has its own document,
[TUNING_ARCHITECTURE.md](TUNING_ARCHITECTURE.md). Diagrams are Mermaid,
which GitHub renders. In PyCharm, enable Mermaid in *Settings → Languages &
Frameworks → Markdown*.

Contents

1. [Design rules](#1-design-rules)
2. [Repository map](#2-repository-map)
3. [System overview](#3-system-overview)
4. [The data contract: `models.py`](#4-the-data-contract-modelspy)
5. [`core/` module by module](#5-core-module-by-module)
6. [The bridge: `pyodideBridge.js`](#6-the-bridge-pyodidebridgejs)
7. [The GUI](#7-the-gui)
8. [End-to-end code flow](#8-end-to-end-code-flow)
9. [Errors](#9-errors)
10. [The tuning harness](#10-the-tuning-harness)
11. [Tests](#11-tests)
12. [Change checklists](#12-change-checklists)

---

## 1. Design rules

These rules shape the whole codebase. Breaking one of them means
redesigning, not refactoring.

| Rule                                                                                                                                                               | Why                                                                                                                                                                                                  |
|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **`core/` does no I/O.** Every function takes and returns bytes or dataclasses, never a file path, a network call, or `print()`.                                   | `core/` runs inside Pyodide in the browser. The network calls it needs (Valhalla) are made by JS, and the results are passed in as data.                                                             |
| **`core/` doesn't know about its interface.** No HTML, no CLI parsing.                                                                                             | The GUI is the only interface today, but a CLI could be added on top of `core/` unchanged.                                                                                                           |
| **One data contract.** Every module reads and writes the types in `core/models.py`.                                                                                | No module invents its own point or track shape. Stops and photos get their own input types, but they resolve down to `Anchor`/`TrackPoint` before pacing sees them.                                  |
| **Start and end are anchors.** A known time at distance 0, at the end, at a summit, from a photo, or at a stop's arrival/departure all use the same `Anchor` type. | `combine()` has one code path: fit between consecutive anchors. The average-speed entry is also just another way to set the end anchor's time. It's converted in JS, and `core/` never sees a speed. |
| **Two-stage inputs.** `RawAnchor → Anchor`, `RawStop → ResolvedStop \| ModeAStop`, `RawPhotoAnchor → ResolvedPhotoAnchor`.                                         | "Raw" is what the frontend can know (a click position, a distance). "Resolved" is pinned to a track point. Don't merge the two stages.                                                               |
| **Derived values are computed, not cached.** `Track.total_distance`, `total_elevation_gain` and `start_time` are properties.                                       | Pacing mutates `points` in place. A cached total could silently go stale, and recomputing costs almost nothing at this data size.                                                                    |
| **`SportType` is a closed enum and a dispatch key.**                                                                                                               | `curve_selection`, `surface` and `fit_writer` branch on it. Adding cycling needs a new pacing model, not just a new enum value.                                                                      |
| **`InputError` is a user-facing contract.**                                                                                                                        | Raise it only for problems the user can fix, with a plain-language message. See [§9](#9-errors).                                                                                                     |
| **Pacing is resolved per workout, fitted per segment.**                                                                                                            | Which curve to use, how much speed may swing, and the curve exponents are decided once from the whole track. Anchors only mark where a time is known, not where the terrain changes.                 |

---

## 2. Repository map

```
src/gpx2fit/
├── core/                         pure Python, loaded into Pyodide
│   ├── models.py                 data contract (all dataclasses + enums + InputError)
│   ├── gpx_reader.py             GPX bytes → Track
│   ├── fit_writer.py             paced Track → FIT bytes
│   ├── activity_profile.py       paced Track → ~1000 ProfileSamples for the chart
│   └── pacing/
│       ├── anchors.py            nearest-point lookup, boundary + user anchors
│       ├── stops.py              stop resolution (Mode A/B), track expansion
│       ├── photo_anchors.py      EXIF readings → matched track points + status
│       ├── gradient.py           smoothed gradients, Minetti/Tobler speed curves
│       ├── curve_selection.py    per-workout: Tobler weight, speed-swing bound, curve shape
│       ├── surface.py            Valhalla response → per-leg surface multipliers
│       ├── surface_weights.json  per-sport weight tables for surface.py
│       └── combine.py            fits speeds to anchors, stamps timestamps
└── gui/
    ├── index.html                app shell; loads Leaflet, Pyodide, exifr from CDNs
    ├── about.html                static About/privacy page
    ├── styles.css                all styles, light + dark tokens
    └── js/                       plain ES modules, no build (see §7)

tuning/                           dev-only calibration harness (see TUNING_ARCHITECTURE.md)
tests/core/  tests/tuning/        pytest
tests/gui/                        node --test + jsdom
docs/                             this file and TUNING_ARCHITECTURE.md
valhalla_spike.html               standalone spike that proved Valhalla is CORS-open
```

---

## 3. System overview

Three runtime layers live in one browser tab. The tuning harness is a
separate offline tool that imports `core/` directly.

```mermaid
flowchart TB
  subgraph Browser["Browser tab"]
    direction TB
    subgraph GUI["GUI (gui/js/*.js)"]
      main["main.js<br/>wiring + app state"]
      ui["map / anchors / stops / photos /<br/>time inputs / profile panel / shortcuts"]
    end
    bridge["pyodideBridge.js<br/>parseGpx · resolveAnchorCandidates ·<br/>resolvePhotoAnchors · convert"]
    subgraph Py["Pyodide runtime (WASM)"]
      track[("_track global<br/>parsed once per upload")]
      core["gpx2fit.core<br/>models · gpx_reader · pacing/* ·<br/>fit_writer · activity_profile"]
    end
    main --> ui
    main --> bridge
    ui --> bridge
    bridge -- "globals.set + runPythonAsync" --> core
    core --- track
  end

  CDN["jsdelivr / unpkg / PyPI<br/>Pyodide, Leaflet, exifr,<br/>gpxpy, fit-tool"] -. "page load" .-> Browser
  TF["Thunderforest tiles"] -. "map.js" .-> ui
  VH["Valhalla trace_attributes<br/>(optional)"] <-. "convert(): route shape → edges" .-> bridge
  Static["static file server<br/>(repo root)"] -. "fetch ../../core/*.py" .-> bridge

  subgraph Offline["Developer machine only"]
    tuning["tuning/ harness<br/>(TUNING_ARCHITECTURE.md)"] --> core2["gpx2fit.core<br/>(imported directly)"]
    corpus[("corpus/*.fit")] --> tuning
  end
```

Ownership in short:

- **`main.js`** owns app state: route points, total distance, chosen sport,
  start-time result, and the GPX file name.
- **`pyodideBridge.js`** owns the Python runtime and is the only file that
  talks to Python.
- **Python** owns every calculation, including distances, nearest-point
  lookups, pacing and encoding. JS never computes a route distance itself.

---

## 4. The data contract: `models.py`

```mermaid
classDiagram
  direction LR
  class Track {
    points: list~TrackPoint~
    sport: SportType?
    device: str?
    device_manufacturer: int?
    device_product: int?
    device_serial: int?
    activity_name: str?
    start_time() datetime?
    total_distance() float
    total_elevation_gain() float
  }
  class TrackPoint {
    lat, lon, elevation: float
    distance_from_start: float
    timestamp: datetime?
  }
  class SportType {
    <<enum>>
    RUNNING
    HIKING
  }
  class Anchor {
    distance_from_start: float
    timestamp: datetime
    source: user|photo|stop_arrival|stop_departure
  }
  class RawAnchor {
    timestamp: datetime
    distance_from_start: float?
    lat, lon: float?
    source: str
  }
  class RawStop {
    distance_from_start: float?
    lat, lon: float?
    duration: timedelta?
    start_timestamp, end_timestamp: datetime?
  }
  class ResolvedStop {
    distance_from_start: float
    arrival: datetime
    departure: datetime
  }
  class ModeAStop {
    distance_from_start: float
    duration: timedelta
  }
  class RawPhotoAnchor {
    lat, lon: float
    timestamp: datetime
  }
  class ResolvedPhotoAnchor {
    distance_from_start, lat, lon: float
    timestamp: datetime
    status: ok|outside_activity_time|too_far|at_route_end
    gap_m: float
  }
  class ProfileSample {
    distance_from_start, elapsed_seconds: float
    speed_mps: float
    elevation: float?
    lat, lon: float
    is_stop: bool
  }

  Track "1" o-- "*" TrackPoint
  Track --> SportType
  RawAnchor ..> Anchor : anchors.build_user_anchors
  RawStop ..> ResolvedStop : resolve_stops, Mode B
  RawStop ..> ModeAStop : resolve_stops, Mode A
  ResolvedStop ..> Anchor : two anchors, built in the bridge
  RawPhotoAnchor ..> ResolvedPhotoAnchor : resolve_photo_anchors
  ResolvedPhotoAnchor ..> RawAnchor : GUI, only if status is ok
  Track ..> ProfileSample : build_activity_profile
```

Invariants that the rest of the code relies on:

- `TrackPoint.distance_from_start` is the cumulative **3D** distance
  (gpxpy's `distance_3d`, falling back to 2D when there's no elevation). It
  never decreases. Gradients are therefore rise over slope distance.
- A missing `<ele>` inherits the previous point's elevation, and the first
  point defaults to `0.0`. `fit_writer` treats `0.0` as "no altitude".
- `timestamp` is `None` from parsing until `combine()` sets it.
- A **stop** is a pair of points at the same distance: the original point
  and a duplicate inserted right after it. The zero-distance leg between
  them is the stop's duration. `combine`, `fit_writer` and
  `activity_profile` all detect a stop with the same test: *a zero-distance
  leg that takes time*.
- Mode A stop: the user gave only a duration, and `combine()` derives the
  arrival from the pacing model. Mode B stop: the user gave explicit arrival
  and departure times, which become two `Anchor`s.

---

## 5. `core/` module by module

### 5.1 Import graph inside `core/`

```mermaid
flowchart LR
  models["models.py"]
  gpx["gpx_reader.py"] --> models
  fitw["fit_writer.py"] --> models
  prof["activity_profile.py"] --> models
  anchors["pacing/anchors.py"] --> models
  stops["pacing/stops.py"] --> anchors
  photo["pacing/photo_anchors.py"] --> anchors
  gradient["pacing/gradient.py"] --> models
  cs["pacing/curve_selection.py"] --> gradient
  surface["pacing/surface.py"] --> models
  surface -. "reads at import" .-> sw[("surface_weights.json")]
  combine["pacing/combine.py"] --> cs
  combine --> gradient
  gpx -.-> gpxpy(["gpxpy"])
  fitw -.-> fittool(["fit-tool"])
```

`models.py` also defines `InputError`, a subclass of `ValueError` (see
[§9](#9-errors)).

`combine.py` is the only module that brings the pacing pieces together.
`surface.py` is not imported by `combine`. The bridge calls it and passes
the resulting multipliers to `combine()` as a plain list. `anchors.py`,
`stops.py` and `photo_anchors.py` never call pacing code.

### 5.2 `gpx_reader.py`

```
parse_gpx_bytes(gpx_bytes, device=None) -> Track
  bytes ─decode utf-8─▶ gpxpy.parse ─▶ for track/segment/point:
      cumulative distance_3d (→ distance_2d) , elevation carry-forward
  ─▶ Track(points, device = device or <gpx creator>)
  raises InputError: not UTF-8 · not GPX · no track points
```

It reads only `<trk>` points. Route (`<rte>`) and waypoint elements are
ignored.

### 5.3 `pacing/anchors.py`: finding points and building anchors

```mermaid
flowchart TD
  dm["distance_meters(lat,lon,lat,lon)<br/>haversine"]
  np1["nearest_point_distance_from_start(track, lat, lon)<br/>→ float"] --> dm
  npc["nearest_point_candidates(track, lat, lon,<br/>radius 25 m, gap 50 m, max 4)<br/>→ [{distance_from_start, lat, lon}]"] --> dm
  ase["add_start_end_anchors(track, start, end|duration)<br/>→ [Anchor@0 'start', Anchor@total 'end']"]
  bua["build_user_anchors(track, raw_anchors, existing)<br/>→ sorted [Anchor]"] --> np1
  bua -.-> err["InputError: two anchors on one point"]
  bua --> recm["route_end_collision_message(what, source)<br/>names the start/finish, else None"]
```

`nearest_point_candidates` is how out-and-back routes are handled. It finds
the closest point, collects every point within 25 m of it, and splits them
into clusters wherever the distance along the route jumps by more than
50 m. Each cluster is one pass of the route, and one representative per
pass is returned. A normal route always returns exactly one candidate.

### 5.4 `pacing/stops.py`

```mermaid
flowchart TD
  rs["resolve_stops(track, raw_stops, hard_anchors)<br/>→ (mode_b: [ResolvedStop], mode_a: [ModeAStop])"]
  rd["_resolve_distance(track, raw)"] --> np["anchors.nearest_point_distance_from_start"]
  rs --> rd
  rs -.-> err["InputError: stop on an anchor/stop ·<br/>departure ≤ arrival"]
  ets["expand_track_with_stops(points, stops)<br/>→ new list, +1 duplicate point per stop"]
  ems["expand_multipliers_with_stops(points, mults, stops)<br/>→ new list, +1 duplicated multiplier per stop"]
```

Both `expand_*` functions return new lists. `_track.points` is never
changed structurally, because the bridge reuses `_track` for every
conversion. A stop's distance must equal an existing point's distance
exactly. That's guaranteed because the GUI always sends a distance that
Python itself returned.

### 5.5 `pacing/photo_anchors.py`

```
resolve_photo_anchors(track, raw_photo_anchors, max_match 150 m,
                      activity_start?, activity_end?)
  for each photo:
     nearest_point_candidates → pick the candidate closest to the photo's GPS
     status = "outside_activity_time"  unless start < capture time < end, no padding  (checked first)
            | "too_far"                if gap > 150 m
            | "at_route_end"           if the match is the first or last point (the start/end anchors sit there)
            | "ok"
  → [ResolvedPhotoAnchor]   (never raises for a bad match; the caller decides)
```

### 5.6 `pacing/gradient.py`: terrain to relative speed

```mermaid
flowchart TD
  cg["calculate_gradient(track, window_m = 70)<br/>→ one gradient per leg"]
  tp["_turning_points(elevations, min_rise 5 m)<br/>hysteresis crest/valley finder"]
  ea["_elevation_at(distance)<br/>interpolated"]
  cg --> tp
  cg --> ea

  ms["minetti_speeds_from_gradients(g, up_exp, down_exp, downhill_cost_slope = 20)"]
  rm["_raw_minetti_relative_speed(g, slope)"] --> mc["_minetti_cost(g, slope)<br/>Minetti 2002 polynomial<br/>+ slope·|g| on descents"]
  ms --> rm
  ts["tobler_speeds_from_gradients(g, up_exp, down_exp)"] --> tsh["_tobler_shape(g)<br/>exp(-3.5·|g + 0.05|)"]
  ms --> so["_soften(raw, g, up, down)<br/>speed ** exponent"]
  ts --> so

  bl["blended_speeds_from_gradients(g, tobler_weight, CurveShape)<br/>minetti^(1-w) · tobler^w"]
  bl --> ms
  bl --> ts
```

- **Window.** Each leg's gradient is rise over run across a 70 m window
  centred on the leg. Near a detected crest or valley, the window shrinks
  symmetrically so that it ends at the turning point, but not below 30 m.
  Past that limit it slides away from the turning point instead. At the
  track's ends it slides inward at full length.
- **Curves are relative.** Every curve returns 1.0 on flat ground, so every
  blend of them does too. Absolute speed comes later, when `combine` scales
  each segment to its duration.
- **Softening.** `speed ** exponent` scales log-speed linearly. That's why
  `curve_selection` can fit the exponents directly to a log-space bound.
- **Descent cost slope.** `MINETTI_DOWNHILL_COST_SLOPE` sets where Minetti's
  descents turn slower than flat. The app always uses the default; the
  `downhill_cost_slope` argument exists so the tuning harness can fit it.

### 5.7 `pacing/curve_selection.py`: three per-workout decisions

```mermaid
flowchart LR
  g[/"gradients, leg_distances"/]
  a[/"active_seconds (elapsed − stops)"/]
  s[/"sport"/]
  sm[/"smoothness 1–10"/]

  rtw["resolve_tobler_weight<br/>→ 0..1"]
  rmr["resolve_max_speed_ratio<br/>→ ratio > 1"]
  rcs["resolve_curve_shape(ratio, sport)<br/>→ CurveShape"]

  g --> rtw
  a --> rtw
  s --> rtw
  g --> rmr
  s --> rmr
  sm --> rmr
  rmr --> rcs
  s --> rcs

  rtw --> v["_verticality · _smoothstep"]
  rtw --> probe["minetti_speeds_from_gradients<br/>(default exponents, probe)"]
  rmr --> v
  rcs --> raw["_reference_swings<br/>both curves' largest |log speed| within ±25 %, exponent 1.0"]
  rcs --> fe["_fitted_exponents<br/>CURVE_FILLS[sport]: running 0.85 / 0.79, hiking 0.45 / 0.17"]
```

| Decision                                                      | Inputs                                    | How                                                                                                                                                                                                                                                                                                                                                                                                                             |
|---------------------------------------------------------------|-------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Tobler weight** (how much walking curve to mix in)          | flat-equivalent speed, verticality, sport | Flat-equivalent speed = `Σ(leg_distance / minetti_speed) / active_seconds`, i.e. the speed with the terrain divided out. Verticality is the distance-weighted mean `                                   \|gradient\|`. Each is ramped through a `_smoothstep` band around the sport's `TOBLER_THRESHOLDS`, and the result is the `max()` of the two. Minetti is always the probe curve.                                          |
| **Max speed ratio** (how far a leg may swing from the median) | verticality, sport, smoothness            | Ramps from `MAX_SPEED_RATIO_BOUNDS[sport].flat` to `.hilly` around `HILLY_VERTICALITY` (0.06 ± 0.04), then raised to `SMOOTHNESS_SCALES[level]`. Level 5 leaves it unchanged.                                                                                                                                                                                                                                                   |
| **Curve shape** (exponents for both curves)                   | max speed ratio, sport                    | Exponents are chosen so that each curve's largest log-speed between flat and ±25% grade equals the sport's `CURVE_FILLS` share `× log(ratio)`. For a curve that keeps slowing, that is its value at ±25%; a curve that peaks and turns back is measured at the peak, so crossing flat speed near 25% can't blow the exponent up. The curve and the bound always narrow together, and the soft clamp only has to catch outliers. |

`CURVE_FILLS`, `MAX_SPEED_RATIO_BOUNDS` and `HILLY_VERTICALITY` are fitted by
the tuning harness (2026-09-29). `TOBLER_THRESHOLDS` and the band widths are
still first guesses.

### 5.8 `pacing/surface.py`

```mermaid
flowchart TD
  btap["build_trace_attributes_payload(track, costing='pedestrian')<br/>→ dict (shape = every point, shape_match walk_or_snap)"]
  csm["calculate_surface_multipliers(track, response, sport)<br/>→ one multiplier per leg"]
  rpei["resolve_point_edge_indexes(response, n)<br/>point → matched edge index, gaps filled from neighbours"]
  lcm["_leg_category_multiplier(edges, 'surface'|'road_class'|'use')"] --> lwa["_length_weighted_average"]
  lsm["_leg_sac_scale_multiplier(edges)"]
  csm --> rpei
  csm --> lcm
  csm --> lsm
  W[("_WEIGHTS_BY_SPORT<br/>from surface_weights.json")] --> csm
```

For each leg: `physical = 0.5·surface + 0.3·use + 0.2·road_class`. If the
leg has a SAC scale, one of two things happens:

- The SAC factor replaces `physical` outright when it's severe (≤ 0.85) and
  within 0.25 of `physical`.
- Otherwise, it's blended in: `0.7·sac + 0.3·physical`.

A leg with no matched edge gets 1.0. The HTTP request itself is made in
`pyodideBridge.js`.

### 5.9 `pacing/combine.py`: fitting the model to the anchors

```mermaid
flowchart TD
  C["combine(track, anchors, sport, multipliers?, mode_a_stops?, smoothness)"]
  C --> rab["_resolve_anchor_bounds(points, anchors)<br/>anchor → point index (bisect;<br/>a same-distance anchor pair takes the stop's two points)"]
  C --> bmas["_bucket_mode_a_stops(anchors, stops)<br/>Mode A stops per segment"]
  C --> cg["gradient.calculate_gradient(track)<br/>once, whole track"]
  C --> tss["_total_stop_seconds → active_seconds"]
  C --> r1["curve_selection.resolve_tobler_weight"]
  C --> r2["curve_selection.resolve_max_speed_ratio"]
  r2 --> r3["curve_selection.resolve_curve_shape"]
  C --> loop{{"for each consecutive anchor pair"}}
  loop --> ps["_pace_segment(points, gradients, a, b,<br/>weight, ratio, shape, multipliers, stops)"]
  ps --> b["gradient.blended_speeds_from_gradients"]
  ps --> sf["× max(surface multiplier, 0.05)"]
  ps --> cst["_compress_speed_toward_typical<br/>tanh in log space around the segment median"]
  ps --> sc["scale = (Δt − Mode A stop time) / Σ(d / v)"]
  ps --> st["stamp timestamps; add each Mode A<br/>stop's duration at its duplicate point"]
  ps -.-> err["InputError: time goes backwards ·<br/>stops exceed the segment's time"]
```

Within one segment, `_pace_segment` does the following, in order:

1. Stamp the first and last points from the two anchors.
2. `v_i` = blended curve speed × surface multiplier.
3. Compress `v_i` toward the segment median with
   `median · exp(L · tanh(ln(v/median) / L))`, where `L = ln(max_ratio)`.
   This is a soft clamp, so it never produces a flat plateau.
4. Modeled time per leg is `d_i / v_i`. Scale all of them so they add up
   to the segment's active time.
5. Walk the legs, adding the time. When the walk crosses a Mode A stop's
   zero-distance leg, add the stop's duration to that point and every later
   point.

Gradients are computed once for the whole track and then sliced per
segment, so smoothing isn't cut off at an anchor.
`tests/tuning/test_model_mirror.py` pins this function's exact output. See
[§10](#10-the-tuning-harness).

### 5.10 `fit_writer.py`

```
write_fit(track) -> bytes
  validate: every point has a timestamp, end > start
  FileIdMessage (manufacturer/product = track.device_manufacturer/_product, or DEVELOPMENT/0 when None;
                 serial only if track.device_serial; product_name = track.device)
  SportMessage (RUNNING | WALKING for hiking)
  Event START
  per point: [Event STOP_ALL + START if zero-distance leg took time]  RecordMessage(lat, lon, alt, distance, speed, ts)
  Event STOP_ALL
  LapMessage + SessionMessage (elapsed, timer = elapsed − paused, distance, ascent, avg/max speed)
  ActivityMessage
  → FitFileBuilder.build_bytes()
```

The per-point speed is recomputed here from the distance and time deltas.
The timer events are what make Strava count stops as stopped rather than as
slow moving time.

### 5.11 `activity_profile.py`

```
build_activity_profile(track, max_samples=1000) -> [ProfileSample]
  bucket legs by ~total_distance/1000 m → one sample per bucket (true distance/time speed)
  a stop leg closes the bucket and emits two zero-speed samples (arrival, departure, is_stop=True)
```

The chart in the GUI only handles presentation. All the reduction happens
here, in Python.

---

## 6. The bridge: `pyodideBridge.js`

This is the only file that talks to Python. It exports four calls and one
helper:

| Export                                  | Python it runs                                 | Returns                                     |
|-----------------------------------------|------------------------------------------------|---------------------------------------------|
| `parseGpx(bytes)`                       | `_track = parse_gpx_bytes(...)`                | `{points, summary}` for the map and sidebar |
| `resolveAnchorCandidates(lat, lon)`     | `nearest_point_candidates(_track, …)`          | 1–4 candidates                              |
| `resolvePhotoAnchors(readings, window)` | `resolve_photo_anchors(_track, …)`             | status per photo                            |
| `convert({...})`                        | the full pipeline ([§8.3](#83-inside-convert)) | `{fitBytes, profile}`                       |
| `classifyPyError(err)`                  | none                                           | tags `InputError`s ([§9](#9-errors))        |

Three mechanisms keep the bridge working:

1. **Lazy startup (`ensurePyodide`).** The first call does all the
   setup:
   - `loadPyodide` from jsdelivr
   - `micropip.install` `gpxpy` and `fit-tool`, pinned to the `uv.lock`
     versions (`GPXPY_VERSION`, `FIT_TOOL_VERSION`)
   - fetch every file in `CORE_FILES` over HTTP from `../../core/`, resolved
     against the bridge module's own URL (`import.meta.url`), so the site
     works whether the repo root or `src/gpx2fit/` is served
   - write them into `/workspace/src` in Pyodide's virtual filesystem, and
     prepend that directory to `sys.path`

   **A `core/` file that isn't in `CORE_FILES` doesn't exist in the
   browser**, and nothing warns about it until an import fails there.
2. **One shared namespace.** Every `runPythonAsync` call runs in the same
   `__main__`. JS passes inputs through `runtime.globals.set(...)`, Python
   leaves its results in globals, and JS reads them back with
   `globals.get(...).toJs(...)`. `_track` stays alive between calls, so a
   GPX file is parsed once and then reused for clicks, photos and every
   conversion.
3. **Serial queue (`enqueue`).** Calls are chained on one promise, so two
   overlapping calls can't overwrite each other's globals. A failed call
   rejects only for its own caller, and the next call in the queue still
   runs. `fetchSurfaceMultipliers` isn't exported and receives `runtime`
   directly, because it runs inside `convert`'s queued task. Queueing it
   again would deadlock.

---

## 7. The GUI

### 7.1 Module dependency graph

```mermaid
flowchart TD
  main["main.js<br/>entry, app state, upload + convert handlers"]
  bridge["pyodideBridge.js"]
  map["map.js<br/>Leaflet map, route, pins, hover marker, popups"]
  ap["anchorPopovers.js<br/>route-click flow: candidate picker →<br/>anchor-or-stop → time popover"]
  anchors["anchors.js<br/>anchor list state"]
  stops["stops.js<br/>stop list state + change listener"]
  ml["markerList.js<br/>shared list+pin bookkeeping"]
  aw["activityWindow.js<br/>is an anchor/stop inside start–end?"]
  photo["photoAnchors.js<br/>EXIF via exifr → resolve → addAnchor"]
  ti["timeInput.js<br/>createTimeToggle (duration / end / avg speed)"]
  dtf["dateTimeField.js<br/>custom date + time fields, segment linking"]
  pp["profilePanel.js<br/>slide-up panel, toggles"]
  pc["profileChart.js<br/>SVG chart"]
  mob["mobileLayout.js<br/>phone scroll sheet"]
  sc["shortcuts.js<br/>global keys + ? panel"]
  dp["devicePicker.js<br/>device button + &lt;dialog&gt;"]
  dev["devices.js<br/>catalog, validation, storage"]
  th["theme.js<br/>light/dark"]
  fmt["format.js<br/>formatting + describeError"]
  cfg["config.js<br/>Thunderforest key (gitignored)"]

  main --> bridge
  main --> map
  main --> ti
  main --> dtf
  main --> anchors
  main --> stops
  main --> ap
  main --> photo
  main --> pp
  main --> sc
  main --> dp
  dp --> dev
  dp --> ti
  main --> th
  main --> fmt
  main --> mob
  mob -. "mapModule injected" .-> map
  map --> mob
  ml --> mob
  ap --> bridge
  ap --> map
  ap --> anchors
  ap --> stops
  ap --> ti
  anchors --> ml
  stops --> ml
  anchors --> aw
  stops --> aw
  ap --> aw
  main --> aw
  ml --> map
  pp --> pc
  pp -. "mapModule injected" .-> map
  photo -. "resolvePhotoAnchors, addAnchor<br/>injected by main" .-> bridge
  ti --> dtf
  th --> map
  map --> cfg
  pc --> fmt
  photo --> fmt
  ti --> fmt
```

Dependencies are injected in two places. `photoAnchors.js` receives
`resolvePhotoAnchors`, `addAnchor` and `removeAnchor` as arguments rather
than importing them, so its tests can pass in fakes. `profilePanel.js`
receives `mapModule` so it can draw the hover marker. `mobileLayout.js`
receives `mapModule` so it can move the attribution. `map.js` itself imports
only `isPhoneLayout` from it, and `markerList.js` only `revealMap`.

### 7.2 Who owns which state

| State                                              | Owner                                                                                           | Read by                                                                                                                      |
|----------------------------------------------------|-------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| route points, total distance, GPX file name, sport | `main.js` (module variables)                                                                    | convert handler; getters passed to `anchorPopovers`, `photoAnchors`, `timeInput`                                             |
| start date/time                                    | `dateTimeField` instance in `main.js`                                                           | `getStartTime()`                                                                                                             |
| duration / end / avg-speed result                  | `createTimeToggle` instance (`startTimeToggle`)                                                 | `startTimeResult` cache and `getResult()` at convert time                                                                    |
| anchors                                            | `anchors.js`, backed by `markerList`                                                            | `getActiveAnchors()` at convert time                                                                                         |
| stops                                              | `stops.js`, backed by `markerList`                                                              | `getActiveStops()` at convert time; `getStops()` for the avg-speed stop total; the change listener refreshes the time toggle |
| activity window (start–end)                        | pushed by `startTimeToggle`'s `onChange` into `anchors.js` and `stops.js` (`setActivityWindow`) | each module's `isInactive` check and `getActive*()`                                                                          |
| parsed `Track`                                     | Python global `_track`                                                                          | every bridge call                                                                                                            |
| theme, surface toggle, smoothness                  | `localStorage`                                                                                  | `theme.js`, `main.js`                                                                                                        |

`main.js` calls `startTimeToggle.refresh()` whenever the average-speed
duration could change: after a GPX parses, on a sport change, and on any
stop change. The convert handler calls `getResult()` again rather than
trusting the cached value.

Every time that toggle's result changes (including a start-time edit),
`main.js` pushes the new `{start, end}` into `anchors.setActivityWindow` and
`stops.setActivityWindow`. Each module re-checks its items with
`activityWindow.js` (strictly inside, the same rule as the photo check).
A window with the same times as before (`sameWindow`) is ignored, and the
re-render (`markerList.refresh`) rebuilds the rows without renumbering the
pins, since a time change can't reorder them. An item outside is kept but marked `.is-inactive`, its pin is dimmed
(`map.setMarkerDimmed`), and it is left out of `getActive*()`. Moving the
window back makes it active again. The convert status then reports how
many were left out (`format.leftOutNote`). An anchor or stop time entered
as "Duration since start" is stored with its offset (`offsetSeconds`, or
`arrival`/`departureOffsetSeconds`), so it follows the start instead of
staying at a fixed clock time. `totalStopSeconds` counts every stop, active
or not: whether a stop is active depends on the end time, which in
avg-speed mode depends on that total.

### 7.3 Interaction flows

Clicking the route:

```mermaid
sequenceDiagram
  actor U as User
  participant M as map.js
  participant AP as anchorPopovers.js
  participant B as pyodideBridge
  participant P as Python
  participant L as anchors.js / stops.js
  U->>M: click on route polyline
  M->>AP: handleRouteClick(lat, lon)
  AP->>B: resolveAnchorCandidates(lat, lon)
  B->>P: nearest_point_candidates(_track, …)
  P-->>AP: 1..4 candidates
  alt more than one pass
    AP->>U: openCandidatePicker
    U->>AP: pick a pass
  end
  AP->>U: openKindChoicePopover (anchor or stop? — a hint instead at the route's first/last point)
  alt anchor
    AP->>U: openAnchorTimePopover (time of day | since start, with estimate hint, blocked outside start–end)
    AP->>L: anchors.addAnchor({lat, lon, distanceFromStart, timestamp, offsetSeconds?})
  else stop
    AP->>U: openStopPopover (duration | arrival+departure)
    AP->>L: stops.addStop({... mode ...})
  end
  L->>M: addMarker / renumberMarkers
```

Dropping photos:

```
photoAnchors.handleFiles
  guard: a route is loaded and the start time is valid
  readFile → readPhotoMetadata(file) → exifr: {lat, lon, DateTimeOriginal}
  resolvePhotoAnchors(readings, {startIso, endIso})     (one batch, one bridge call)
  per result: ok → addAnchor({..., source: 'photo'}) · too_far / outside_activity_time / at_route_end → row message only
  afterwards: a start/duration change re-checks the photo anchor like any other (anchors.setActivityWindow)
```

### 7.4 Smaller modules

- **`timeInput.js`**: `createTimeToggle({variant, speedMode?})`.
  - Variants are `durationOrEnd` (the main control), `durationOrTimeOfDay`
    (the anchor popover) and `durationOnly` (a stop's duration).
  - Every mode resolves to `{isValid, mode, resolvedDate, durationSeconds}`.
  - Average-speed mode computes
    `duration = distance / speed + total stop time` in JS.
- **`dateTimeField.js`**: custom date and time fields with quick-pick
  dropdowns. `linkSegmentPair` makes the arrow keys move between the
  hours and minutes boxes.
- **`shortcuts.js`**: each entry in one `SHORTCUTS` array both binds a key
  and renders a row in the "?" panel. Each action calls `.click()` on the
  existing control.
- **`devices.js`**: `DEVICE_CATALOG` (brand → `{name, manufacturer, product}`;
  only IDs with a real source: Garmin from `fit_tool`'s `GarminProduct`, Suunto
  from its own product-ID list, other brands from a recording Strava named or
  another FIT reader's device table: GoldenCheetah, Runalyze) plus pure helpers
  (`selectionLabel`, `parseCustomIds`, `parseStoredSelection`,
  `toConvertDevice`). Tested.
- **`devicePicker.js`**: the sidebar's device button and the native
  `<dialog>` it opens with `showModal()`. Persists the choice in
  `localStorage` (`device`). jsdom has no `showModal`, so it is untested.
- **`profileChart.js`**: pure helpers (`niceTicks`, `linearScale`,
  `buildSeries`, `stopRuns`, `averageMovingSpeedMps`, `linePath`, …) plus
  `createProfileChart`. The pure part has tests.
- **`format.js`**: every formatter, plus `describeError(err) →
  {message, kind: 'input'|'error'}`.
- **`mobileLayout.js`**: the phone layout (`PHONE_LAYOUT_QUERY`, ≤ 860 px,
  kept in step with `styles.css`). The CSS fixes the map full-screen behind
  the sidebar, which becomes a sheet in the page's scroll. The module:
  - opens the page with the sheet half-way up
  - moves `#profilePanel` and `#profileShowButton` to the top of the sheet,
    and moves the Leaflet attribution to the top right
  - `coveredMapHeight()` tells `map.js` how much of the map the sheet hides,
    via `setBottomInsetProvider`. `renderRoute` and `panToMarker` then aim at
    the visible part.
  - `revealMap()` scrolls the sheet back to half-way. It runs after a GPX
    parses and when an anchor or stop row is clicked.

  On phones, `map.js`'s `openAnchorPopup` builds the popover into the
  `#routeDialog` modal instead of a Leaflet popup. The builders in
  `anchorPopovers.js` get the same `(container, close, updateLayout)` either
  way. The layout logic is tested against a stubbed `matchMedia`.

---

## 8. End-to-end code flow

### 8.1 Timeline of one session

```mermaid
sequenceDiagram
  actor U as User
  participant Main as main.js
  participant B as pyodideBridge.js
  participant Py as Pyodide (core)
  participant V as Valhalla
  participant PP as profilePanel.js

  Note over Main: page load: init lists, map, theme, profile panel,<br/>shortcuts, time toggle, anchor placer, photo drop
  U->>Main: drop GPX
  Main->>B: parseGpx(bytes)
  B->>B: ensurePyodide() on first call:<br/>load runtime, micropip, fetch core/*.py
  B->>Py: _track = parse_gpx_bytes(bytes)
  Py-->>Main: points + {total_distance, total_elevation_gain}
  Main->>Main: reset anchors/stops/photos, hideProfile,<br/>renderRoute, refresh time toggle
  U->>Main: set start + duration/end/speed, place anchors/stops, drop photos
  Note over Main,Py: resolveAnchorCandidates / resolvePhotoAnchors (§7.3)
  U->>Main: Convert (button or Ctrl+Enter)
  Main->>Main: getResult() again, build anchor + stop payloads (ISO strings)
  Main->>B: convert({startIso, durationSeconds, sportEnumName,<br/>anchors, stops, device, surfaceLookup, smoothness})
  opt surfaceLookup
    B->>Py: build_trace_attributes_payload(_track)
    B->>V: POST trace_attributes (30 s timeout)
    V-->>B: edges + matched_points
    B->>Py: calculate_surface_multipliers(...)
    Note over B: on any failure: warn, continue with no multipliers
  end
  B->>Py: pipeline (§8.3)
  Py-->>B: fit_bytes, _profile_samples
  B-->>Main: {fitBytes, profile}
  Main->>U: Blob → download link (name from GPX)
  Main->>PP: showProfile(profile, sport)
```

### 8.2 Data flow

```mermaid
flowchart TD
  GPX[/"GPX bytes"/] --> parse["gpx_reader.parse_gpx_bytes"] --> T[("_track: Track<br/>no timestamps")]

  start[/"startIso + durationSeconds"/] --> ase["anchors.add_start_end_anchors"] --> boundary["boundary anchors (2)"]
  ua[/"mid-route + photo anchors<br/>{distanceFromStart, timestamp, source}"/] --> bua["anchors.build_user_anchors"] --> mid["mid-route anchors"]
  boundary --> hard["hard_anchors (sorted)"]
  mid --> hard
  T --> bua
  T --> ase

  st[/"stops {distanceFromStart,<br/>durationSeconds | startIso+endIso}"/] --> rs["stops.resolve_stops"]
  hard --> rs
  rs --> mb["Mode B: ResolvedStop"] --> sa["2 Anchors each<br/>stop_arrival / stop_departure"]
  rs --> ma["Mode A: ModeAStop"]
  hard --> all["all_anchors (sorted by distance, time)"]
  sa --> all

  T --> ets["stops.expand_track_with_stops"] --> WT[("working Track<br/>+1 point per stop, sport set")]
  mb --> ets
  ma --> ets

  T -. optional .-> sp["surface.build_trace_attributes_payload"] -.-> VH(("Valhalla")) -.-> csm["surface.calculate_surface_multipliers"] -.-> em["stops.expand_multipliers_with_stops"] -.-> WM["working multipliers"]

  WT --> comb["combine.combine"]
  all --> comb
  ma --> comb
  WM -.-> comb
  sm[/"smoothness"/] --> comb
  comb --> PT[("working Track, every point timestamped")]
  PT --> fw["fit_writer.write_fit"] --> FIT[/"FIT bytes → Blob → download"/]
  PT --> bap["activity_profile.build_activity_profile"] --> PS[/"ProfileSamples → profileChart"/]
```

### 8.3 Inside `convert()`

This is the literal order of the Python that `convert()` runs. The bridge
converts JSON and ISO strings into dataclasses first.

1. `boundary = add_start_end_anchors(_track, start, duration=…)`
2. `mid_route = build_user_anchors(_track, raw_anchors, existing_anchors=boundary)`.
   The GUI always sends `distanceFromStart`, so no lat/lon lookup happens
   here.
3. `hard_anchors = sorted(boundary + mid_route)`
4. `resolved_mode_b, mode_a_stops = resolve_stops(_track, raw_stops, hard_anchors)`
5. `all_anchors = sorted(hard_anchors + stop_arrival/departure anchors)`
6. `working_track = Track(expand_track_with_stops(_track.points, all_stops), sport, device, name)`,
   where `device` is `device_name or _track.device` plus the three device IDs.
   They go on the working track, never on `_track`, so clearing the device
   between two conversions of one route really clears it
7. `working_multipliers = expand_multipliers_with_stops(...)` (if surface
   data was fetched)
8. `combine(working_track, all_anchors, sport, working_multipliers, mode_a_stops, smoothness)`
9. `fit_bytes = write_fit(working_track)`
10. `_profile_samples = build_activity_profile(working_track)`, converted
    to camelCase dicts

Surface multipliers are computed against `_track` (not yet expanded), and
only then expanded to match `working_track`. Every conversion builds a fresh
working track, so the same `_track` can be converted any number of times.

---

## 9. Errors

```mermaid
flowchart LR
  raise["Python raises"] --> tb["Pyodide PythonError<br/>message = full traceback"]
  tb --> cls["classifyPyError (in enqueue)<br/>last line matches ^…InputError: msg ?"]
  cls -- yes --> ie["Error(msg), isInputError = true"]
  cls -- no --> raw["original error"]
  ie --> de["format.describeError → kind 'input'"] --> amber["status line, amber, message only"]
  raw --> de2["describeError → kind 'error'"] --> red["status line, red, full traceback"]
```

| Raise                         | When                                                                                                                                                                                                                                                                   |
|-------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `InputError`                  | The user can fix it: corrupt GPX, two anchors on one point, a stop on an anchor, departure ≤ arrival, anchor times out of order with distance, stops longer than their segment. Write the message for the user: plain words, real km and times, and a hint at the fix. |
| `ValueError` / `RuntimeError` | A broken internal contract, i.e. a bug between the GUI and `core/`. The traceback is shown so it can be debugged.                                                                                                                                                      |

`combine._describe_anchor` formats an anchor for these messages, for
example "photo anchor at 5.30 km (2026-09-13 14:02)".

---

## 10. The tuning harness

`tuning/` calibrates the pacing constants against real recorded activities.
It's development tooling that runs only on a developer's machine, and it's
documented separately in [TUNING_ARCHITECTURE.md](TUNING_ARCHITECTURE.md).

It touches the app in exactly three places:

- It imports `core/` directly (`gpx_reader`, `gradient`, `curve_selection`,
  `combine`, `anchors`, `models`) and never modifies it. Nothing from
  `tuning/` goes in `CORE_FILES`.
- `tuning/model.py` restates `combine._pace_segment` with the constants as
  arguments. `tests/tuning/test_model_mirror.py` asserts the two produce
  identical timestamps, so a change to `_pace_segment` or the resolvers in
  `curve_selection.py` means updating the mirror.
- What it proposes are new values for constants in `curve_selection.py`,
  applied by hand.

---

## 11. Tests

| Suite           | Location                            | Covers                                                                                                                                                                                                                                                                                                                                                                                    |
|-----------------|-------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| pytest, `core/` | `tests/core/`, `tests/core/pacing/` | every `core` module. Shared track builders live in `tests/core/conftest.py`                                                                                                                                                                                                                                                                                                               |
| pytest, harness | `tests/tuning/`                     | see [TUNING_ARCHITECTURE.md §13](TUNING_ARCHITECTURE.md#13-tests)                                                                                                                                                                                                                                                                                                                         |
| node, GUI       | `tests/gui/*.test.js`               | `format`, `dateTimeField`, `timeInput`, `markerList`, `activityWindow`, `anchors` + `stops` (`anchorsAndStops`: the activity-window re-check and `getActive*()`, with `map.js` mocked), `photoAnchors` (incl. `initPhotoDrop`'s per-status rows), `pyodideBridge` (against a fake Pyodide), `shortcuts`, `profileChart`, `devices`, `mobileLayout`. `testUtils/domSetup.js` sets up jsdom |
| untested        | none                                | `map.js`, `anchorPopovers.js`, `profilePanel.js`, `main.js` (Leaflet and popover UI), plus the row/pin rendering side of `anchors.js`/`stops.js`, which have to be checked by hand in a browser                                                                                                                                                                                           |

---

## 12. Change checklists

**Adding a module under `core/`**
- Add it to `CORE_FILES` in `pyodideBridge.js`.
- Keep it free of I/O and native extensions.
- Use `models.py` types.

**Adding a network call**
- Make it from JS (the bridge), never from `core/`.
- Treat a failure as optional if you can.
- Update `about.html#privacy` and the README's privacy table.

**Changing `_pace_segment` or the curve functions**
- Update `tuning/model.py` until `test_model_mirror.py` passes (see
  [TUNING_ARCHITECTURE.md §8](TUNING_ARCHITECTURE.md#8-modelpy-the-pacing-model-with-its-constants-as-arguments)).

**Adding a user-fixable validation**
- Raise `InputError` with a message written for the user.

**Adding a sport**
- Extend `SportType`.
- Add the sport to every `SportType`-keyed table: `TOBLER_THRESHOLDS`,
  `MAX_SPEED_RATIO_BOUNDS` and `surface_weights.json`.
- Update the sport mapping in `fit_writer`.
- Add a segment in the GUI's sport control and update `sportEnumName` in
  `main.js`.
- Cycling also needs its own speed model in `gradient.py`.

**Adding a keyboard shortcut**
- Add one entry to `SHORTCUTS` in `shortcuts.js`. It should `.click()` an
  existing control.

**Adding a persisted setting**
- Wrap the `localStorage` access in try/catch.
- Update the "Only four settings" line in `about.html#privacy`.
