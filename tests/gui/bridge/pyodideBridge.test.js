import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it } from 'node:test';

import {
  classifyPyError,
  convert,
  describeSurfaceFailure,
  parseGpx,
  resolveAnchorCandidates,
  resolvePhotoAnchors,
  warmUpPyodide,
} from '../../../src/gpx2fit/gui/js/bridge/pyodideBridge.js';

describe('classifyPyError', () => {
  it('extracts the message and tags isInputError for a traceback ending in InputError', () => {
    const pyError = new Error(
      'Traceback (most recent call last):\n  File "combine.py", line 42, in _pace_segment\nInputError: Anchor at 4.2km is earlier than the anchor before it.',
    );
    const result = classifyPyError(pyError);
    assert.equal(result.isInputError, true);
    assert.equal(result.name, 'InputError');
    assert.equal(result.message, 'Anchor at 4.2km is earlier than the anchor before it.');
  });

  it('handles trailing blank lines after the InputError line', () => {
    const pyError = new Error('InputError: Two anchors resolve to the same point.\n\n  \n');
    const result = classifyPyError(pyError);
    assert.equal(result.isInputError, true);
    assert.equal(result.message, 'Two anchors resolve to the same point.');
  });

  it('passes through a traceback ending in a different exception class unchanged', () => {
    const pyError = new Error(
      'Traceback (most recent call last):\n  File "combine.py", line 10, in combine\nValueError: missing sport',
    );
    const result = classifyPyError(pyError);
    assert.equal(result, pyError);
    assert.equal(result.isInputError, undefined);
  });

  it('passes through a non-Error value unchanged', () => {
    const value = 'some non-Error thrown value';
    assert.equal(classifyPyError(value), value);
  });

  it('passes through an error with an empty message unchanged', () => {
    const pyError = new Error('');
    assert.equal(classifyPyError(pyError), pyError);
  });
});

