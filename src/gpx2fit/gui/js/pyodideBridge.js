// Owns the Pyodide runtime lifecycle and every call into core/. The parsed
// Track is kept alive as a plain Python global (`_track`) across separate
// runPythonAsync calls, since they all share the same __main__ namespace —
// this is what lets the GPX be parsed exactly once per upload and reused
// for the map preview, anchor resolution, and the final conversion.

/* global loadPyodide */
// loadPyodide is loaded globally via the <script> tag in index.html, not
// imported — this directive just tells the IDE/linter it's an intentional
// external global, not a typo. Everything chained off its return value
// (`.loadPackage`, `.globals.get(...).toJs()`, etc.) is untyped for the same
// reason; that's expected, not a bug.

let pyodidePromise = null;
let pyodide = null;

async function ensurePyodide() {
  if (pyodide) {
    return pyodide;
  }
  if (pyodidePromise) {
    return pyodidePromise;
  }

  pyodidePromise = (async () => {
    const runtime = await loadPyodide({ indexURL: 'https://cdn.jsdelivr.net/pyodide/v0.27.3/full/' });
    await runtime.loadPackage(['micropip']);

    await runtime.runPythonAsync(`
import micropip
await micropip.install('gpxpy')
await micropip.install('fit-tool')
`);

    const fetchText = async (path) => {
      const response = await fetch(path);
      if (!response.ok) {
        throw new Error(`Failed to load ${path}: ${response.status} ${response.statusText}`);
      }
      return response.text();
    };

    const backendFiles = [
      ['src/gpx2fit/__init__.py', ''],
      ['src/gpx2fit/core/__init__.py', ''],
      ['src/gpx2fit/core/pacing/__init__.py', ''],
      ['src/gpx2fit/core/models.py', await fetchText('/src/gpx2fit/core/models.py')],
      ['src/gpx2fit/core/gpx_reader.py', await fetchText('/src/gpx2fit/core/gpx_reader.py')],
      ['src/gpx2fit/core/fit_writer.py', await fetchText('/src/gpx2fit/core/fit_writer.py')],
      ['src/gpx2fit/core/pacing/anchors.py', await fetchText('/src/gpx2fit/core/pacing/anchors.py')],
      ['src/gpx2fit/core/pacing/photo_anchors.py', await fetchText('/src/gpx2fit/core/pacing/photo_anchors.py')],
      ['src/gpx2fit/core/pacing/gradient.py', await fetchText('/src/gpx2fit/core/pacing/gradient.py')],
      ['src/gpx2fit/core/pacing/combine.py', await fetchText('/src/gpx2fit/core/pacing/combine.py')],
      ['src/gpx2fit/core/pacing/stops.py', await fetchText('/src/gpx2fit/core/pacing/stops.py')],
    ];

    runtime.globals.set('backend_files_json', JSON.stringify(backendFiles));

    await runtime.runPythonAsync(`
import json
import pathlib
import sys

root = pathlib.Path('/workspace')
root.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, '/workspace/src')

for rel_path, content in json.loads(backend_files_json):
    target = root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
`);

    pyodide = runtime;
    return runtime;
  })();

  return pyodidePromise;
}

/**
 * Parses uploaded GPX bytes into a `Track` (kept alive Python-side as
 * `_track`) and returns a JS-friendly preview of it. Call this once per
 * upload — `resolveAnchorCandidates` and `convert` both reuse `_track`
 * rather than reparsing.
 *
 * @param {Uint8Array} bytes - raw contents of the uploaded .gpx file
 * @returns {Promise<{
 *   points: {lat: number, lon: number, elevation: number, distance: number}[],
 *   summary: {total_distance: number, total_elevation_gain: number},
 * }>}
 */
