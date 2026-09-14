import assert from 'node:assert/strict';
import { afterEach, describe, it, mock } from 'node:test';

import { formatGapMeters, readPhotoMetadata } from '../../src/gpx2fit/gui/js/photoAnchors.js';

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
