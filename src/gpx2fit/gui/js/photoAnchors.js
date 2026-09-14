// Photo dropzone: reads GPS + timestamp from dropped photos' EXIF metadata
// (via the globally-loaded `exifr` library) entirely client-side — this is
// the one step that has to stay in JS, since metadata-parsing libraries are
// JS-only. Everything else (matching each reading to the route, deciding
// whether the match is close enough to trust) runs in Python via
// pyodideBridge's `resolvePhotoAnchors`, so this module owns only the
// dropzone UI and per-file status list, not any geometry — mirrors
// stops.js's separation of concerns.

/* global exifr */
// exifr is loaded globally via the <script> tag in index.html (no bundler),
// same pattern as Leaflet/Pyodide — this directive tells the IDE/linter it's
// an intentional external global, not a typo.

import { describeError, formatDateTime } from './format.js';

let listEl = null;
let emptyStateEl = null;
let fileCount = 0;

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
  if (listEl) {
    listEl.innerHTML = '';
  }
  fileCount = 0;
  setEmptyState();
}

/**
 * Adds a status row for a dropped file. Returns `{ setStatus, setAnchorId }`:
 * `setStatus` updates the row's status text/class as processing progresses,
 * `setAnchorId` records the id of the anchor this row's photo resolved to
 * (once known) so the row's delete button can remove that anchor too, not
 * just its own row.
 */
