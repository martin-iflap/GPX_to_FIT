// Photo dropzone: reads GPS + timestamp from dropped photos' EXIF metadata
// (via the globally-loaded `exifr` library) entirely client-side — this is
// the one step that has to stay in JS, since metadata-parsing libraries are
// JS-only. Everything else (matching each reading to the route, deciding
// whether the match is close enough to trust) runs in Python via
// pyodideBridge's `resolvePhotoAnchors`, so this module owns only the
// dropzone UI and per-file status list, not any geometry — mirrors
// stops.js's separation of concerns.
//
// Phones often hand over a photo with its GPS removed but its capture time
// intact: Android's Photo Picker redacts location for the browser, and so do
// email, Bluetooth and Quick Share. Such a photo gets a "Place on map" button
// instead of an error, and the user taps the route where it was taken
// (photoPlacement.js / anchorPopovers.js). The main file input therefore asks
// for the file chooser rather than the Photo Picker (see index.html), and the
// gallery input is the explicit fallback.
//
// A photo on a spot the route passes more than once (an out-and-back) gets a
// "Resolve" button: the passes are metres apart, so its GPS can't tell which
// one it was taken on, and the user picks (anchorPopovers' choosePhotoPass).
//
// The photo list only holds photos that still need the user: the ones
// waiting to be placed or resolved, and the ones that can't be used, with the
// reason. A
// photo that becomes an anchor, GPS-matched or placed by hand, leaves the
// list and is shown once, in the anchor list, with its thumbnail. A summary
// line counts those. Its anchor is added with an `onRemove` hook, so
// deleting it there drops the photo too.
//
// main.js creates the one photo drop (createPhotoDrop) and resets it when a
// new route loads.

/* global exifr */
// exifr is loaded globally via the <script> tag in index.html (no bundler),
// same pattern as Leaflet/Pyodide — this directive tells the IDE/linter it's
// an intentional external global, not a typo.

import { isStrictlyInside } from '../route/activityWindow.js';
import { wireDropzone, wireFileInput } from '../inputs/dropzone.js';
import { describeError, formatClockSeconds, formatGapMeters } from '../util/format.js';

/**
 * A dropped photo with both a position and a capture time, ready to be
 * matched to the route.
 * @typedef {{ lat: number, lon: number, timestamp: Date, row: ReturnType<typeof createPhotoRow> }} PhotoReading
 */

// Extensions a photo can carry when the browser doesn't give it an image
// type: Android's file chooser and Windows both send HEIC with an empty type.
const PHOTO_EXTENSIONS = new Set(['jpg', 'jpeg', 'jpe', 'heic', 'heif', 'png', 'tif', 'tiff', 'webp', 'dng', 'avif']);
// Types that say "unknown" rather than "not an image".
const UNTYPED = new Set(['', 'application/octet-stream']);

const NO_LOCATION_TEXT = 'No location. Phones often remove it when sharing a photo; '
  + 'place it on the map instead, at the photo\'s own time.';
const PLACING_TEXT = 'Tap the route where this photo was taken.';
const AMBIGUOUS_TEXT = 'The route passes here more than once (e.g. an out-and-back). '
  + 'Resolve it to pick which pass the photo was taken on.';

/**
 * Whether a file looks like a photo worth reading: anything the browser
 * types as an image, or an untyped file with a photo extension. An explicit
 * non-image type wins over the extension.
 * @param {File} file
 * @returns {boolean}
 */
export function isLikelyPhotoFile(file) {
  if (file.type.startsWith('image/')) {
    return true;
  }
  if (!UNTYPED.has(file.type)) {
    return false;
  }
  const dot = file.name.lastIndexOf('.');
  return dot > 0 && PHOTO_EXTENSIONS.has(file.name.slice(dot + 1).toLowerCase());
}

/**
 * Extracts `{ lat, lon, timestamp }` from a photo's EXIF data. Either half may
 * be missing (`lat`/`lon` both null, or `timestamp` null): a phone that
 * stripped the GPS usually kept the time, and that photo can still be placed
 * by hand. Returns `null` only when neither is there.
 * @param {File} file
 * @returns {Promise<{lat: number|null, lon: number|null, timestamp: Date|null} | null>}
 */
