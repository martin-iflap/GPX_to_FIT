import assert from 'node:assert/strict';
import { afterEach, describe, it, mock } from 'node:test';

import './testUtils/domSetup.js';

import {
  formatGapMeters,
  initPhotoDrop,
  isLikelyPhotoFile,
  readPhotoMetadata,
  resetPhotoDrop,
} from '../../src/gpx2fit/gui/js/photoAnchors.js';

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

  it('returns null when the tags carry neither a position nor a time', async () => {
    stubExifr({ Make: 'Google' });
    assert.equal(await readPhotoMetadata(fakeFile()), null);
  });

  // Android's Photo Picker (and email, Bluetooth, Quick Share) redact the GPS
  // tags but keep the capture time; that photo can still be placed by hand,
  // so its time must survive instead of the whole reading becoming null.
  it('keeps the time when GPS was stripped', async () => {
    const timestamp = new Date(2024, 5, 1, 12, 0);
    stubExifr({ DateTimeOriginal: timestamp });
    assert.deepEqual(await readPhotoMetadata(fakeFile()), { lat: null, lon: null, timestamp });
  });

  it('treats lat/lon resolved to null (unresolved GPS IFD) as no position, keeping the time', async () => {
    const timestamp = new Date(2024, 5, 1, 12, 0);
    stubExifr({ latitude: null, longitude: null, DateTimeOriginal: timestamp });
    assert.deepEqual(await readPhotoMetadata(fakeFile()), { lat: null, lon: null, timestamp });
  });

  it('treats half a position as no position', async () => {
    const timestamp = new Date(2024, 5, 1, 12, 0);
    stubExifr({ latitude: 5, longitude: null, DateTimeOriginal: timestamp });
    assert.deepEqual(await readPhotoMetadata(fakeFile()), { lat: null, lon: null, timestamp });
    stubExifr({ longitude: 5, DateTimeOriginal: timestamp });
    assert.deepEqual(await readPhotoMetadata(fakeFile()), { lat: null, lon: null, timestamp });
  });

  it('treats a NaN coordinate as no position, for both lat and lon', async () => {
    const timestamp = new Date(2024, 5, 1, 12, 0);
    stubExifr({ latitude: NaN, longitude: 5, DateTimeOriginal: timestamp });
    assert.deepEqual(await readPhotoMetadata(fakeFile()), { lat: null, lon: null, timestamp });
    stubExifr({ latitude: 5, longitude: NaN, DateTimeOriginal: timestamp });
    assert.deepEqual(await readPhotoMetadata(fakeFile()), { lat: null, lon: null, timestamp });
  });

  it('keeps the position when there is no timestamp field at all', async () => {
    stubExifr({ latitude: 1, longitude: 2 });
    assert.deepEqual(await readPhotoMetadata(fakeFile()), { lat: 1, lon: 2, timestamp: null });
  });

  it('treats an invalid Date as no time', async () => {
    stubExifr({ latitude: 1, longitude: 2, DateTimeOriginal: new Date(NaN) });
    assert.deepEqual(await readPhotoMetadata(fakeFile()), { lat: 1, lon: 2, timestamp: null });
  });

  // A string would reach `toISOString()` later and throw; even a parseable
  // one carries no time zone, so it isn't trusted either.
  it('treats a timestamp that isn\'t a Date (an unparsed string) as no time', async () => {
    stubExifr({ latitude: 1, longitude: 2, DateTimeOriginal: '2024:06:01 12:00:00' });
    assert.deepEqual(await readPhotoMetadata(fakeFile()), { lat: 1, lon: 2, timestamp: null });
    stubExifr({ latitude: 1, longitude: 2, DateTimeOriginal: '2024-06-01T12:00:00' });
    assert.deepEqual(await readPhotoMetadata(fakeFile()), { lat: 1, lon: 2, timestamp: null });
  });
});

