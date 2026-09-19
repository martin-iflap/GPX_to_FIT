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
// network without a real WASM runtime.
function fakePyodideRuntime() {
  const globals = new Map();
  return {
    loadPackage: async () => {},
    runPythonAsync: async () => {},
    globals: {
      set: (name, value) => globals.set(name, value),
      get: () => ({ toJs: () => [] }),
    },
  };
}

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
    globalThis.loadPyodide = async () => fakePyodideRuntime();
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
