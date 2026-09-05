// Owns the Pyodide runtime lifecycle and every call into core/. The parsed
// Track is kept alive as a plain Python global (`_track`) across separate
// runPythonAsync calls, since they all share the same __main__ namespace —
// this is what lets the GPX be parsed exactly once per upload and reused
// for the map preview, anchor resolution, and the final conversion.

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
      ['src/gpx2fit/core/pacing/gradient.py', await fetchText('/src/gpx2fit/core/pacing/gradient.py')],
      ['src/gpx2fit/core/pacing/combine.py', await fetchText('/src/gpx2fit/core/pacing/combine.py')],
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

export async function resolveAnchorDistance(lat, lon) {
  const runtime = await ensurePyodide();
  runtime.globals.set('click_lat', lat);
  runtime.globals.set('click_lon', lon);

  await runtime.runPythonAsync(`
from gpx2fit.core.pacing.anchors import nearest_point_distance_from_start

_clicked_distance = nearest_point_distance_from_start(_track, float(click_lat), float(click_lon))
`);

  const distance = runtime.globals.get('_clicked_distance');
  return Number(distance);
}

export async function convert({ startIso, durationSeconds, sportEnumName, anchors }) {
  const runtime = await ensurePyodide();
  runtime.globals.set('start_iso', startIso);
  runtime.globals.set('duration_seconds', durationSeconds);
  runtime.globals.set('sport_enum_name', sportEnumName);
  runtime.globals.set('raw_anchors_json', JSON.stringify(anchors));

  await runtime.runPythonAsync(`
import datetime as dt
import json

from gpx2fit.core.models import RawAnchor, SportType
from gpx2fit.core.pacing.anchors import add_start_end_anchors, build_user_anchors
from gpx2fit.core.pacing.combine import combine
from gpx2fit.core.fit_writer import write_fit

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

all_anchors = sorted(boundary + mid_route, key=lambda a: (a.distance_from_start, a.timestamp))
sport = SportType[sport_enum_name]
combine(track=_track, anchors=all_anchors, sport=sport)
fit_bytes = write_fit(_track)
`);

  return runtime.globals.get('fit_bytes').toJs({ create_proxies: false });
}
