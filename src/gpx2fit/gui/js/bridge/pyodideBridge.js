// Owns the Pyodide runtime lifecycle and every call into core/. Each export
// is one call to a function in core/bridge_api.py, which does the Python side
// of the work and keeps the parsed route between calls. So the GPX is parsed
// once per upload and reused for the map preview, anchor resolution, photo
// matching and every conversion. This module only loads the runtime, converts
// arguments and results between JS and Python, and makes the one network call
// core/ can't (Valhalla's surface lookup).

// Loaded from the CDN by `ensurePyodide`, not by a <script> in index.html, so
// the version is pinned here only, and the page doesn't wait on it to render.
const PYODIDE_VERSION = '0.27.3';
const PYODIDE_BASE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
// The subresource-integrity hash of that version's pyodide.js: the browser
// refuses the script if the CDN serves anything else. Bump it with the
// version: `curl -sL <url> | openssl dgst -sha384 -binary | openssl base64 -A`.
// (It covers this one file. The files pyodide.js fetches itself, the WASM and
// the standard library, have no such check.)
const PYODIDE_INTEGRITY = 'sha384-09G8vbnODTq2tbTbdd3cWId3kiO+u+UO+LBpDg64SG+qeYbALxB/WiDlA6LZTcia';

// Public Valhalla demo server — no API key needed.
const VALHALLA_TRACE_ATTRIBUTES_URL = 'https://valhalla1.openstreetmap.de/trace_attributes';
// The demo server has no SLA, and convert() treats a failed/slow fetch here
// as "no surface data" rather than blocking the whole conversion on it.
const VALHALLA_FETCH_TIMEOUT_MS = 30_000;

// Pinned to uv.lock, so a new upstream release can't change the live site
// without a deployment. Bump it together with the lock file. (FIT files are
// written by core/fit_encoder.py; fit-tool is only a test dependency.)
const GPXPY_VERSION = '1.6.2';

// Every core/ file the browser runtime needs, relative to core/. A module
// missing here simply doesn't exist inside Pyodide.
const CORE_FILES = [
  'models.py',
  'bridge_api.py',
  'gpx_reader.py',
  'fit_encoder.py',
  'fit_writer.py',
  'activity_profile.py',
  'pacing/anchors.py',
  'pacing/photo_anchors.py',
  'pacing/gradient.py',
  'pacing/curve_selection.py',
  'pacing/surface.py',
  'pacing/surface_weights.json',
  'pacing/combine.py',
  'pacing/stops.py',
  'pacing/variation.py',
  'pacing/acceleration.py',
];

// Where core/ is written inside Pyodide's virtual filesystem.
const PY_SOURCE_ROOT = '/workspace/src';

// How a Python result comes back: dicts as plain objects, and everything
// copied, so no proxy into Python's memory outlives the call.
const TO_JS_OPTIONS = { create_proxies: false, dict_converter: Object.fromEntries };

// Pyodide's runtime and its proxies are `any`: like the CDN globals (see
// types/cdn-globals.d.ts), they come untyped.
/** @type {Promise<any>|null} */
let pyodidePromise = null;
/** @type {any} */
let pyodide = null;
// The `gpx2fit.core.bridge_api` module, imported once the runtime is up.
/** @type {any} */
let bridgeApi = null;

// bridge_api keeps the parsed route between calls, and convert() awaits the
// surface lookup between two of them. A parse landing in that gap would
// swap the route under the conversion, so every call is funneled through
// this one-at-a-time queue. A call that throws still lets later ones run —
// only its own caller sees the error.
let queueTail = Promise.resolve();

// Pyodide surfaces every Python exception as a `PythonError` whose `message`
// is a full formatted traceback — useful for debugging a real bug, but
// unreadable noise for a problem actually caused by something the user
// entered (e.g. two anchors with contradictory times). `core.models.InputError`
// exists precisely to be told apart from that: since Python's traceback
// formatter always ends on a "<ExceptionClassName>: <message>" line, catching
// that one class by name here (rather than needing every call site below to
// catch and re-signal it individually) is enough to recover the clean
// message and flag it for the UI. Every other exception (including plain
// ValueErrors raised for internal contract violations) passes through
// unchanged, traceback and all.
export function classifyPyError(error) {
  const rawMessage = error instanceof Error ? error.message : String(error);
  const lines = rawMessage.split('\n').map((line) => line.trim()).filter(Boolean);
  const lastLine = lines[lines.length - 1] || '';
  const match = /^(?:[\w.]+\.)?InputError:\s*(.*)$/.exec(lastLine);
  if (!match) {
    return error;
  }
  return Object.assign(new Error(match[1]), { name: 'InputError', isInputError: true });
}

