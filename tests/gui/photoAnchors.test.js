import assert from 'node:assert/strict';
import { afterEach, describe, it, mock } from 'node:test';

import './testUtils/domSetup.js';

import { formatGapMeters, initPhotoDrop, readPhotoMetadata } from '../../src/gpx2fit/gui/js/photoAnchors.js';

describe('formatGapMeters', () => {
  it('formats sub-kilometer gaps in whole meters', () => {
    assert.equal(formatGapMeters(250), '250 m');
    assert.equal(formatGapMeters(999), '999 m');
  });

  it('switches to one-decimal km at exactly 1000m', () => {
    assert.equal(formatGapMeters(1000), '1.0 km');
  });

  it('formats larger gaps in km', () => {
    assert.equal(formatGapMeters(2500), '2.5 km');
  });
});

describe('readPhotoMetadata', () => {
  afterEach(() => {
    delete globalThis.exifr;
  });

  function fakeFile() {
    return {};
  }

  function stubExifr(resolvedTags) {
    globalThis.exifr = { parse: mock.fn(() => Promise.resolve(resolvedTags)) };
  }

  it('extracts lat/lon/timestamp when EXIF data is valid', async () => {
    const timestamp = new Date(2024, 5, 1, 12, 0);
    stubExifr({ latitude: 12.5, longitude: 45.5, DateTimeOriginal: timestamp });
    const result = await readPhotoMetadata(fakeFile());
    assert.deepEqual(result, { lat: 12.5, lon: 45.5, timestamp });
  });

  it('falls back through CreateDate then ModifyDate when DateTimeOriginal is missing', async () => {
    const timestamp = new Date(2024, 5, 1, 12, 0);
    stubExifr({ latitude: 1, longitude: 2, ModifyDate: timestamp });
    const result = await readPhotoMetadata(fakeFile());
    assert.equal(result.timestamp, timestamp);
  });

  it('returns null when exifr resolves null tags entirely', async () => {
    stubExifr(null);
    assert.equal(await readPhotoMetadata(fakeFile()), null);
  });

  it('returns null when GPS is present but lat/lon resolved to null (unresolved GPS IFD)', async () => {
    stubExifr({ latitude: null, longitude: null, DateTimeOriginal: new Date() });
    assert.equal(await readPhotoMetadata(fakeFile()), null);
  });

  it('returns null when lat/lon resolved to NaN', async () => {
    stubExifr({ latitude: NaN, longitude: 5, DateTimeOriginal: new Date() });
    assert.equal(await readPhotoMetadata(fakeFile()), null);
  });

  it('returns null when no timestamp field is present at all', async () => {
    stubExifr({ latitude: 1, longitude: 2 });
    assert.equal(await readPhotoMetadata(fakeFile()), null);
  });

  it('returns null when the timestamp is an invalid Date', async () => {
    stubExifr({ latitude: 1, longitude: 2, DateTimeOriginal: new Date(NaN) });
    assert.equal(await readPhotoMetadata(fakeFile()), null);
  });
});

describe('initPhotoDrop', () => {
  const START = new Date(2024, 5, 1, 8, 0);
  const END = new Date(2024, 5, 1, 10, 0);
  const TAKEN = new Date(2024, 5, 1, 9, 0);

  afterEach(() => {
    delete globalThis.exifr;
    document.body.innerHTML = '';
  });

  async function settle() {
    for (let i = 0; i < 10; i += 1) {
      await new Promise((resolve) => setImmediate(resolve));
    }
  }

  // Drops one photo whose route match comes back as `result`, and returns the
  // row it produced plus the addAnchor mock.
  async function dropPhotoResolvingTo(result) {
    globalThis.exifr = { parse: mock.fn(() => Promise.resolve({ latitude: 45, longitude: 7, DateTimeOriginal: TAKEN })) };
    const dropzoneEl = document.createElement('div');
    const inputEl = document.createElement('input');
    const listEl = document.createElement('ul');
    const emptyStateEl = document.createElement('p');
    document.body.append(dropzoneEl, inputEl, listEl, emptyStateEl);
    const addAnchor = mock.fn(() => 1);

    initPhotoDrop({
      dropzoneEl,
      inputEl,
      listEl,
      emptyStateEl,
      isTrackReady: () => true,
      getStartTime: () => START,
      getStartTimeResult: () => ({ isValid: true, resolvedDate: END }),
      resolvePhotoAnchors: async () => [{ lat: 45, lon: 7, distanceFromStart: 0, timestamp: TAKEN.toISOString(), gapM: 3, ...result }],
      addAnchor,
      removeAnchor: mock.fn(),
      setStatus: mock.fn(),
    });

    const drop = new Event('drop');
    Object.defineProperty(drop, 'dataTransfer', {
      value: { files: [new File(['x'], 'trailhead.jpg', { type: 'image/jpeg' })] },
    });
    dropzoneEl.dispatchEvent(drop);
    await settle();

    return { row: listEl.querySelector('.anchor-row'), addAnchor };
  }

  it('explains a photo at the route\'s start/finish instead of adding an anchor', async () => {
    const { row, addAnchor } = await dropPhotoResolvingTo({ status: 'at_route_end' });

    assert.equal(addAnchor.mock.callCount(), 0);
    assert.equal(row.classList.contains('is-problem'), true);
    assert.match(row.textContent, /start\/finish/i);
    assert.doesNotMatch(row.textContent, /too far/i);
  });

  it('adds an anchor for an ok match, at the matched distance and the photo\'s own time', async () => {
    const { row, addAnchor } = await dropPhotoResolvingTo({ status: 'ok', distanceFromStart: 1234 });

    assert.equal(addAnchor.mock.callCount(), 1);
    const [anchor] = addAnchor.mock.calls[0].arguments;
    assert.equal(anchor.distanceFromStart, 1234);
    assert.equal(anchor.timestamp.getTime(), TAKEN.getTime());
    assert.equal(anchor.source, 'photo');
    assert.equal(row.classList.contains('is-problem'), false);
  });

  it('still reports a too-far match with its gap', async () => {
    const { row, addAnchor } = await dropPhotoResolvingTo({ status: 'too_far', gapM: 2500 });

    assert.equal(addAnchor.mock.callCount(), 0);
    assert.match(row.textContent, /Too far from route \(2\.5 km away\)/);
  });
});