describe('isLikelyPhotoFile', () => {
  function file(name, type) {
    return new File(['x'], name, { type });
  }

  it('accepts anything the browser labels as an image', () => {
    assert.equal(isLikelyPhotoFile(file('1000046.jpg', 'image/jpeg')), true);
    assert.equal(isLikelyPhotoFile(file('IMG_0001.HEIC', 'image/heic')), true);
    assert.equal(isLikelyPhotoFile(file('no-extension', 'image/png')), true);
  });

  // Android's file chooser (used to keep the GPS) and desktop Windows can
  // hand over a HEIC with no type at all, or as a generic binary.
  it('accepts a photo extension with an empty or generic binary type', () => {
    assert.equal(isLikelyPhotoFile(file('PXL_20240601_093015.heic', '')), true);
    assert.equal(isLikelyPhotoFile(file('IMG_1234.HEIF', '')), true);
    assert.equal(isLikelyPhotoFile(file('photo.jpg', 'application/octet-stream')), true);
    assert.equal(isLikelyPhotoFile(file('photo.JPEG', '')), true);
    assert.equal(isLikelyPhotoFile(file('raw.dng', '')), true);
  });

  it('rejects other files, typed or not', () => {
    assert.equal(isLikelyPhotoFile(file('route.gpx', 'application/gpx+xml')), false);
    assert.equal(isLikelyPhotoFile(file('route.gpx', '')), false);
    assert.equal(isLikelyPhotoFile(file('notes.txt', 'text/plain')), false);
    assert.equal(isLikelyPhotoFile(file('jpg', '')), false);
    assert.equal(isLikelyPhotoFile(file('clip.mp4', 'video/mp4')), false);
  });

  it('does not take a photo extension\'s word over an explicit non-image type', () => {
    assert.equal(isLikelyPhotoFile(file('fake.jpg', 'text/plain')), false);
  });
});