/**
 * The user-facing warning for a surface lookup that failed outright. The
 * conversion goes ahead without surface multipliers either way; this only
 * says why, since otherwise a dead server and a successful lookup would look
 * the same to the user.
 *
 * @param {any} error - whatever `ensureSurfaceResponse` threw
 * @returns {string}
 */
export function describeSurfaceFailure(error) {
  let reason;
  if (error?.name === 'AbortError') {
    reason = `Valhalla took too long to answer (over ${VALHALLA_FETCH_TIMEOUT_MS / 1000} s)`;
  } else if (error?.isValhallaHttpError) {
    reason = error.message;
  } else if (error instanceof TypeError) {
    // What fetch() rejects with when the request never got an answer.
    reason = "couldn't reach Valhalla; are you offline?";
  } else {
    reason = 'its answer could not be used';
  }
  return `Surface lookup failed (${reason}), so this file is paced on gradient alone.`;
}

/**
 * Runs `task` after every task queued before it, so two Python calls never
 * interleave. A Python error comes out through `classifyPyError`.
 *
 * @template T
 * @param {() => Promise<T>} task
 * @returns {Promise<T>}
 */
function enqueue(task) {
  const runPromise = queueTail.then(task).catch((error) => {
    throw classifyPyError(error);
  });
  queueTail = runPromise.then(() => undefined, () => undefined);
  return runPromise;
}

// Resolves to Pyodide's global `loadPyodide`, adding its <script> on first
// use. A failed load removes the tag again, so a retry fetches it afresh.
function loadPyodideScript() {
  if (typeof globalThis.loadPyodide === 'function') {
    return Promise.resolve(globalThis.loadPyodide);
  }
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = `${PYODIDE_BASE_URL}pyodide.js`;
    script.integrity = PYODIDE_INTEGRITY;
    script.crossOrigin = 'anonymous';
    script.addEventListener('load', () => resolve(globalThis.loadPyodide));
    script.addEventListener('error', () => {
      script.remove();
      reject(new Error(`Failed to load ${script.src}`));
    });
    document.head.append(script);
  });
}