describe('describeSurfaceFailure', () => {
  it('says the server was too slow for an aborted request', () => {
    const error = new Error('The operation was aborted.');
    error.name = 'AbortError';
    assert.match(describeSurfaceFailure(error), /took too long/);
  });

  it("says the server couldn't be reached for a network failure", () => {
    assert.match(describeSurfaceFailure(new TypeError('Failed to fetch')), /couldn't reach/);
  });

  it('always ends on what it means for the file', () => {
    for (const error of [new TypeError('Failed to fetch'), new Error('anything'), 'not even an Error']) {
      assert.match(describeSurfaceFailure(error), /^Surface lookup failed \(.+\), so this file is paced on gradient alone\.$/);
    }
  });
});

// A stand-in Pyodide runtime: Python never runs. `pyimport` hands back a fake
// core/bridge_api module whose functions record their arguments and answer
// from `responses` (a value, or a function of the arguments; an Error is
// thrown). Every proxy it makes (an argument, a function, a result) is
// tracked until destroyed, so a test can check none is left alive.
// pyodideBridge.js caches the runtime after its first load, so all tests
// share this one instance.
function fakePyodideRuntime() {
  const live = new Set();
  const runtime = {
    calls: [],
    responses: {},
    files: new Map(),
    scripts: [],
    imported: [],
    live,
    reset() {
      runtime.calls.length = 0;
      runtime.responses = {};
      live.clear();
    },
    loadPackage: async () => {},
    runPythonAsync: async (source) => {
      runtime.scripts.push(source);
    },
    runPython: (source) => {
      runtime.scripts.push(source);
    },
    FS: {
      mkdirTree: () => {},
      writeFile: (path, text) => runtime.files.set(path, text),
    },
    toPy: (value) => track({ pyValue: value }),
    pyimport: (name) => {
      runtime.imported.push(name);
      return new Proxy({}, { get: (_, fnName) => bridgeFunction(fnName) });
    },
  };

  function track(proxy) {
    live.add(proxy);
    proxy.destroy = () => live.delete(proxy);
    return proxy;
  }

  function bridgeFunction(name) {
    const fn = (...args) => {
      const plainArgs = args.map((arg) => (arg !== null && typeof arg === 'object' && 'pyValue' in arg ? arg.pyValue : arg));
      runtime.calls.push({ name, args: plainArgs });
      const response = runtime.responses[name];
      const value = typeof response === 'function' ? response(...plainArgs) : response;
      if (value instanceof Error) {
        throw value;
      }
      return track({ toJs: () => value ?? null });
    };
    return track(fn);
  }

  return runtime;
}

const fakeRuntime = fakePyodideRuntime();

function callsTo(name) {
  return fakeRuntime.calls.filter((call) => call.name === name);
}

// The convert() result bridge_api would give back, with no warnings of its own.
const CONVERTED = { fitBytes: new Uint8Array([1, 2, 3]), profile: [], warnings: [] };

// Before every describe that loads fakeRuntime: once a load succeeds the
// bridge keeps it, and these need the bridge not to have a runtime yet.
describe('loading the runtime', () => {
  let originalDocument;
  let originalNavigator;
  let originalWarn;

  beforeEach(() => {
    originalDocument = globalThis.document;
    originalNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
    originalWarn = console.warn;
  });

  afterEach(() => {
    delete globalThis.loadPyodide;
    delete globalThis.requestIdleCallback;
    globalThis.document = originalDocument;
    if (originalNavigator) {
      Object.defineProperty(globalThis, 'navigator', originalNavigator);
    }
    console.warn = originalWarn;
  });

  it('is retried on the next call after a failure, instead of failing until a reload', async () => {
    let attempts = 0;
    globalThis.loadPyodide = async () => {
      attempts += 1;
      throw new TypeError('Failed to fetch');
    };

    await assert.rejects(parseGpx(new Uint8Array([1])), /Failed to fetch/);
    await assert.rejects(parseGpx(new Uint8Array([1])), /Failed to fetch/);

    assert.equal(attempts, 2);
  });

  it("adds Pyodide's own script, pinned to one version, and drops a tag that failed to load", async () => {
    const appended = [];
    globalThis.document = {
      head: { append: (element) => appended.push(element) },
      createElement: () => {
        const listeners = {};
        return {
          removed: false,
          addEventListener: (type, listener) => {
            listeners[type] = listener;
          },
          remove() {
            this.removed = true;
          },
          fire: (type) => listeners[type](),
        };
      },
    };

    const firstTry = parseGpx(new Uint8Array([1]));
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(appended.length, 1);
    assert.match(appended[0].src, /^https:\/\/cdn\.jsdelivr\.net\/pyodide\/v\d+\.\d+\.\d+\/full\/pyodide\.js$/);
    appended[0].fire('error');
    await assert.rejects(firstTry, /Failed to load/);
    // Subresource integrity: a CDN that serves other bytes gets the script
    // refused. It needs a CORS request, hence crossOrigin. (Checked once the
    // load has settled, so a failure here fails rather than hangs.)
    assert.match(appended[0].integrity, /^sha384-[A-Za-z0-9+/]{64}$/);
    assert.equal(appended[0].crossOrigin, 'anonymous');
    assert.equal(appended[0].removed, true);

    const secondTry = parseGpx(new Uint8Array([1]));
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(appended.length, 2, 'a retry adds a fresh tag');
    globalThis.loadPyodide = async () => {
      throw new Error('stop here');
    };
    appended[1].fire('load');
    await assert.rejects(secondTry, /stop here/);
  });

  it('warms up when the page is idle, and only logs a failure', async () => {
    let attempts = 0;
    globalThis.loadPyodide = async () => {
      attempts += 1;
      throw new TypeError('Failed to fetch');
    };
    const idle = [];
    globalThis.requestIdleCallback = (callback) => idle.push(callback);
    const logged = [];
    console.warn = (...args) => logged.push(args);

    warmUpPyodide();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(attempts, 0, 'nothing loads before the page is idle');
    assert.equal(idle.length, 1);
    idle.forEach((callback) => callback());
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(attempts, 1);
    assert.equal(logged.length, 1);
  });

  it('does not warm up when the browser asks to save data', () => {
    Object.defineProperty(globalThis, 'navigator', { value: { connection: { saveData: true } }, configurable: true });
    const idle = [];
    globalThis.requestIdleCallback = (callback) => idle.push(callback);

    warmUpPyodide();

    assert.equal(idle.length, 0);
  });
});

describe('the bridge calls', () => {
  let fetchedUrls;
  let originalFetch;

  beforeEach(() => {
    fetchedUrls = [];
    originalFetch = globalThis.fetch;
    globalThis.loadPyodide = async () => fakeRuntime;
    globalThis.fetch = async (url) => {
      fetchedUrls.push(String(url));
      return { ok: true, text: async () => `# ${url}`, json: async () => ({}) };
    };
    fakeRuntime.reset();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    delete globalThis.loadPyodide;
  });

  // Must stay the first call in this file that loads: the runtime is loaded
  // (and core/ fetched) only once.
  it('loads core/ next to the bridge module, pins gpxpy, and imports bridge_api', async () => {
    fakeRuntime.responses.parse = { points: [], summary: {}, warnings: [] };
    await parseGpx(new Uint8Array([1]));

    const coreUrls = fetchedUrls.filter((url) => url.includes('/core/'));
    for (const file of ['models.py', 'bridge_api.py', 'fit_encoder.py', 'pacing/combine.py']) {
      const expected = new URL(`../../../src/gpx2fit/core/${file}`, import.meta.url).href;
      assert.ok(coreUrls.includes(expected), `expected ${expected} among ${coreUrls.join(', ')}`);
    }
    // Each fetched file lands at its package path, with the package __init__s.
    assert.equal(fakeRuntime.files.get('/workspace/src/gpx2fit/core/bridge_api.py'), `# ${new URL('../../../src/gpx2fit/core/bridge_api.py', import.meta.url).href}`);
    assert.equal(fakeRuntime.files.get('/workspace/src/gpx2fit/core/pacing/__init__.py'), '');
    assert.ok(fakeRuntime.scripts.some((source) => source.includes("sys.path.insert(0, '/workspace/src')")));
    assert.deepEqual(fakeRuntime.imported, ['gpx2fit.core.bridge_api']);

    const installScript = fakeRuntime.scripts.find((source) => source.includes('micropip.install'));
    assert.match(installScript, /micropip\.install\('gpxpy==[\d.]+'\)/);
    // core/fit_encoder.py writes the FIT bytes itself; fit_tool is only the tests' oracle now.
    assert.doesNotMatch(installScript, /fit-tool/);
  });

  it('parseGpx hands the bytes to parse() and returns its result', async () => {
    const parsed = { points: [{ lat: 1, lon: 2, elevation: 3, distance: 0 }], summary: { total_distance: 0 }, warnings: ['gap'] };
    fakeRuntime.responses.parse = parsed;
    const bytes = new Uint8Array([1, 2, 3]);

    assert.deepEqual(await parseGpx(bytes), parsed);
    assert.deepEqual(callsTo('parse'), [{ name: 'parse', args: [bytes] }]);
  });

  it('resolveAnchorCandidates asks nearest_candidates() for the clicked spot', async () => {
    fakeRuntime.responses.nearest_candidates = [{ lat: 1, lon: 2, distance_from_start: 300 }];

    assert.deepEqual(await resolveAnchorCandidates(45.1, 7.2), [{ lat: 1, lon: 2, distance_from_start: 300 }]);
    assert.deepEqual(callsTo('nearest_candidates')[0].args, [45.1, 7.2]);
  });

  it('resolvePhotoAnchors sends the readings alone, with no activity window', async () => {
    const readings = [{ lat: 1, lon: 2, timestamp: '2026-06-01T08:00:00.000Z' }];
    fakeRuntime.responses.resolve_photos = [{ status: 'ok' }];

    assert.deepEqual(await resolvePhotoAnchors(readings), [{ status: 'ok' }]);
    assert.deepEqual(callsTo('resolve_photos')[0].args, [readings]);
  });

  it('leaves no proxy alive after a call, even one that throws', async () => {
    fakeRuntime.responses.parse = { points: [], summary: {}, warnings: [] };
    await parseGpx(new Uint8Array([1]));
    assert.equal(fakeRuntime.live.size, 0);

    fakeRuntime.responses.resolve_photos = new Error('Traceback\nValueError: boom');
    await assert.rejects(resolvePhotoAnchors([{ lat: 1, lon: 2, timestamp: 'x' }]), /boom/);
    assert.equal(fakeRuntime.live.size, 0);
  });

  it("turns Python's InputError into a message for the user", async () => {
    fakeRuntime.responses.parse = new Error('Traceback (most recent call last):\nInputError: Could not parse this file as GPX.');

    await assert.rejects(parseGpx(new Uint8Array([1])), (error) => {
      assert.equal(error.isInputError, true);
      assert.equal(error.message, 'Could not parse this file as GPX.');
      return true;
    });
  });
});

describe('convert', () => {
  const VALHALLA_HOST = 'valhalla1.openstreetmap.de';
  const convertArgs = {
    startIso: '2026-06-01T08:00:00.000Z',
    durationSeconds: 3600,
    sport: 'hiking',
    anchors: [],
  };
  let valhallaRequests;
  let originalFetch;
  let originalWarn;

  function stubValhalla(respond) {
    globalThis.fetch = async (url, init) => {
      if (String(url).includes(VALHALLA_HOST)) {
        valhallaRequests.push(init);
        return respond(init);
      }
      return { ok: true, text: async () => '', json: async () => ({}) };
    };
  }

  function convertRequest() {
    const [call] = callsTo('convert');
    assert.ok(call, 'convert() never called bridge_api.convert');
    return call.args[0];
  }

  beforeEach(() => {
    valhallaRequests = [];
    originalFetch = globalThis.fetch;
    originalWarn = console.warn;
    console.warn = () => {};
    globalThis.loadPyodide = async () => fakeRuntime;
    fakeRuntime.reset();
    fakeRuntime.responses.convert = CONVERTED;
    fakeRuntime.responses.trace_payload = { shape: [{ lat: 1, lon: 2 }], costing: 'pedestrian' };
    fakeRuntime.responses.surface_multipliers = { multipliers: [0.9, 0.8], warnings: [] };
    stubValhalla(() => ({ ok: true, json: async () => ({ edges: ['answer'] }) }));
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    console.warn = originalWarn;
    delete globalThis.loadPyodide;
  });

  it('returns the FIT bytes and profile bridge_api made', async () => {
    const result = await convert(convertArgs);
    assert.equal(result.fitBytes, CONVERTED.fitBytes);
    assert.equal(result.profile, CONVERTED.profile);
    assert.equal(fakeRuntime.live.size, 0);
  });

  it('sends the request in the shape bridge_api.convert reads, the sport as the GUI names it', async () => {
    const anchors = [{ distanceFromStart: 500, timestamp: '2026-06-01T08:30:00.000Z', source: 'user' }];
    const stops = [{ distanceFromStart: 700, durationSeconds: 120 }];

    await convert({ ...convertArgs, anchors, stops, surfaceLookup: false });

    assert.deepEqual(convertRequest(), {
      startIso: convertArgs.startIso,
      durationSeconds: 3600,
      sport: 'hiking',
      anchors,
      stops,
      device: null,
      surfaceMultipliers: null,
      smoothness: 5,
    });
  });

  it('forwards the smoothness level and the device', async () => {
    const device = { name: 'Garmin Forerunner 965', manufacturer: 1, product: 4315, serialNumber: 42 };
    await convert({ ...convertArgs, smoothness: 8, device, surfaceLookup: false });
    assert.equal(convertRequest().smoothness, 8);
    assert.deepEqual(convertRequest().device, device);
  });

  it("asks Valhalla with bridge_api's payload, and paces with the multipliers from its answer", async () => {
    await convert({ ...convertArgs, sport: 'running' });

    assert.equal(valhallaRequests.length, 1);
    assert.deepEqual(JSON.parse(valhallaRequests[0].body), fakeRuntime.responses.trace_payload);
    assert.deepEqual(callsTo('surface_multipliers')[0].args, [{ edges: ['answer'] }, 'running']);
    assert.deepEqual(convertRequest().surfaceMultipliers, [0.9, 0.8]);
  });

  it('sends nothing to Valhalla when surfaceLookup is false', async () => {
    await convert({ ...convertArgs, surfaceLookup: false });
    assert.equal(valhallaRequests.length, 0);
    assert.equal(callsTo('trace_payload').length, 0);
    assert.deepEqual(convertRequest().surfaceMultipliers, null);
  });

  it('puts the surface warnings ahead of the pacing ones', async () => {
    fakeRuntime.responses.surface_multipliers = { multipliers: [1], warnings: ['surface'] };
    fakeRuntime.responses.convert = { ...CONVERTED, warnings: ['pacing'] };

    const { warnings } = await convert(convertArgs);

    assert.deepEqual(warnings, ['surface', 'pacing']);
  });

  it("still converts when Valhalla rejects the route, and says why in Valhalla's words", async () => {
    stubValhalla(() => ({
      ok: false,
      status: 400,
      statusText: 'Bad Request',
      json: async () => ({ error_code: 154, error: 'Path distance exceeds the max distance limit' }),
    }));

    const { warnings } = await convert(convertArgs);

    assert.equal(warnings.length, 1);
    assert.match(warnings[0], /Path distance exceeds the max distance limit/);
    assert.match(warnings[0], /400/);
    assert.equal(convertRequest().surfaceMultipliers, null);
  });

  it('reports an HTTP error whose body is not JSON by its status', async () => {
    stubValhalla(() => ({
      ok: false,
      status: 503,
      statusText: 'Service Unavailable',
      json: async () => { throw new SyntaxError('Unexpected token <'); },
    }));

    const { warnings } = await convert(convertArgs);

    assert.match(warnings[0], /503 Service Unavailable/);
  });

  it('reports a network failure', async () => {
    stubValhalla(() => { throw new TypeError('Failed to fetch'); });

    const { warnings } = await convert(convertArgs);

    assert.equal(warnings.length, 1);
    assert.match(warnings[0], /couldn't reach/);
  });

  it("still converts when Python can't use Valhalla's answer", async () => {
    fakeRuntime.responses.surface_multipliers = new Error('Traceback\nKeyError: matched_points');

    const { warnings } = await convert(convertArgs);

    assert.deepEqual(warnings, [describeSurfaceFailure(new Error('x'))]);
    assert.equal(convertRequest().surfaceMultipliers, null);
    assert.equal(fakeRuntime.live.size, 0);
  });

  it('has no surface warning when the lookup is switched off', async () => {
    stubValhalla(() => { throw new TypeError('Failed to fetch'); });

    const { warnings } = await convert({ ...convertArgs, surfaceLookup: false });

    assert.deepEqual(warnings, []);
  });
});