describe('initPhotoDrop', () => {
  const START = new Date(2024, 5, 1, 8, 0);
  const END = new Date(2024, 5, 1, 10, 0);
  const TAKEN = new Date(2024, 5, 1, 9, 0);

  const originalCreateObjectURL = URL.createObjectURL;
  const originalRevokeObjectURL = URL.revokeObjectURL;

  afterEach(() => {
    resetPhotoDrop();
    delete globalThis.exifr;
    URL.createObjectURL = originalCreateObjectURL;
    URL.revokeObjectURL = originalRevokeObjectURL;
    document.body.innerHTML = '';
  });

  async function settle() {
    for (let i = 0; i < 10; i += 1) {
      await new Promise((resolve) => setImmediate(resolve));
    }
  }

  function photoFile(name = 'trailhead.jpg', type = 'image/jpeg') {
    return new File(['x'], name, { type });
  }

  // Mounts the photo drop with mocked dependencies. `tags` is what exifr
  // reads from every file, or a function of the file. `result` is merged into
  // each route match. The fake resolver honours an activity window the way
  // photo_anchors.resolve_photo_anchors does: given one, a photo not strictly
  // inside it comes back "outside_activity_time" whatever its GPS match.
  function mountPhotoDrop({
    tags = { latitude: 45, longitude: 7, DateTimeOriginal: TAKEN },
    result = { status: 'ok' },
    startTimeResult = { isValid: true, resolvedDate: END },
    thumbnail,
    withGallery = true,
    resolvePhotoAnchors,
  } = {}) {
    globalThis.exifr = {
      parse: mock.fn(async (file) => (typeof tags === 'function' ? tags(file) : tags)),
      thumbnail: mock.fn(async () => thumbnail),
    };
    const dropzoneEl = document.createElement('div');
    const inputEl = document.createElement('input');
    inputEl.type = 'file';
    const galleryInputEl = document.createElement('input');
    galleryInputEl.type = 'file';
    const galleryLinkEl = document.createElement('button');
    const listEl = document.createElement('ul');
    const emptyStateEl = document.createElement('p');
    document.body.append(dropzoneEl, inputEl, galleryInputEl, galleryLinkEl, listEl, emptyStateEl);
    // A stand-in for anchors.js: ids from 1, and each anchor's onRemove hook
    // run when it's removed, as markerList does.
    const anchorHooks = new Map();
    let nextAnchorId = 1;
    const mocks = {
      addAnchor: mock.fn((anchor, hooks) => {
        const id = nextAnchorId++;
        anchorHooks.set(id, hooks);
        return id;
      }),
      removeAnchor: mock.fn((id) => {
        const hooks = anchorHooks.get(id);
        anchorHooks.delete(id);
        hooks?.onRemove?.();
      }),
      setStatus: mock.fn(),
      placePhoto: mock.fn(),
      cancelPhotoPlacement: mock.fn(),
      resolvePhotoAnchors: resolvePhotoAnchors ?? mock.fn(async (readings, activityWindow) => readings.map((reading) => {
        const time = new Date(reading.timestamp).getTime();
        const outside = activityWindow
          && !(Date.parse(activityWindow.startIso) < time && time < Date.parse(activityWindow.endIso));
        return {
          lat: 45, lon: 7, distanceFromStart: 0, timestamp: reading.timestamp, gapM: 3,
          ...result,
          ...(outside ? { status: 'outside_activity_time' } : {}),
        };
      })),
    };

    initPhotoDrop({
      dropzoneEl,
      inputEl,
      ...(withGallery ? { galleryInputEl, galleryLinkEl } : {}),
      listEl,
      emptyStateEl,
      isTrackReady: () => true,
      getStartTime: () => START,
      getStartTimeResult: () => startTimeResult,
      ...mocks,
    });

    async function drop(files = [photoFile()]) {
      const event = new Event('drop');
      Object.defineProperty(event, 'dataTransfer', { value: { files } });
      dropzoneEl.dispatchEvent(event);
      await settle();
    }

    async function choose(input, files = [photoFile()]) {
      Object.defineProperty(input, 'files', { value: files, configurable: true });
      input.dispatchEvent(new Event('change'));
      await settle();
    }

    // The anchor list's own × on an anchor: the anchor goes, and its hook
    // tells the photo row, without the row having asked.
    function deleteFromAnchorList(id) {
      const hooks = anchorHooks.get(id);
      anchorHooks.delete(id);
      hooks?.onRemove?.();
    }

    const rows = () => [...listEl.querySelectorAll('.anchor-row')];
    return { dropzoneEl, inputEl, galleryInputEl, galleryLinkEl, listEl, ...mocks, drop, choose, rows, deleteFromAnchorList };
  }

  // Drops one photo whose route match comes back as `result`, and returns the
  // row it produced plus the addAnchor and setStatus mocks. `taken` is the
  // photo's capture time, and `startTimeResult` the main start-time control's
  // value.
  async function dropPhotoResolvingTo(result, {
    taken = TAKEN,
    startTimeResult = { isValid: true, resolvedDate: END },
  } = {}) {
    const drop = mountPhotoDrop({
      tags: { latitude: 45, longitude: 7, DateTimeOriginal: taken },
      result,
      startTimeResult,
    });
    await drop.drop();
    return { row: drop.rows()[0], addAnchor: drop.addAnchor, setStatus: drop.setStatus };
  }

  function placeButton(row) {
    return row.querySelector('.photo-place-button');
  }

  describe('a photo with its time but no location', () => {
    async function dropTimeOnly() {
      const drop = mountPhotoDrop({ tags: { DateTimeOriginal: TAKEN } });
      await drop.drop([photoFile('1000046.jpg')]);
      return { ...drop, row: drop.rows()[0] };
    }

    it('says the location is missing and why, without claiming the time is missing too', async () => {
      const { row, addAnchor, resolvePhotoAnchors } = await dropTimeOnly();

      assert.match(row.textContent, /no location/i);
      assert.match(row.textContent, /phone/i);
      assert.doesNotMatch(row.textContent, /capture time/i);
      assert.equal(row.classList.contains('is-problem'), true);
      assert.equal(addAnchor.mock.callCount(), 0);
      assert.equal(resolvePhotoAnchors.mock.callCount(), 0);
    });

    it('offers a "Place on map" button that hands the photo\'s own time to placePhoto', async () => {
      const { row, placePhoto } = await dropTimeOnly();
      const button = placeButton(row);

      assert.ok(button, 'no Place on map button');
      assert.match(button.textContent, /place on map/i);
      button.click();

      assert.equal(placePhoto.mock.callCount(), 1);
      const [photo] = placePhoto.mock.calls[0].arguments;
      assert.equal(photo.timestamp.getTime(), TAKEN.getTime());
      assert.equal(typeof photo.onPlaced, 'function');
      assert.equal(typeof photo.onCancel, 'function');
    });

    it('hands its thumbnail to placePhoto for the place card, or null without one', async () => {
      URL.createObjectURL = mock.fn(() => 'blob:thumb');
      URL.revokeObjectURL = mock.fn();
      const withThumb = mountPhotoDrop({ tags: { DateTimeOriginal: TAKEN }, thumbnail: new Uint8Array([1]) });
      await withThumb.drop();
      placeButton(withThumb.rows()[0]).click();
      assert.equal(withThumb.placePhoto.mock.calls[0].arguments[0].thumbnailUrl, 'blob:thumb');

      resetPhotoDrop();
      document.body.innerHTML = '';
      const without = mountPhotoDrop({ tags: { DateTimeOriginal: TAKEN } });
      await without.drop();
      placeButton(without.rows()[0]).click();
      assert.equal(without.placePhoto.mock.calls[0].arguments[0].thumbnailUrl, null);
    });

    it('tells the user what to do while waiting for the tap, and the button turns into Cancel', async () => {
      const { row } = await dropTimeOnly();
      placeButton(row).click();

      assert.match(row.textContent, /tap the route/i);
      assert.match(placeButton(row).textContent, /cancel/i);
    });

    it('cancels through cancelPhotoPlacement when the button is pressed again', async () => {
      const { row, placePhoto, cancelPhotoPlacement } = await dropTimeOnly();
      placeButton(row).click();
      placeButton(row).click();

      assert.equal(placePhoto.mock.callCount(), 1);
      assert.equal(cancelPhotoPlacement.mock.callCount(), 1);
    });

    it('goes back to "Place on map" when the placement is cancelled', async () => {
      const { row, placePhoto } = await dropTimeOnly();
      placeButton(row).click();
      placePhoto.mock.calls[0].arguments[0].onCancel();

      assert.match(row.textContent, /no location/i);
      assert.doesNotMatch(row.textContent, /tap the route/i);
      assert.match(placeButton(row).textContent, /place on map/i);
      placeButton(row).click();
      assert.equal(placePhoto.mock.callCount(), 2);
    });

    // The photo most recently handed to placePhoto.
    function armedPhoto(placePhoto) {
      return placePhoto.mock.calls.at(-1).arguments[0];
    }

    // Picks the route point for the armed photo, as the place card does.
    function placeAt(placePhoto, position = { lat: 46, lon: 8, distanceFromStart: 2500 }) {
      armedPhoto(placePhoto).onPlaced(position);
    }

    it('adds a photo anchor at the tapped point with the photo\'s exact time once placed', async () => {
      const { row, placePhoto, addAnchor } = await dropTimeOnly();
      placeButton(row).click();
      placeAt(placePhoto);

      assert.equal(addAnchor.mock.callCount(), 1);
      const [anchor] = addAnchor.mock.calls[0].arguments;
      assert.deepEqual(
        { ...anchor, timestamp: anchor.timestamp.getTime() },
        { lat: 46, lon: 8, distanceFromStart: 2500, timestamp: TAKEN.getTime(), source: 'photo' },
      );
      assert.equal('offsetSeconds' in anchor, false);
      assert.match(row.textContent, /placed by hand/i);
      assert.equal(row.classList.contains('is-problem'), false);
      assert.equal(placeButton(row), null);
    });

    it('removes its anchor when the row\'s × is pressed', async () => {
      const { row, placePhoto, removeAnchor } = await dropTimeOnly();
      placeButton(row).click();
      placeAt(placePhoto);
      row.querySelector('.anchor-row-delete').click();

      assert.equal(removeAnchor.mock.callCount(), 1);
      assert.equal(removeAnchor.mock.calls[0].arguments[0], 1);
    });

    it('goes back to "Place on map" when its anchor is deleted from the anchor list, and can be placed again', async () => {
      const { row, placePhoto, removeAnchor, deleteFromAnchorList } = await dropTimeOnly();
      placeButton(row).click();
      placeAt(placePhoto);
      deleteFromAnchorList(1);

      assert.match(row.textContent, /no location/i);
      assert.match(placeButton(row).textContent, /place on map/i);

      // Nothing left to remove: the row's × must not remove a dead anchor id.
      placeButton(row).click();
      assert.equal(placePhoto.mock.callCount(), 2);
      armedPhoto(placePhoto).onCancel();
      row.querySelector('.anchor-row-delete').click();
      assert.equal(removeAnchor.mock.callCount(), 0);
    });


    it('cancels a pending placement when its row is removed', async () => {
      const { row, cancelPhotoPlacement } = await dropTimeOnly();
      placeButton(row).click();
      row.querySelector('.anchor-row-delete').click();

      assert.equal(cancelPhotoPlacement.mock.callCount(), 1);
    });

    it('does not cancel anything when an unarmed row is removed', async () => {
      const { row, cancelPhotoPlacement, removeAnchor } = await dropTimeOnly();
      row.querySelector('.anchor-row-delete').click();

      assert.equal(cancelPhotoPlacement.mock.callCount(), 0);
      assert.equal(removeAnchor.mock.callCount(), 0);
    });

    it('cancels a pending placement on reset', async () => {
      const { row, cancelPhotoPlacement } = await dropTimeOnly();
      placeButton(row).click();
      resetPhotoDrop();

      assert.equal(cancelPhotoPlacement.mock.callCount(), 1);
    });
  });

  describe('a photo with no capture time', () => {
    it('says the time is missing, offers no placement, and is not matched', async () => {
      const drop = mountPhotoDrop({ tags: { latitude: 45, longitude: 7 } });
      await drop.drop();
      const [row] = drop.rows();

      assert.match(row.textContent, /no capture time/i);
      assert.doesNotMatch(row.textContent, /no location/i);
      assert.equal(row.classList.contains('is-problem'), true);
      assert.equal(placeButton(row), null);
      assert.equal(drop.resolvePhotoAnchors.mock.callCount(), 0);
    });

    it('says both are missing when the photo has neither', async () => {
      const drop = mountPhotoDrop({ tags: null });
      await drop.drop();
      const [row] = drop.rows();

      assert.match(row.textContent, /no location or capture time/i);
      assert.equal(placeButton(row), null);
    });
  });

  it('only sends photos with both a position and a time to the route matcher', async () => {
    const drop = mountPhotoDrop({
      tags: (file) => ({
        'full.jpg': { latitude: 45, longitude: 7, DateTimeOriginal: TAKEN },
        'time-only.jpg': { DateTimeOriginal: TAKEN },
        'gps-only.jpg': { latitude: 45, longitude: 7 },
      })[file.name],
    });
    await drop.drop([photoFile('time-only.jpg'), photoFile('full.jpg'), photoFile('gps-only.jpg')]);

    assert.equal(drop.resolvePhotoAnchors.mock.callCount(), 1);
    const [readings] = drop.resolvePhotoAnchors.mock.calls[0].arguments;
    assert.equal(readings.length, 1);
    assert.equal(readings[0].lat, 45);
    assert.equal(drop.addAnchor.mock.callCount(), 1);
  });

  it('reports a metadata read that throws (e.g. NotReadableError) on its row', async () => {
    const drop = mountPhotoDrop({ tags: () => { throw new Error('NotReadableError'); } });
    const consoleError = mock.method(console, 'error', () => {});
    await drop.drop();
    consoleError.mock.restore();

    assert.match(drop.rows()[0].textContent, /could not read metadata/i);
  });

  it('reads a HEIC handed over with no type instead of calling it "not a photo"', async () => {
    const drop = mountPhotoDrop();
    await drop.drop([photoFile('PXL_20240601.heic', '')]);

    assert.doesNotMatch(drop.rows()[0].textContent, /not a photo/i);
    assert.equal(drop.addAnchor.mock.callCount(), 1);
  });

  it('rejects a non-photo without reading it', async () => {
    const drop = mountPhotoDrop();
    await drop.drop([photoFile('route.gpx', '')]);

    assert.match(drop.rows()[0].textContent, /not a photo/i);
    assert.equal(globalThis.exifr.parse.mock.callCount(), 0);
  });

  describe('row label', () => {
    it('shows the capture time, since phone pickers rename photos to numbers', async () => {
      const drop = mountPhotoDrop({ tags: { latitude: 45, longitude: 7, DateTimeOriginal: new Date(2024, 5, 1, 9, 5, 7) } });
      await drop.drop([photoFile('1000046.jpg')]);
      const [row] = drop.rows();

      assert.equal(row.querySelector('.anchor-row-distance').textContent, 'Photo · 09:05:07');
      assert.match(row.title, /1000046\.jpg/);
    });

    it('falls back to the file name when there is no capture time', async () => {
      const drop = mountPhotoDrop({ tags: { latitude: 45, longitude: 7 } });
      await drop.drop([photoFile('summit.jpg')]);

      assert.equal(drop.rows()[0].querySelector('.anchor-row-distance').textContent, 'summit.jpg');
    });
  });

  describe('thumbnail', () => {
    function stubObjectUrls() {
      let next = 0;
      URL.createObjectURL = mock.fn(() => `blob:thumb-${next++}`);
      URL.revokeObjectURL = mock.fn();
    }

    it('shows the photo\'s embedded thumbnail', async () => {
      stubObjectUrls();
      const drop = mountPhotoDrop({ thumbnail: new Uint8Array([0xff, 0xd8]) });
      await drop.drop();

      const img = drop.rows()[0].querySelector('img.photo-thumb');
      assert.ok(img, 'no thumbnail');
      assert.equal(img.getAttribute('src'), 'blob:thumb-0');
    });

    it('shows none when the photo has no embedded thumbnail', async () => {
      stubObjectUrls();
      const drop = mountPhotoDrop({ thumbnail: undefined });
      await drop.drop();

      assert.equal(drop.rows()[0].querySelector('img.photo-thumb'), null);
      assert.equal(URL.createObjectURL.mock.callCount(), 0);
    });

    it('still reads the photo when the thumbnail read fails', async () => {
      stubObjectUrls();
      const drop = mountPhotoDrop();
      globalThis.exifr.thumbnail = mock.fn(async () => { throw new Error('no thumb'); });
      await drop.drop();

      assert.equal(drop.addAnchor.mock.callCount(), 1);
      assert.equal(drop.rows()[0].querySelector('img.photo-thumb'), null);
    });

    it('releases the thumbnail when its row is removed', async () => {
      stubObjectUrls();
      const drop = mountPhotoDrop({ thumbnail: new Uint8Array([1]) });
      await drop.drop();
      drop.rows()[0].querySelector('.anchor-row-delete').click();

      assert.deepEqual(URL.revokeObjectURL.mock.calls.map((call) => call.arguments[0]), ['blob:thumb-0']);
    });

    it('releases every thumbnail on reset', async () => {
      stubObjectUrls();
      const drop = mountPhotoDrop({ thumbnail: new Uint8Array([1]) });
      await drop.drop([photoFile('a.jpg'), photoFile('b.jpg')]);
      resetPhotoDrop();

      const revoked = URL.revokeObjectURL.mock.calls.map((call) => call.arguments[0]).sort();
      assert.deepEqual(revoked, ['blob:thumb-0', 'blob:thumb-1']);
      assert.equal(drop.rows().length, 0);
    });
  });

  describe('pickers', () => {
    it('reads photos chosen through the main file input', async () => {
      const drop = mountPhotoDrop();
      await drop.choose(drop.inputEl);

      assert.equal(drop.rows().length, 1);
      assert.equal(drop.addAnchor.mock.callCount(), 1);
    });

    it('reads photos chosen through the gallery input', async () => {
      const drop = mountPhotoDrop();
      await drop.choose(drop.galleryInputEl);

      assert.equal(drop.rows().length, 1);
      assert.equal(drop.addAnchor.mock.callCount(), 1);
    });

    it('opens the gallery input from its link, and the main input from the dropzone', () => {
      const drop = mountPhotoDrop();
      const galleryClick = mock.method(drop.galleryInputEl, 'click', () => {});
      const mainClick = mock.method(drop.inputEl, 'click', () => {});

      drop.galleryLinkEl.click();
      assert.equal(galleryClick.mock.callCount(), 1);
      assert.equal(mainClick.mock.callCount(), 0);

      drop.dropzoneEl.click();
      assert.equal(mainClick.mock.callCount(), 1);
      assert.equal(galleryClick.mock.callCount(), 1);
    });

    it('works without a gallery picker', async () => {
      const drop = mountPhotoDrop({ withGallery: false });
      await drop.drop();

      assert.equal(drop.rows().length, 1);
    });
  });

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

  // A photo outside the current start–end is still placed as an anchor:
  // anchors.js re-checks every anchor whenever the start or duration
  // changes, dimming this one now and using it once the window covers it.
  // Discarding it here would leave nothing for that re-check to bring back.
  it('adds an anchor for a photo taken before the current start, at its own time', async () => {
    const taken = new Date(2024, 5, 1, 7, 0);
    const { row, addAnchor } = await dropPhotoResolvingTo({ status: 'ok', distanceFromStart: 1234 }, { taken });

    assert.equal(addAnchor.mock.callCount(), 1);
    const [anchor] = addAnchor.mock.calls[0].arguments;
    assert.equal(anchor.distanceFromStart, 1234);
    assert.equal(anchor.timestamp.getTime(), taken.getTime());
    assert.equal(anchor.source, 'photo');
    assert.doesNotMatch(row.textContent, /ignored/i);
  });

  it('adds an anchor for a photo taken after the current end', async () => {
    const { addAnchor } = await dropPhotoResolvingTo({ status: 'ok' }, { taken: new Date(2024, 5, 1, 11, 0) });

    assert.equal(addAnchor.mock.callCount(), 1);
  });

  it('tells the user a photo outside the activity\'s time is kept but not used yet', async () => {
    const { setStatus } = await dropPhotoResolvingTo({ status: 'ok' }, { taken: new Date(2024, 5, 1, 7, 0) });

    const messages = setStatus.mock.calls.map((call) => call.arguments[0]);
    assert.ok(
      messages.some((message) => /outside the activity's time/i.test(message) && /not used/i.test(message)),
      `no status explained the photo outside the window; got ${JSON.stringify(messages)}`,
    );
  });

  it('says nothing about the activity\'s time for a photo inside it', async () => {
    const { setStatus } = await dropPhotoResolvingTo({ status: 'ok' });

    const messages = setStatus.mock.calls.map((call) => call.arguments[0]);
    assert.ok(messages.every((message) => !/outside the activity's time/i.test(message)));
  });

  it('accepts photos before the start time and duration are valid', async () => {
    const { addAnchor } = await dropPhotoResolvingTo({ status: 'ok' }, { startTimeResult: { isValid: false } });

    assert.equal(addAnchor.mock.callCount(), 1);
  });

  describe('a matched photo whose anchor is deleted from the anchor list', () => {
    async function dropAndDelete() {
      const drop = mountPhotoDrop({ result: { status: 'ok', distanceFromStart: 1234 } });
      await drop.drop();
      drop.deleteFromAnchorList(1);
      return { ...drop, row: drop.rows()[0] };
    }

    it('says so and offers it back', async () => {
      const { row } = await dropAndDelete();

      assert.match(row.textContent, /anchor removed/i);
      assert.equal(row.classList.contains('is-problem'), true);
      assert.match(placeButton(row).textContent, /re-add/i);
    });

    it('re-adds the same anchor, still linked to the row', async () => {
      const { row, addAnchor, removeAnchor } = await dropAndDelete();
      placeButton(row).click();

      assert.equal(addAnchor.mock.callCount(), 2);
      assert.deepEqual(addAnchor.mock.calls[1].arguments[0], addAnchor.mock.calls[0].arguments[0]);
      assert.match(row.textContent, /anchor added/i);
      assert.equal(placeButton(row), null);

      row.querySelector('.anchor-row-delete').click();
      assert.deepEqual(removeAnchor.mock.calls.map((call) => call.arguments[0]), [2]);
    });

    it('does not try to remove the deleted anchor again when its row goes', async () => {
      const { row, removeAnchor } = await dropAndDelete();
      row.querySelector('.anchor-row-delete').click();

      assert.equal(removeAnchor.mock.callCount(), 0);
    });
  });

  it('removing a photo\'s row removes its anchor without the row reacting to that', async () => {
    const drop = mountPhotoDrop();
    await drop.drop();
    const [row] = drop.rows();
    row.querySelector('.anchor-row-delete').click();

    assert.deepEqual(drop.removeAnchor.mock.calls.map((call) => call.arguments[0]), [1]);
    assert.equal(drop.rows().length, 0);
    assert.equal(placeButton(row), null);
  });

  describe('while the batch is being matched to the route', () => {
    // A resolver that waits until the test lets it answer.
    function heldResolver() {
      let release;
      const gate = new Promise((resolve) => {
        release = resolve;
      });
      const resolvePhotoAnchors = mock.fn(async (readings) => {
        await gate;
        return readings.map((reading) => ({
          status: 'ok', lat: 45, lon: 7, distanceFromStart: 100, timestamp: reading.timestamp, gapM: 1,
        }));
      });
      return { resolvePhotoAnchors, release: () => release() };
    }

    it('does not even match photos whose rows a reset removed while they were being read', async () => {
      let releaseRead;
      const reading = new Promise((resolve) => {
        releaseRead = resolve;
      });
      const drop = mountPhotoDrop({
        tags: () => reading.then(() => ({ latitude: 45, longitude: 7, DateTimeOriginal: TAKEN })),
      });
      await drop.drop();
      resetPhotoDrop();
      releaseRead();
      await settle();

      assert.equal(drop.resolvePhotoAnchors.mock.callCount(), 0);
      assert.equal(drop.addAnchor.mock.callCount(), 0);
    });

    it('adds nothing once a new route has reset the photos', async () => {
      const held = heldResolver();
      const drop = mountPhotoDrop({ resolvePhotoAnchors: held.resolvePhotoAnchors });
      await drop.drop();
      assert.equal(held.resolvePhotoAnchors.mock.callCount(), 1);

      resetPhotoDrop();
      held.release();
      await settle();

      assert.equal(drop.addAnchor.mock.callCount(), 0);
      assert.equal(drop.setStatus.mock.callCount(), 0);
    });

    it('adds no anchor for a photo whose row was removed meanwhile, but still does for the others', async () => {
      const held = heldResolver();
      const drop = mountPhotoDrop({ resolvePhotoAnchors: held.resolvePhotoAnchors });
      await drop.drop([photoFile('a.jpg'), photoFile('b.jpg')]);
      drop.rows()[0].querySelector('.anchor-row-delete').click();

      held.release();
      await settle();

      assert.equal(drop.addAnchor.mock.callCount(), 1);
      assert.equal(drop.removeAnchor.mock.callCount(), 0);
      assert.match(drop.rows()[0].textContent, /anchor added/i);
    });
  });
});