export async function readPhotoMetadata(file) {
  const tags = await exifr.parse(file, { gps: true, exif: true, tiff: true });
  if (!tags) {
    return null;
  }
  // exifr reports unresolvable GPS as `null` (e.g. a GPS IFD present but
  // missing GPSLatitudeRef, as some phones write when a fix failed) rather
  // than omitting the fields, so `=== undefined` alone doesn't catch it —
  // and a null/NaN coordinate reaching the backend crashes the whole batch
  // instead of just this photo's row.
  const hasPosition = typeof tags.latitude === 'number' && typeof tags.longitude === 'number'
    && !Number.isNaN(tags.latitude) && !Number.isNaN(tags.longitude);
  const rawTimestamp = tags.DateTimeOriginal ?? tags.CreateDate ?? tags.ModifyDate;
  const timestamp = rawTimestamp instanceof Date && !Number.isNaN(rawTimestamp.getTime()) ? rawTimestamp : null;
  if (!hasPosition && !timestamp) {
    return null;
  }
  return {
    lat: hasPosition ? tags.latitude : null,
    lon: hasPosition ? tags.longitude : null,
    timestamp,
  };
}

/**
 * Builds the status row for a dropped file, at the end of `listEl`. Returns
 * its controls:
 * - `setStatus`, `setLabel`, `setThumbnail` and `setAction` (one button, or
 *   null for none) update what the row shows.
 * - `becomeAnchor` takes the row out of the list once the photo is an
 *   anchor. `isAnchor` says whether it has.
 * - `anchorRemoved` is that anchor's `onRemove` hook: the photo goes with it,
 *   as it does with the row's own × (only there while the photo is listed,
 *   so never with an anchor to remove too). `release` only frees what the
 *   photo holds, for a reset. `setOnRemove` runs in all three, and
 *   `isRemoved` says whether any has happened.
 * - `key`/`setKey` identify the photo by capture time and place, to catch
 *   the same photo added twice.
 *
 * @param {string} fileName
 * @param {HTMLElement} listEl
 * @param {{ onRemoved: (row: object) => void, onBecameAnchor: () => void }} hooks -
 *   what the photo drop keeps count of: the photo gone (its × or its
 *   anchor's), and the photo now listed as an anchor instead
 */
function createPhotoRow(fileName, listEl, { onRemoved, onBecameAnchor }) {
  const row = document.createElement('li');
  row.className = 'list-row photo-row';
  row.title = fileName;

  const info = document.createElement('div');
  info.className = 'list-row-info';

  const textEl = document.createElement('div');
  textEl.className = 'list-row-text';

  const nameEl = document.createElement('span');
  nameEl.className = 'list-row-distance';

  const statusEl = document.createElement('span');
  statusEl.className = 'list-row-time';
  statusEl.textContent = 'Reading…';

  textEl.append(nameEl, statusEl);
  info.append(textEl);

  let isAnchor = false;
  /** @type {string|null} */
  let thumbnailUrl = null;
  /** @type {HTMLElement|null} */
  let actionEl = null;
  let onRemove = () => {};
  let removed = false;
  /** @type {string|null} */
  let key = null;

  function release() {
    removed = true;
    onRemove();
    if (thumbnailUrl) {
      URL.revokeObjectURL(thumbnailUrl);
      thumbnailUrl = null;
    }
  }

  function remove() {
    release();
    row.remove();
    onRemoved(controls);
  }

  const deleteBtn = document.createElement('button');
  deleteBtn.type = 'button';
  deleteBtn.className = 'list-row-delete';
  deleteBtn.textContent = '×';
  deleteBtn.addEventListener('click', remove);

  // The remove button names the photo as the row does, so a screen reader
  // can tell one × from the next.
  function setLabel(text) {
    nameEl.textContent = text;
    deleteBtn.setAttribute('aria-label', `Remove ${text}`);
  }
  setLabel(fileName);

  row.append(info, deleteBtn);
  listEl.append(row);

  const controls = {
    setStatus: (text, isProblem = false) => {
      statusEl.textContent = text;
      row.classList.toggle('is-problem', isProblem);
    },
    becomeAnchor: () => {
      isAnchor = true;
      actionEl?.remove();
      actionEl = null;
      row.remove();
      onBecameAnchor();
    },
    isAnchor: () => isAnchor,
    anchorRemoved: remove,
    release,
    key: () => key,
    setKey: (value) => {
      key = value;
    },
    isRemoved: () => removed,
    thumbnailUrl: () => thumbnailUrl,
    setLabel,
    setThumbnail: (url) => {
      if (removed) {
        URL.revokeObjectURL(url);
        return;
      }
      thumbnailUrl = url;
      const img = document.createElement('img');
      img.className = 'photo-thumb';
      img.alt = '';
      img.src = url;
      info.prepend(img);
    },
    setAction: (button) => {
      actionEl?.remove();
      actionEl = button;
      if (button) {
        textEl.append(button);
      }
    },
    setOnRemove: (callback) => {
      onRemove = callback;
    },
  };
  return controls;
}

