import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it } from 'node:test';

import { classifyPyError, convert } from '../../src/gpx2fit/gui/js/pyodideBridge.js';

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

// A stand-in Pyodide runtime: Python never runs, every global read comes back
// as an empty value, so convert() can be exercised for what it does over the
// network and what it hands to Python, without a real WASM runtime. It records
// every global set and every script run. pyodideBridge.js caches the runtime
// after its first load, so all tests share this one instance.
function fakePyodideRuntime() {
  const globals = new Map();
  const scripts = [];
  return {
    globalsSet: globals,
    scripts,
    loadPackage: async () => {},
    runPythonAsync: async (source) => {
      scripts.push(source);
    },
    globals: {
      set: (name, value) => globals.set(name, value),
      get: () => ({ toJs: () => [] }),
    },
  };
}

const fakeRuntime = fakePyodideRuntime();

describe('convert surfaceLookup', () => {
  const VALHALLA_HOST = 'valhalla1.openstreetmap.de';
  const convertArgs = {
    startIso: '2026-06-01T08:00:00.000Z',
    durationSeconds: 3600,
    sportEnumName: 'HIKING',
    anchors: [],
  };
  let fetchedUrls;
  let originalFetch;

  beforeEach(() => {
    fetchedUrls = [];
    originalFetch = globalThis.fetch;
    globalThis.loadPyodide = async () => fakeRuntime;
    globalThis.fetch = async (url) => {
      fetchedUrls.push(String(url));
      return { ok: true, text: async () => '', json: async () => ({}) };
    };
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    delete globalThis.loadPyodide;
  });

  it('asks Valhalla for surfaces by default', async () => {
    await convert(convertArgs);
    assert.ok(fetchedUrls.some((url) => url.includes(VALHALLA_HOST)));
  });

  it('sends nothing to Valhalla when surfaceLookup is false', async () => {
    await convert({ ...convertArgs, surfaceLookup: false });
    assert.ok(!fetchedUrls.some((url) => url.includes(VALHALLA_HOST)));
  });
});

describe('convert smoothness', () => {
  const convertArgs = {
    startIso: '2026-06-01T08:00:00.000Z',
    durationSeconds: 3600,
    sportEnumName: 'HIKING',
    anchors: [],
    surfaceLookup: false,
  };
  let originalFetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    globalThis.loadPyodide = async () => fakeRuntime;
    globalThis.fetch = async () => ({ ok: true, text: async () => '', json: async () => ({}) });
    fakeRuntime.globalsSet.clear();
    fakeRuntime.scripts.length = 0;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    delete globalThis.loadPyodide;
  });

  // The script that actually paces the track: the one calling combine().
  function pacingScript() {
    const script = fakeRuntime.scripts.find((source) => /\bcombine\(/.test(source));
    assert.ok(script, 'convert() never ran a script calling combine()');
    return script;
  }

  it('hands the chosen level to Python', async () => {
    await convert({ ...convertArgs, smoothness: 8 });
    assert.equal(fakeRuntime.globalsSet.get('smoothness'), 8);
  });

  it('defaults to the automatic level when none is given', async () => {
    await convert(convertArgs);
    assert.equal(fakeRuntime.globalsSet.get('smoothness'), 5);
  });

  it('forwards the level into combine() rather than just setting it', async () => {
    await convert({ ...convertArgs, smoothness: 8 });
    const [combineCall] = pacingScript().match(/\bcombine\(.*$/m);
    assert.match(combineCall, /\bsmoothness\s*=\s*int\(smoothness\)/);
  });
});

describe('convert device', () => {
  const convertArgs = {
    startIso: '2026-06-01T08:00:00.000Z',
    durationSeconds: 3600,
    sportEnumName: 'HIKING',
    anchors: [],
    surfaceLookup: false,
  };
  let originalFetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    globalThis.loadPyodide = async () => fakeRuntime;
    globalThis.fetch = async () => ({ ok: true, text: async () => '', json: async () => ({}) });
    fakeRuntime.globalsSet.clear();
    fakeRuntime.scripts.length = 0;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    delete globalThis.loadPyodide;
  });

  it('hands every part of the device identity to Python', async () => {
    await convert({
      ...convertArgs,
      device: { name: 'Garmin Forerunner 965', manufacturer: 1, product: 4315, serialNumber: 42 },
    });
    assert.equal(fakeRuntime.globalsSet.get('device_name'), 'Garmin Forerunner 965');
    assert.equal(fakeRuntime.globalsSet.get('device_manufacturer'), 1);
    assert.equal(fakeRuntime.globalsSet.get('device_product'), 4315);
    assert.equal(fakeRuntime.globalsSet.get('device_serial'), 42);
  });

  it('sets every device global to null when no device is chosen', async () => {
    await convert(convertArgs);
    for (const name of ['device_name', 'device_manufacturer', 'device_product', 'device_serial']) {
      assert.equal(fakeRuntime.globalsSet.get(name), null, name);
    }
  });

  it('puts the IDs on the track that gets written, not on the shared parsed route', async () => {
    await convert({ ...convertArgs, device: { manufacturer: 1, product: 4315 } });
    const script = fakeRuntime.scripts.find((source) => /\bwrite_fit\(/.test(source));
    assert.match(script, /device_manufacturer=/);
    assert.doesNotMatch(script, /_track\.device\s*=/);
  });
});