function addRow(fileName, removeAnchor) {
  fileCount += 1;
  setEmptyState();

  const row = document.createElement('li');
  row.className = 'anchor-row';

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
  const deleteBtn = document.createElement('button');
  deleteBtn.type = 'button';
  deleteBtn.className = 'anchor-row-delete';
  deleteBtn.setAttribute('aria-label', 'Remove');
  deleteBtn.textContent = '×';
  deleteBtn.addEventListener('click', () => {
    if (anchorId !== null) {
      removeAnchor(anchorId);
    }
    row.remove();
    fileCount -= 1;
    setEmptyState();
  });

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
 * Extracts `{ lat, lon, timestamp }` from a photo's EXIF data, or `null` if
 * GPS and/or a capture timestamp are missing.
 * @param {File} file
 * @returns {Promise<{lat: number, lon: number, timestamp: Date} | null>}
 */
export async function readPhotoMetadata(file) {
  const tags = await exifr.parse(file, { gps: true, exif: true, tiff: true });
  // exifr reports unresolvable GPS as `null` (e.g. a GPS IFD present but
  // missing GPSLatitudeRef, as some phones write when a fix failed) rather
  // than omitting the fields, so `=== undefined` alone doesn't catch it —
  // and a null/NaN coordinate reaching the backend crashes the whole batch
  // instead of just this photo's row.
  if (!tags || typeof tags.latitude !== 'number' || typeof tags.longitude !== 'number'
    || Number.isNaN(tags.latitude) || Number.isNaN(tags.longitude)) {
    return null;
  }
  const timestamp = tags.DateTimeOriginal ?? tags.CreateDate ?? tags.ModifyDate;
  if (!(timestamp instanceof Date) || Number.isNaN(timestamp.getTime())) {
    return null;
  }
  return { lat: tags.latitude, lon: tags.longitude, timestamp };
}

/**
 * Reads EXIF metadata for one dropped file and reports the outcome on its
 * own status row. Returns the extracted `{ lat, lon, timestamp, setRowStatus, setAnchorId }`
 * for files with usable metadata, or `null` for anything that can't be
 * batched into a route-resolution call (never throws — failures are
 * reported on the row and swallowed here).
 */
async function readFile(file, removeAnchor) {
  const { setStatus: setRowStatus, setAnchorId } = addRow(file.name, removeAnchor);

  if (!file.type.startsWith('image/')) {
    setRowStatus('Not a photo', true);
    return null;
  }

  let metadata;
  try {
    metadata = await readPhotoMetadata(file);
  } catch (error) {
    console.error(error);
    setRowStatus('Could not read metadata', true);
    return null;
  }

  if (!metadata) {
    setRowStatus('No location/timestamp data', true);
    return null;
  }

  return { ...metadata, setRowStatus, setAnchorId };
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
 * @param {HTMLInputElement} opts.inputEl
 * @param {HTMLElement} opts.listEl - `<ul>` to render per-file status rows into
 * @param {HTMLElement} opts.emptyStateEl - shown while no files have been dropped
 * @param {() => boolean} opts.isTrackReady - must return true (a GPX is parsed) before drops are accepted
 * @param {() => Date|null} opts.getStartTime - current route start time, or null if unset/invalid
 * @param {() => {isValid: boolean, resolvedDate?: Date}} opts.getStartTimeResult -
 *   current value of the main start-time toggle; `resolvedDate` is the
 *   activity's end time regardless of whether it was entered directly or as
 *   a duration. A valid result is required before photos are resolved, so
 *   every photo can be checked against the actual activity time window
 *   (see resolvePhotoAnchors below) instead of just its GPS.
 * @param {(photoReadings: {lat: number, lon: number, timestamp: string}[], activityWindow?: {startIso: string, endIso: string}) => Promise<{status: 'ok'|'too_far'|'outside_activity_time', lat: number, lon: number, distanceFromStart: number, timestamp: string, gapM: number}[]>} opts.resolvePhotoAnchors -
 *   pyodideBridge.resolvePhotoAnchors, reused as-is
 * @param {(anchor: {lat: number, lon: number, distanceFromStart: number, timestamp: Date, source: string}) => number} opts.addAnchor -
 *   anchors.js's addAnchor, reused as-is
 * @param {(id: number) => void} opts.removeAnchor - anchors.js's removeAnchor, reused as-is;
 *   called when a successfully-resolved photo's row is dismissed, so removing the row also
 *   removes the anchor it created
 * @param {(message: string, kind?: 'error'|'input') => void} opts.setStatus
 */
export function initPhotoDrop({
  dropzoneEl,
  inputEl,
  listEl: list,
  emptyStateEl: emptyState,
  isTrackReady,
  getStartTime,
  getStartTimeResult,
  resolvePhotoAnchors,
  addAnchor,
  removeAnchor,
  setStatus,
}) {
  listEl = list;
  emptyStateEl = emptyState;
  setEmptyState();

  async function handleFiles(files) {
    if (!files || files.length === 0) {
      return;
    }
    if (!isTrackReady()) {
      setStatus('Upload a GPX route before adding photos.', 'input');
      return;
    }
    const startTimeResult = getStartTimeResult();
    const start = getStartTime();
    if (!startTimeResult.isValid || !start) {
      setStatus('Set a start time and duration before adding photos.', 'input');
      return;
    }
    const activityWindow = { startIso: start.toISOString(), endIso: startTimeResult.resolvedDate.toISOString() };

    const readings = (await Promise.all([...files].map((file) => readFile(file, removeAnchor)))).filter(Boolean);
    if (readings.length === 0) {
      return;
    }

    let results;
    try {
      results = await resolvePhotoAnchors(
        readings.map((r) => ({ lat: r.lat, lon: r.lon, timestamp: r.timestamp.toISOString() })),
        activityWindow,
      );
    } catch (error) {
      console.error(error);
      // This row is too narrow for a full internal error (traceback and
      // all) — only swap in the real message when it's an InputError, whose
      // text is already short and written for the user; anything else keeps
      // the generic label, with the detail left to the console for debugging.
      const described = describeError(error);
      const rowMessage = described.kind === 'input' ? described.message : 'Could not match to route';
      readings.forEach((r) => r.setRowStatus(rowMessage, true));
      return;
    }

    readings.forEach((reading, index) => {
      const result = results[index];
      if (result.status === 'outside_activity_time') {
        reading.setRowStatus(`Taken ${formatDateTime(reading.timestamp)}, outside the activity's time — ignored`, true);
        return;
      }
      if (result.status !== 'ok') {
        reading.setRowStatus(`Too far from route (${formatGapMeters(result.gapM)} away)`, true);
        return;
      }
      const anchorId = addAnchor({
        lat: result.lat,
        lon: result.lon,
        distanceFromStart: result.distanceFromStart,
        timestamp: reading.timestamp,
        source: 'photo',
      });
      reading.setAnchorId(anchorId);
      reading.setRowStatus('Anchor added');
    });
  }

  dropzoneEl.addEventListener('click', () => inputEl.click());
  dropzoneEl.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      inputEl.click();
    }
  });
  inputEl.addEventListener('change', () => {
    void handleFiles(inputEl.files);
    inputEl.value = '';
  });

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