export async function parseGpx(bytes) {
  const runtime = await ensurePyodide();
  runtime.globals.set('gpx_bytes', bytes);

  await runtime.runPythonAsync(`
from gpx2fit.core.gpx_reader import parse_gpx_bytes

_track = parse_gpx_bytes(bytes(gpx_bytes.to_py()))
if not _track.points:
    raise ValueError('No points were found in the GPX file.')

route_points = [
    {"lat": p.lat, "lon": p.lon, "elevation": p.elevation, "distance": p.distance_from_start}
    for p in _track.points
]
route_summary = {
    "total_distance": _track.total_distance,
    "total_elevation_gain": _track.total_elevation_gain,
}
`);

  const toJsOpts = { create_proxies: false, dict_converter: Object.fromEntries };
  const points = runtime.globals.get('route_points').toJs(toJsOpts);
  const summary = runtime.globals.get('route_summary').toJs(toJsOpts);
  return { points, summary };
}

/**
 * Finds the track point(s) nearest to a map click. Returns more than one
 * candidate when the route passes near this spot multiple times (e.g. an
 * out-and-back) — the caller is responsible for disambiguating.
 *
 * @param {number} lat
 * @param {number} lon
 * @returns {Promise<{lat: number, lon: number, distance_from_start: number}[]>}
 */
export async function resolveAnchorCandidates(lat, lon) {
  const runtime = await ensurePyodide();
  runtime.globals.set('click_lat', lat);
  runtime.globals.set('click_lon', lon);

  await runtime.runPythonAsync(`
from gpx2fit.core.pacing.anchors import nearest_point_candidates

_anchor_candidates = nearest_point_candidates(_track, float(click_lat), float(click_lon))
`);

  const toJsOpts = { create_proxies: false, dict_converter: Object.fromEntries };
  return runtime.globals.get('_anchor_candidates').toJs(toJsOpts);
}

/**
 * Resolves a batch of photo-derived GPS+timestamp readings against the
 * already-parsed route. For each reading, finds the nearest track point
 * (`photo_anchors.resolve_photo_anchors`, reusing the same nearest-point
 * lookup map-click anchors use) and reports whether it's close enough to
 * trust — the frontend never computes distances itself, it just reads
 * `status` per reading.
 *
 * @param {{lat: number, lon: number, timestamp: string}[]} photoReadings -
 *   EXIF-derived GPS+timestamp per photo, timestamp as ISO 8601
 * @returns {Promise<{status: 'ok'|'too_far', lat: number, lon: number, distanceFromStart: number, timestamp: string, gapM: number}[]>}
 */
export async function resolvePhotoAnchors(photoReadings) {
  const runtime = await ensurePyodide();
  runtime.globals.set('photo_readings_json', JSON.stringify(photoReadings));

  await runtime.runPythonAsync(`
import datetime as dt
import json

from gpx2fit.core.pacing.photo_anchors import RawPhotoAnchor, resolve_photo_anchors

_raw_photo_anchors = [
    RawPhotoAnchor(lat=r["lat"], lon=r["lon"], timestamp=dt.datetime.fromisoformat(r["timestamp"]))
    for r in json.loads(photo_readings_json)
]
_resolved_photo_anchors = [
    {
        "status": r.status,
        "lat": r.lat,
        "lon": r.lon,
        "distanceFromStart": r.distance_from_start,
        "timestamp": r.timestamp.isoformat(),
        "gapM": r.gap_m,
    }
    for r in resolve_photo_anchors(_track, _raw_photo_anchors)
]
`);

  const toJsOpts = { create_proxies: false, dict_converter: Object.fromEntries };
  return runtime.globals.get('_resolved_photo_anchors').toJs(toJsOpts);
}