/**
 * Shows the photo's embedded EXIF thumbnail on its row. Best effort: the
 * embedded JPEG is tiny (decoding the full photo per row would cost a phone
 * real memory) and readable even from a HEIC the browser can't display.
 */
async function showThumbnail(file, row) {
  let thumbnail;
  try {
    thumbnail = await exifr.thumbnail(file);
  } catch {
    return;
  }
  if (thumbnail) {
    row.setThumbnail(URL.createObjectURL(new Blob([thumbnail], { type: 'image/jpeg' })));
  }
}

function rowButton(text) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'photo-place-button';
  button.textContent = text;
  return button;
}

/**
 * Creates the photo drop: wires the dropzone for drag/drop and
 * click-to-browse, both accepting multiple files. Each file's EXIF metadata
 * is read independently, then all successfully-read readings are resolved
 * against the route in a single batched Python call. Every file gets a row in `listEl` until it becomes an
 * anchor, and then counts toward `summaryEl` instead.
 *
 * @param {object} opts
 * @param {HTMLElement} opts.dropzoneEl
 * @param {HTMLInputElement} opts.inputEl - the main picker (a file chooser, which keeps GPS)
 * @param {HTMLInputElement} [opts.galleryInputEl] - the phone's photo gallery picker, which strips GPS
 * @param {HTMLElement} [opts.galleryLinkEl] - opens `galleryInputEl`
 * @param {HTMLElement} opts.listEl - `<ul>` to render per-file status rows into
 * @param {HTMLElement} opts.emptyStateEl - shown while there are no photos
 * @param {HTMLElement|null} [opts.summaryEl] - says how many photos became
 *   anchors; hidden while none have
 * @param {{ route: object|null, activityWindow: () => ({start: Date, end: Date}|null) }} opts.appState -
 *   appState.js's: drops are only accepted once a route has parsed, and the
 *   activity window is only used to tell the user when a dropped photo falls
 *   outside it (the photo is added as an anchor either way, and anchors.js
 *   decides whether it's used)
 * @param {(photoReadings: {lat: number, lon: number, timestamp: string}[]) => Promise<{status: 'ok'|'too_far'|'at_route_end'|'ambiguous', lat: number, lon: number, distanceFromStart: number, timestamp: string, gapM: number, passes: {lat: number, lon: number, distance_from_start: number}[]}[]>} opts.resolvePhotoAnchors -
 *   pyodideBridge.resolvePhotoAnchors, called without an activity window
 * @param {(anchor: {lat: number, lon: number, distanceFromStart: number, timestamp: Date, source: 'photo', thumbnailUrl: string|null}, hooks: {onRemove: () => void}) => number} opts.addAnchor -
 *   the anchor list's addAnchor, reused as-is
 * @param {(photo: {timestamp: Date, thumbnailUrl: string|null, onPlaced: (position: {lat: number, lon: number, distanceFromStart: number}) => void, onCancel: () => void}) => void} opts.placePhoto -
 *   anchorPopovers' placePhoto: the next route tap picks this photo's point,
 *   and `onPlaced` adds the anchor there
 * @param {() => void} opts.cancelPhotoPlacement - drops a pending placement (its `onCancel` runs)
 * @param {(photo: {timestamp: Date, thumbnailUrl: string|null, passes: {lat: number, lon: number, distance_from_start: number}[], onPicked: (pass: {lat: number, lon: number, distance_from_start: number}) => void}) => void} opts.choosePhotoPass -
 *   anchorPopovers' choosePhotoPass: asks which of `passes` the photo was
 *   taken on, and `onPicked` adds the anchor there
 * @param {(message: string, kind?: import('../shell/status.js').StatusKind) => void} opts.setStatus
 * @returns {{ reset: () => void }} `reset` clears every photo (e.g. before
 *   loading a new route). The anchors they became are removed separately, by
 *   the anchor list's own reset, which runs no `onRemove` hooks.
 */