// Resolved against this module's own URL, not the site root, so the app
// works both from a repo-root dev server (/src/gpx2fit/gui/...) and from a
// deployment that uploads src/gpx2fit/ alone (/gui/...).
async function fetchCoreFile(relativePath) {
  const url = new URL(`../../../core/${relativePath}`, import.meta.url);
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to load ${url}: ${response.status} ${response.statusText}`);
  }
  return response.text();
}

function writeSourceFile(runtime, relativePath, text) {
  const path = `${PY_SOURCE_ROOT}/${relativePath}`;
  runtime.FS.mkdirTree(path.slice(0, path.lastIndexOf('/')));
  runtime.FS.writeFile(path, text);
}

async function ensurePyodide() {
  if (pyodide) {
    return pyodide;
  }
  if (pyodidePromise) {
    return pyodidePromise;
  }

  pyodidePromise = (async () => {
    const loadPyodide = await loadPyodideScript();
    const runtime = await loadPyodide({ indexURL: PYODIDE_BASE_URL });
    await runtime.loadPackage(['micropip']);
    await runtime.runPythonAsync(`
    import micropip
    await micropip.install('gpxpy==${GPXPY_VERSION}')
    `);

    const sources = await Promise.all(CORE_FILES.map(fetchCoreFile));
    for (const packageDir of ['gpx2fit', 'gpx2fit/core', 'gpx2fit/core/pacing']) {
      writeSourceFile(runtime, `${packageDir}/__init__.py`, '');
    }
    CORE_FILES.forEach((relativePath, index) => {
      writeSourceFile(runtime, `gpx2fit/core/${relativePath}`, sources[index]);
    });
    runtime.runPython(`import sys; sys.path.insert(0, '${PY_SOURCE_ROOT}')`);

    bridgeApi = runtime.pyimport('gpx2fit.core.bridge_api');
    pyodide = runtime;
    return runtime;
  })();
  // A failed load (offline, a CDN hiccup) must not stick: forget it, so the
  // next call tries again instead of failing until the page is reloaded.
  pyodidePromise.catch(() => {
    pyodidePromise = null;
  });

  return pyodidePromise;
}

/**
 * Starts loading Pyodide once the page is idle, so the first upload doesn't
 * wait several seconds for it. Skipped when the browser asks to save data:
 * the runtime is then loaded on the first upload, as before. A failed warm-up
 * is only logged, and the upload tries again.
 */
export function warmUpPyodide() {
  // navigator.connection is Chromium-only, so the DOM types don't have it.
  if (/** @type {any} */ (globalThis.navigator)?.connection?.saveData) {
    return;
  }
  const start = () => {
    ensurePyodide().catch((error) => console.warn('Pyodide warm-up failed; it is retried on upload.', error));
  };
  if (typeof globalThis.requestIdleCallback === 'function') {
    globalThis.requestIdleCallback(start, { timeout: 5000 });
  } else {
    setTimeout(start, 1000);
  }
}

/**
 * Calls one `bridge_api` function and returns its result as plain JS.
 * Objects and arrays go over as Python dicts and lists (a Uint8Array as a
 * memoryview). Every proxy made on the way, the function's included, is
 * destroyed before returning, so no conversion's data stays alive in Python.
 * Only call from inside an `enqueue`d task, after `ensurePyodide`.
 *
 * @param {string} name - a `bridge_api` function
 * @param {...unknown} args
 * @returns {any} - typed by each export's own JSDoc
 */
function callBridge(name, ...args) {
  /** @type {any[]} */
  const pyArgs = [];
  const fn = bridgeApi[name];
  if (typeof fn !== 'function') {
    // Said here: the `finally` below would otherwise fail on fn.destroy() first.
    throw new Error(`bridge_api has no function ${name}`);
  }
  let result;
  try {
    // Converted inside the try, so an argument that fails to convert still
    // frees the ones converted before it.
    args.forEach((arg) => pyArgs.push(arg !== null && typeof arg === 'object' ? pyodide.toPy(arg) : arg));
    result = fn(...pyArgs);
  } finally {
    fn.destroy();
    pyArgs.forEach((arg) => arg?.destroy?.());
  }
  if (typeof result?.toJs !== 'function') {
    return result;
  }
  try {
    return result.toJs(TO_JS_OPTIONS);
  } finally {
    result.destroy();
  }
}

/**
 * Parses uploaded GPX bytes into the route every later call uses, and
 * returns a JS-friendly preview of it. Call this once per upload.
 *
 * Rejects with an `Error` whose `isInputError` is `true` (see
 * `classifyPyError`) if the file isn't valid GPX or has no track or route points —
 * callers should show that message to the user as-is. The previous route
 * stays loaded then.
 *
 * @param {Uint8Array} bytes - raw contents of the uploaded .gpx file
 * @returns {Promise<{
 *   points: {lat: number, lon: number}[],
 *   summary: {total_distance: number, total_elevation_gain: number},
 *   warnings: string[],
 * }>}
 */
export async function parseGpx(bytes) {
  return enqueue(async () => {
    await ensurePyodide();
    return callBridge('parse', bytes);
  });
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
  return enqueue(async () => {
    await ensurePyodide();
    return callBridge('nearest_candidates', lat, lon);
  });
}

/**
 * Matches photos' GPS readings to the parsed route (see
 * `photo_anchors.resolve_photo_anchors`). The frontend never computes
 * distances itself, it just reads `status` per reading. There is no activity
 * window here: every match is added, and the window decides later whether
 * it's used (see photoAnchors.js).
 *   A match on the route's first or last point resolves as "at_route_end":
 *   the start/end anchors already sit there, so it can't be an anchor too.
 *   A spot the route passes more than once (an out-and-back) resolves as
 *   "ambiguous", with `passes` listing each pass (in
 *   `resolveAnchorCandidates`' shape) for the user to pick from.
 *
 * @param {{lat: number, lon: number, timestamp: string}[]} photoReadings -
 *   EXIF-derived GPS+timestamp per photo, timestamp as ISO 8601
 * @returns {Promise<{status: 'ok'|'too_far'|'at_route_end'|'ambiguous', lat: number, lon: number, distanceFromStart: number, timestamp: string, gapM: number, passes: {lat: number, lon: number, distance_from_start: number}[]}[]>}
 */
export async function resolvePhotoAnchors(photoReadings) {
  return enqueue(async () => {
    await ensurePyodide();
    return callBridge('resolve_photos', photoReadings);
  });
}

/**
 * Makes sure Python has Valhalla's trace_attributes answer for the parsed
 * route. The payload is built, and the answer kept and read, in Python
 * (`bridge_api`, `core/pacing/surface.py`); this function's only job is the
 * network round trip, since `core/` must stay I/O-free. The answer depends
 * only on the route, so it's fetched once per route: `trace_payload` returns
 * nothing once one is stored, and a failed lookup stores nothing, so the
 * next conversion asks again. The fetch is aborted after
 * `VALHALLA_FETCH_TIMEOUT_MS` so a hung public demo server can't stall
 * `convert()` indefinitely.
 *
 * Not exported: only `convert()` calls it, from inside its own queued task.
 *
 * @returns {Promise<void>} rejects if the lookup failed or Python couldn't
 *   read the answer
 */
async function ensureSurfaceResponse() {
  const payload = callBridge('trace_payload');
  if (payload == null) {
    return;
  }

  const abortController = new AbortController();
  const timeoutId = setTimeout(() => abortController.abort(), VALHALLA_FETCH_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(VALHALLA_TRACE_ATTRIBUTES_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: abortController.signal,
    });
  } finally {
    clearTimeout(timeoutId);
  }
  if (!response.ok) {
    // Valhalla explains a rejection in its JSON body (e.g. a route over its
    // distance or shape-point limit); that's the most useful thing to show.
    let detail = '';
    try {
      const body = await response.json();
      detail = typeof body?.error === 'string' ? `: ${body.error}` : '';
    } catch {
      // Not JSON (a proxy's error page, say); the status alone will do.
    }
    throw Object.assign(new Error(`Valhalla answered ${response.status} ${response.statusText}${detail}`), {
      isValhallaHttpError: true,
    });
  }
  callBridge('store_surface_response', await response.json());
}

/**
 * Runs the full pacing + FIT-encoding pipeline over the parsed route
 * (`bridge_api.convert`): builds start/end anchors from the given duration,
 * merges in any mid-route anchors and stops, fits per-leg speeds, and writes
 * a FIT file. It also returns a downsampled profile of the paced result for
 * the chart under the map.
 *
 * Rejects with an `Error` whose `isInputError` is `true` (see
 * `classifyPyError`) if the given anchors/stops contradict each other (e.g.
 * two anchors resolving to the same point, or timestamps that don't
 * increase in the same order as distance along the route) — callers should
 * show that message to the user as-is, not as a generic failure.
 *
 * @param {object} args
 * @param {string} args.startIso - route start time, ISO 8601
 * @param {number} args.durationSeconds - total planned duration of the activity
 * @param {'running'|'hiking'} args.sport
 * @param {{distanceFromStart: number, timestamp: string, source: string}[]} args.anchors -
 *   mid-route anchors (timestamp as ISO 8601); start/end anchors are added internally
 * @param {{distanceFromStart: number, durationSeconds?: number, startIso?: string, endIso?: string}[]} [args.stops] -
 *   mid-route stops: exactly one of durationSeconds (Mode A) or
 *   startIso+endIso (Mode B) per entry
 * @param {{name?: string, manufacturer?: number, product?: number, serialNumber?: number}} [args.device] -
 *   the FIT device identity: `manufacturer` + `product` are what Strava matches to show a
 *   device, `name` is written as `product_name` (falling back to the GPX's creator), and
 *   no `manufacturer` means a neutral "development" ID (see `fit_writer.write_fit`)
 * @param {boolean} [args.surfaceLookup=true] - whether to pace on the route's surface,
 *   looked up from Valhalla once per route; `false` skips the request entirely, so the
 *   route's coordinates never leave the browser (the user-facing privacy opt-out)
 * @param {number} [args.smoothness=5] - pace-smoothness level, an integer 1–10
 *   (higher is more even pacing, 5 is the automatic terrain/sport-based swing);
 *   see `curve_selection.resolve_max_speed_ratio`
 * @returns {Promise<{
 *   fitBytes: Uint8Array<ArrayBuffer>,
 *   profile: import('../profile/profileChart.js').ProfileSample[],
 *   warnings: string[],
 * }>} the encoded FIT file, the paced activity's profile samples, and
 *   user-facing warnings about a conversion that went through but may not be
 *   what the user meant (a failed surface lookup first, then Python's own:
 *   surface data unmatched for much of the route, a segment whose anchors
 *   imply an implausible pace)
 */
export async function convert({
  startIso,
  durationSeconds,
  sport,
  anchors,
  stops,
  device,
  surfaceLookup = true,
  smoothness = 5,
}) {
  return enqueue(async () => {
    await ensurePyodide();
    const warnings = [];

    // The surface lookup is best-effort: a Valhalla outage or network hiccup
    // must not block the whole conversion, so any failure here just means
    // the FIT file comes out without surface-based pacing, and the user is
    // told. Python then has no answer stored, which is the same path as
    // opting out, minus the warning.
    if (surfaceLookup) {
      try {
        await ensureSurfaceResponse();
      } catch (err) {
        console.warn('Surface lookup failed; continuing without surface-based pacing.', err);
        warnings.push(describeSurfaceFailure(err));
      }
    }

    const result = callBridge('convert', {
      startIso,
      durationSeconds,
      sport,
      anchors,
      stops: stops ?? [],
      device: device ?? null,
      surfaceLookup,
      smoothness,
    });
    return { fitBytes: result.fitBytes, profile: result.profile, warnings: [...warnings, ...result.warnings] };
  });
}
