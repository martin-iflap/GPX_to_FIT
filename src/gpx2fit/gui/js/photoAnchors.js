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
// Every photo anchor, GPS-matched or placed by hand, is added here, with an
// `onRemove` hook: deleting it from the anchor list puts its row back to
// "Place on map" (or "Re-add"), so a row never claims an anchor that's gone.

/* global exifr */
// exifr is loaded globally via the <script> tag in index.html (no bundler),
// same pattern as Leaflet/Pyodide — this directive tells the IDE/linter it's
// an intentional external global, not a typo.

import { isStrictlyInside } from './activityWindow.js';
import { describeError, formatClockSeconds } from './format.js';

// Extensions a photo can carry when the browser doesn't give it an image
// type: Android's file chooser and Windows both send HEIC with an empty type.
const PHOTO_EXTENSIONS = new Set(['jpg', 'jpeg', 'jpe', 'heic', 'heif', 'png', 'tif', 'tiff', 'webp', 'dng', 'avif']);
// Types that say "unknown" rather than "not an image".
const UNTYPED = new Set(['', 'application/octet-stream']);

const NO_LOCATION_TEXT = 'No location. Phones often remove it when sharing a photo; '
  + 'place it on the map instead, at the photo\'s own time.';
const PLACING_TEXT = 'Tap the route where this photo was taken.';

let listEl = null;
let emptyStateEl = null;
let fileCount = 0;
// anchors.js's addAnchor/removeAnchor and anchorPopovers' placePhoto/
// cancelPhotoPlacement, as handed to initPhotoDrop.
let deps = null;
// Every live row's cleanup (thumbnail release, pending placement), run on
// reset as well as by the row's own ×.
const rowCleanups = new Set();

function setEmptyState() {
  if (emptyStateEl) {
    emptyStateEl.hidden = fileCount > 0;
  }
}

/**
 * Clears every dropped-file row (e.g. before loading a new route) — the
 * anchors those rows created are removed separately, by anchors.js's own
 * reset, since this module never tracks anchor ids beyond a single row's
 * delete button.
 */
export function resetPhotoDrop() {
  rowCleanups.forEach((cleanup) => cleanup());
  rowCleanups.clear();
  if (listEl) {
    listEl.innerHTML = '';
  }
  fileCount = 0;
  setEmptyState();
}

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
 * Adds a status row for a dropped file. Returns its controls: `setStatus`
 * updates the row's status text/class as processing progresses,
 * `setAnchorId` records the id of the anchor this row's photo became (null
 * once it's gone) so the row's delete button can remove that anchor too, not
 * just its own row. `setAction` puts one button in the row (or removes it,
 * given null), `setOnRemove` runs when the row goes (× or reset), and
 * `isRemoved` says whether it has.
 */