export function createPhotoDrop({
  dropzoneEl,
  inputEl,
  galleryInputEl,
  galleryLinkEl,
  listEl,
  emptyStateEl,
  summaryEl = null,
  appState,
  resolvePhotoAnchors,
  addAnchor,
  placePhoto,
  cancelPhotoPlacement,
  choosePhotoPass,
  setStatus,
}) {
  // Every photo still around: the ones listed, and the ones that became an
  // anchor (no longer listed, but they own its thumbnail).
  const livePhotos = new Set();

  function updateListState() {
    const added = [...livePhotos].filter((photo) => photo.isAnchor()).length;
    emptyStateEl.hidden = livePhotos.size > 0;
    if (summaryEl) {
      summaryEl.hidden = added === 0;
      summaryEl.textContent = added === 1
        ? '1 photo added as an anchor, listed under Anchor points.'
        : `${added} photos added as anchors, listed under Anchor points.`;
    }
  }

  function addRow(fileName) {
    const row = createPhotoRow(fileName, listEl, {
      onRemoved: (removedRow) => {
        livePhotos.delete(removedRow);
        updateListState();
      },
      onBecameAnchor: updateListState,
    });
    livePhotos.add(row);
    updateListState();
    return row;
  }

  /**
   * Adds the anchor a photo became, with its thumbnail, and moves the photo
   * out of the list: from now on the anchor list shows it, and deleting it
   * there drops the photo too. Does nothing for a photo removed meanwhile (its
   * ×, or a reset for a new route) while a card or picker was still open.
   */
  function addPhotoAnchor(row, anchor) {
    if (row.isRemoved()) {
      return;
    }
    addAnchor(
      { ...anchor, source: 'photo', thumbnailUrl: row.thumbnailUrl() },
      { onRemove: row.anchorRemoved },
    );
    row.becomeAnchor();
  }

  /**
   * Turns a time-only photo's row into its "Place on map" state: a button that
   * arms a placement (the next route tap places this photo, at its own time),
   * and turns into Cancel while armed.
   */
  function offerPlacement(row, timestamp) {
    let armed = false;
    const button = rowButton('Place on map');

    function showIdle() {
      armed = false;
      button.textContent = 'Place on map';
      row.setAction(button);
      row.setStatus(NO_LOCATION_TEXT, true);
    }

    button.addEventListener('click', () => {
      if (armed) {
        cancelPhotoPlacement();
        return;
      }
      armed = true;
      button.textContent = 'Cancel';
      row.setStatus(PLACING_TEXT, true);
      placePhoto({
        timestamp,
        thumbnailUrl: row.thumbnailUrl(),
        onPlaced: ({ lat, lon, distanceFromStart }) => {
          armed = false;
          addPhotoAnchor(row, { lat, lon, distanceFromStart, timestamp });
        },
        onCancel: showIdle,
      });
    });

    row.setOnRemove(() => {
      if (armed) {
        armed = false;
        cancelPhotoPlacement();
      }
    });
    showIdle();
  }

  /**
   * Turns the row of a photo on a spot the route passes more than once into a
   * "Resolve" button, which opens the "Which pass?" picker. Closing the picker
   * without a pick leaves the row as it was.
   */
  function offerPassChoice(row, timestamp, passes) {
    const button = rowButton('Resolve');
    button.addEventListener('click', () => {
      choosePhotoPass({
        timestamp,
        thumbnailUrl: row.thumbnailUrl(),
        passes,
        onPicked: ({ lat, lon, distance_from_start: distanceFromStart }) => {
          addPhotoAnchor(row, { lat, lon, distanceFromStart, timestamp });
        },
      });
    });
    row.setAction(button);
    row.setStatus(AMBIGUOUS_TEXT, true);
  }

  /**
   * Reads EXIF metadata for one dropped file and reports the outcome on its
   * own status row. Returns the extracted `{ lat, lon, timestamp, row }`
   * for files with a position and a time, or `null` for anything that can't be
   * batched into a route-resolution call (never throws — failures are
   * reported on the row and swallowed here).
   *
   * @param {File} file
   * @returns {Promise<PhotoReading|null>}
   */
  async function readFile(file) {
    const row = addRow(file.name);

    if (!isLikelyPhotoFile(file)) {
      row.setStatus('Not a photo', true);
      return null;
    }

    let metadata;
    try {
      [metadata] = await Promise.all([readPhotoMetadata(file), showThumbnail(file, row)]);
    } catch (error) {
      console.error(error);
      row.setStatus('Could not read metadata', true);
      return null;
    }

    if (metadata?.timestamp) {
      row.setLabel(`Photo · ${formatClockSeconds(metadata.timestamp)}`);
    }
    if (!metadata) {
      row.setStatus('No location or capture time in this photo', true);
      return null;
    }
    if (!metadata.timestamp) {
      row.setStatus('No capture time in this photo, so it can\'t be an anchor', true);
      return null;
    }
    // The same photo added twice (or a burst taken within one second) would
    // put two anchors on one point, which only fails later, at Convert.
    const key = `${metadata.timestamp.getTime()}|${metadata.lat}|${metadata.lon}`;
    if ([...livePhotos].some((photo) => photo.key() === key)) {
      const what = metadata.lat === null ? 'capture time' : 'capture time and place';
      row.setStatus(`Same ${what} as a photo you already added`, true);
      return null;
    }
    row.setKey(key);
    if (metadata.lat === null || metadata.lon === null) {
      offerPlacement(row, metadata.timestamp);
      return null;
    }

    return { lat: metadata.lat, lon: metadata.lon, timestamp: metadata.timestamp, row };
  }

  async function handleFiles(files) {
    if (!files || files.length === 0) {
      return;
    }
    if (!appState.route) {
      setStatus('Upload a GPX route before adding photos.', 'input');
      return;
    }

    // A row removed while its photo was being read or matched (its ×, or a
    // reset when a new route loads) must not become an anchor: on a reset
    // the match would even be against the new route.
    const readings = (await Promise.all([...files].map((file) => readFile(file))))
      .filter(
        /** @param {PhotoReading|null} reading @returns {reading is PhotoReading} */
        (reading) => reading !== null && !reading.row.isRemoved(),
      );
    if (readings.length === 0) {
      return;
    }

    let results;
    try {
      // Matched on GPS alone, without the activity window: a photo outside
      // the current start–end still becomes an anchor, which anchors.js
      // re-checks on every start/duration change (dimmed and left out of
      // convert() until the window covers it). Rejecting it here instead
      // would leave nothing for that re-check to bring back.
      results = await resolvePhotoAnchors(
        readings.map((r) => ({ lat: r.lat, lon: r.lon, timestamp: r.timestamp.toISOString() })),
      );
    } catch (error) {
      console.error(error);
      // This row is too narrow for a full internal error (traceback and
      // all) — only swap in the real message when it's an InputError, whose
      // text is already short and written for the user; anything else keeps
      // the generic label, with the detail left to the console for debugging.
      const described = describeError(error);
      const rowMessage = described.kind === 'input' ? described.message : 'Could not match to route';
      readings.forEach((r) => r.row.setStatus(rowMessage, true));
      return;
    }

    const activityWindow = appState.activityWindow();
    let outsideCount = 0;

    readings.forEach((reading, index) => {
      const result = results[index];
      // Removed while the batch was being matched.
      if (reading.row.isRemoved()) {
        return;
      }
      if (result.status === 'at_route_end') {
        reading.row.setStatus('At the route\'s start/finish — your start/end time already covers this point', true);
        return;
      }
      if (result.status === 'ambiguous') {
        offerPassChoice(reading.row, reading.timestamp, result.passes);
        return;
      }
      if (result.status !== 'ok') {
        reading.row.setStatus(`Too far from route (${formatGapMeters(result.gapM)} away)`, true);
        return;
      }
      addPhotoAnchor(reading.row, {
        lat: result.lat,
        lon: result.lon,
        distanceFromStart: result.distanceFromStart,
        timestamp: reading.timestamp,
      });
      if (!isStrictlyInside(reading.timestamp, activityWindow)) {
        outsideCount += 1;
      }
    });

    // Said once for the batch, not on each row: a row's text would go stale
    // as soon as the start or duration moved, while the anchor list's own
    // "outside the activity's time now" note follows the window.
    if (outsideCount > 0) {
      const photos = outsideCount === 1 ? '1 photo was' : `${outsideCount} photos were`;
      setStatus(
        `${photos} taken outside the activity's time. Added, but not used until the start time and duration cover them.`,
        'input',
      );
    }
  }

  // handleFiles reports its own errors in the rows and the status.
  const onFiles = (files) => void handleFiles(files);
  wireDropzone(dropzoneEl, inputEl, onFiles);

  if (galleryInputEl) {
    wireFileInput(galleryInputEl, onFiles);
    galleryLinkEl?.addEventListener('click', (event) => {
      // The link sits outside the dropzone, but a click must never reach it
      // and open the file chooser as well.
      event.stopPropagation();
      galleryInputEl.click();
    });
  }

  updateListState();

  return {
    reset() {
      livePhotos.forEach((photo) => photo.release());
      livePhotos.clear();
      listEl.replaceChildren();
      updateListState();
    },
  };
}