/**
 * Runs the full pacing + FIT-encoding pipeline over the already-parsed
 * `_track`: builds start/end anchors from the given duration, merges in any
 * mid-route anchors and stops, fits per-leg speeds, and writes a FIT file.
 * Pacing runs against a fresh working copy of `_track`'s points (expanded
 * with a duplicate point per stop) — `_track` itself is never structurally
 * mutated, since it's a persistent global reused across repeated calls.
 *
 * @param {object} args
 * @param {string} args.startIso - route start time, ISO 8601
 * @param {number} args.durationSeconds - total planned duration of the activity
 * @param {'RUNNING'|'HIKING'} args.sportEnumName - name of a `SportType` member
 * @param {{distanceFromStart: number, timestamp: string, source: string}[]} args.anchors -
 *   mid-route anchors (timestamp as ISO 8601); start/end anchors are added internally
 * @param {{distanceFromStart: number, durationSeconds?: number, startIso?: string, endIso?: string}[]} [args.stops] -
 *   mid-route stops: exactly one of durationSeconds (Mode A) or
 *   startIso+endIso (Mode B) per entry
 * @param {string} [args.device] - device name to embed in the FIT file, applied to `_track.device`
 * @returns {Promise<Uint8Array>} the encoded FIT file
 */
export async function convert({ startIso, durationSeconds, sportEnumName, anchors, stops, device }) {
  const runtime = await ensurePyodide();
  runtime.globals.set('start_iso', startIso);
  runtime.globals.set('duration_seconds', durationSeconds);
  runtime.globals.set('sport_enum_name', sportEnumName);
  runtime.globals.set('raw_anchors_json', JSON.stringify(anchors));
  runtime.globals.set('raw_stops_json', JSON.stringify(stops ?? []));
  runtime.globals.set('device_name', device ?? null);

  await runtime.runPythonAsync(`
import datetime as dt
import json

from gpx2fit.core.models import Anchor, RawAnchor, RawStop, SportType, Track
from gpx2fit.core.pacing.anchors import add_start_end_anchors, build_user_anchors
from gpx2fit.core.pacing.combine import combine
from gpx2fit.core.pacing.stops import expand_track_with_stops, resolve_stops
from gpx2fit.core.fit_writer import write_fit

if device_name:
    _track.device = device_name

start = dt.datetime.fromisoformat(start_iso)
boundary = add_start_end_anchors(
    track=_track,
    start_time=start,
    duration=dt.timedelta(seconds=float(duration_seconds)),
)

raw_anchor_dicts = json.loads(raw_anchors_json)
raw_anchors = [
    RawAnchor(
        timestamp=dt.datetime.fromisoformat(a["timestamp"]),
        distance_from_start=a["distanceFromStart"],
        source=a.get("source", "user"),
    )
    for a in raw_anchor_dicts
]
mid_route = build_user_anchors(_track, raw_anchors)
hard_anchors = sorted(boundary + mid_route, key=lambda a: (a.distance_from_start, a.timestamp))
sport = SportType[sport_enum_name]

raw_stop_dicts = json.loads(raw_stops_json)
raw_stops = [
    RawStop(
        distance_from_start=s["distanceFromStart"],
        duration=dt.timedelta(seconds=float(s["durationSeconds"])) if s.get("durationSeconds") is not None else None,
        start_timestamp=dt.datetime.fromisoformat(s["startIso"]) if s.get("startIso") else None,
        end_timestamp=dt.datetime.fromisoformat(s["endIso"]) if s.get("endIso") else None,
    )
    for s in raw_stop_dicts
]

resolved_mode_b, mode_a_stops = resolve_stops(_track, raw_stops, hard_anchors)
stop_anchors = [
    a
    for rs in resolved_mode_b
    for a in (
        Anchor(rs.distance_from_start, rs.arrival, source="stop_arrival"),
        Anchor(rs.distance_from_start, rs.departure, source="stop_departure"),
    )
]
all_anchors = sorted(hard_anchors + stop_anchors, key=lambda a: (a.distance_from_start, a.timestamp))

working_points = expand_track_with_stops(_track.points, [*resolved_mode_b, *mode_a_stops])
working_track = Track(points=working_points, sport=_track.sport, device=_track.device, activity_name=_track.activity_name)
combine(track=working_track, anchors=all_anchors, sport=sport, mode_a_stops=mode_a_stops)
fit_bytes = write_fit(working_track)
`);

  return runtime.globals.get('fit_bytes').toJs({ create_proxies: false });
}