function addRow(fileName) {
  fileCount += 1;
  setEmptyState();

  const row = document.createElement('li');
  row.className = 'anchor-row photo-row';
  row.title = fileName;

  const info = document.createElement('div');
  info.className = 'anchor-row-info';

  const textEl = document.createElement('div');
  textEl.className = 'anchor-row-text';

  const nameEl = document.createElement('span');
  nameEl.className = 'anchor-row-distance';
  nameEl.textContent = fileName;

  const statusEl = document.createElement('span');
  statusEl.className = 'anchor-row-time';
  statusEl.textContent = 'Reading…';

  textEl.append(nameEl, statusEl);
  info.append(textEl);

  let anchorId = null;
  let thumbnailUrl = null;
  let actionEl = null;
  let onRemove = () => {};
  let removed = false;

  function cleanup() {
    removed = true;
    onRemove();
    if (thumbnailUrl) {
      URL.revokeObjectURL(thumbnailUrl);
      thumbnailUrl = null;
    }
  }

  const deleteBtn = document.createElement('button');
  deleteBtn.type = 'button';
  deleteBtn.className = 'anchor-row-delete';
  deleteBtn.setAttribute('aria-label', 'Remove');
  deleteBtn.textContent = '×';
  deleteBtn.addEventListener('click', () => {
    // Cleanup first: removing the anchor calls back into this row (see
    // linkAnchor), which must already know it's going.
    cleanup();
    if (anchorId !== null) {
      deps.removeAnchor(anchorId);
    }
    rowCleanups.delete(cleanup);
    row.remove();
    fileCount -= 1;
    setEmptyState();
  });
  rowCleanups.add(cleanup);

  row.append(info, deleteBtn);
  listEl.append(row);

  return {
    setStatus: (text, isProblem = false) => {
      statusEl.textContent = text;
      row.classList.toggle('is-problem', isProblem);
    },
    setAnchorId: (id) => {
      anchorId = id;
    },
    isRemoved: () => removed,
    thumbnailUrl: () => thumbnailUrl,
    setLabel: (text) => {
      nameEl.textContent = text;
    },
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
}

/** Formats a gap distance for display: meters below 1km, one-decimal km above. */
export function formatGapMeters(meters) {
  if (meters >= 1000) {
    return `${(meters / 1000).toFixed(1)} km`;
  }
  return `${Math.round(meters)} m`;
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
 * Adds the photo anchor a row stands for, and keeps the row in step with
 * it: when the anchor is removed from the anchor list, `onAnchorRemoved`
 * puts the row back into a state that can add it again. Not when the row
 * itself is going (its × removes the anchor too).
 */
function linkAnchor(row, anchor, onAnchorRemoved) {
  const anchorId = deps.addAnchor({ ...anchor, source: 'photo' }, {
    onRemove: () => {
      row.setAnchorId(null);
      if (!row.isRemoved()) {
        onAnchorRemoved();
      }
    },
  });
  row.setAnchorId(anchorId);
  row.setAction(null);
}

/**
 * Adds the anchor for a photo the route matcher placed by its GPS. If the
 * anchor is later deleted from the anchor list, the row offers it back.
 */
function addMatchedAnchor(row, anchor) {
  linkAnchor(row, anchor, () => {
    const button = rowButton('Re-add');
    button.addEventListener('click', () => addMatchedAnchor(row, anchor));
    row.setAction(button);
    row.setStatus('Anchor removed', true);
  });
  row.setStatus('Anchor added');
}

/**
 * Turns a time-only photo's row into its "Place on map" state: a button that
 * arms a placement (the next route tap places this photo, at its own time),
 * and turns into Cancel while armed. A placed photo whose anchor is deleted
 * from the anchor list comes back to this state.
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
      deps.cancelPhotoPlacement();
      return;
    }
    armed = true;
    button.textContent = 'Cancel';
    row.setStatus(PLACING_TEXT, true);
    deps.placePhoto({
      timestamp,
      thumbnailUrl: row.thumbnailUrl(),
      onPlaced: ({ lat, lon, distanceFromStart }) => {
        armed = false;
        linkAnchor(row, { lat, lon, distanceFromStart, timestamp }, showIdle);
        row.setStatus('Anchor added (placed by hand)');
      },
      onCancel: showIdle,
    });
  });

  row.setOnRemove(() => {
    if (armed) {
      armed = false;
      deps.cancelPhotoPlacement();
    }
  });
  showIdle();
}

/**
 * Reads EXIF metadata for one dropped file and reports the outcome on its
 * own status row. Returns the extracted `{ lat, lon, timestamp, row }`
 * for files with a position and a time, or `null` for anything that can't be
 * batched into a route-resolution call (never throws — failures are
 * reported on the row and swallowed here).
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
  if (metadata.lat === null) {
    offerPlacement(row, metadata.timestamp);
    return null;
  }

  return { ...metadata, row };
}

/**
 * Wires the photo dropzone: drag/drop and click-to-browse, both accepting
 * multiple files. Each file's EXIF metadata is read independently, then all
 * successfully-read readings are resolved against the route in a single
 * batched Python call. Every file is reported in its own row of
 * `listEl`/`emptyStateEl`.
 *
 * @param {object} opts
 * @param {HTMLElement} opts.dropzoneEl
 * @param {HTMLInputElement} opts.inputEl - the main picker (a file chooser, which keeps GPS)
 * @param {HTMLInputElement} [opts.galleryInputEl] - the phone's photo gallery picker, which strips GPS
 * @param {HTMLElement} [opts.galleryLinkEl] - opens `galleryInputEl`
 * @param {HTMLElement} opts.listEl - `<ul>` to render per-file status rows into
 * @param {HTMLElement} opts.emptyStateEl - shown while no files have been dropped
 * @param {() => boolean} opts.isTrackReady - must return true (a GPX is parsed) before drops are accepted
 * @param {() => Date|null} opts.getStartTime - current route start time, or null if unset/invalid
 * @param {() => {isValid: boolean, resolvedDate?: Date}} opts.getStartTimeResult -
 *   current value of the main start-time toggle; `resolvedDate` is the
 *   activity's end time regardless of whether it was entered directly or as
 *   a duration. Only used to tell the user when a dropped photo falls
 *   outside that window: the photo is added as an anchor either way, and
 *   anchors.js decides whether it's used.
 * @param {(photoReadings: {lat: number, lon: number, timestamp: string}[]) => Promise<{status: 'ok'|'too_far'|'at_route_end', lat: number, lon: number, distanceFromStart: number, timestamp: string, gapM: number}[]>} opts.resolvePhotoAnchors -
 *   pyodideBridge.resolvePhotoAnchors, called without an activity window
 * @param {(anchor: {lat: number, lon: number, distanceFromStart: number, timestamp: Date, source: string}, hooks: {onRemove: () => void}) => number} opts.addAnchor -
 *   anchors.js's addAnchor, reused as-is
 * @param {(id: number) => void} opts.removeAnchor - anchors.js's removeAnchor, reused as-is;
 *   called when a successfully-resolved photo's row is dismissed, so removing the row also
 *   removes the anchor it created
 * @param {(photo: {timestamp: Date, thumbnailUrl: string|null, onPlaced: (position: {lat: number, lon: number, distanceFromStart: number}) => void, onCancel: () => void}) => void} opts.placePhoto -
 *   anchorPopovers' placePhoto: the next route tap picks this photo's point,
 *   and `onPlaced` adds the anchor there
 * @param {() => void} opts.cancelPhotoPlacement - drops a pending placement (its `onCancel` runs)
 * @param {(message: string, kind?: 'error'|'input') => void} opts.setStatus
 */
export function initPhotoDrop({
  dropzoneEl,
  inputEl,
  galleryInputEl,
  galleryLinkEl,
  listEl: list,
  emptyStateEl: emptyState,
  isTrackReady,
  getStartTime,
  getStartTimeResult,
  resolvePhotoAnchors,
  addAnchor,
  removeAnchor,
  placePhoto,
  cancelPhotoPlacement,
  setStatus,
}) {
  listEl = list;
  emptyStateEl = emptyState;
  deps = { addAnchor, removeAnchor, placePhoto, cancelPhotoPlacement };
  setEmptyState();

  async function handleFiles(files) {
    if (!files || files.length === 0) {
      return;
    }
    if (!isTrackReady()) {
      setStatus('Upload a GPX route before adding photos.', 'input');
      return;
    }

    // A row removed while its photo was being read or matched (its ×, or a
    // reset when a new route loads) must not become an anchor: on a reset
    // the match would even be against the new route.
    const readings = (await Promise.all([...files].map((file) => readFile(file))))
      .filter((reading) => reading && !reading.row.isRemoved());
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

    const startTimeResult = getStartTimeResult();
    const start = getStartTime();
    const activityWindow = startTimeResult.isValid && start ? { start, end: startTimeResult.resolvedDate } : null;
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
      if (result.status !== 'ok') {
        reading.row.setStatus(`Too far from route (${formatGapMeters(result.gapM)} away)`, true);
        return;
      }
      addMatchedAnchor(reading.row, {
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

  function wireInput(input) {
    input.addEventListener('change', () => {
      void handleFiles(input.files);
      input.value = '';
    });
  }

  dropzoneEl.addEventListener('click', () => inputEl.click());
  dropzoneEl.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      inputEl.click();
    }
  });
  wireInput(inputEl);

  if (galleryInputEl) {
    wireInput(galleryInputEl);
    galleryLinkEl?.addEventListener('click', (event) => {
      // The link sits outside the dropzone, but a click must never reach it
      // and open the file chooser as well.
      event.stopPropagation();
      galleryInputEl.click();
    });
  }

  ['dragenter', 'dragover'].forEach((eventName) => {
    dropzoneEl.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzoneEl.classList.add('is-dragover');
    });
  });
  ['dragleave', 'drop'].forEach((eventName) => {
    dropzoneEl.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzoneEl.classList.remove('is-dragover');
    });
  });
  dropzoneEl.addEventListener('drop', (event) => {
    void handleFiles(event.dataTransfer.files);
  });
}
