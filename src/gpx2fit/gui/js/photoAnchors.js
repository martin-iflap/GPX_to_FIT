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

let listEl = null;
let emptyStateEl = null;
let fileCount = 0;

function setEmptyState() {
  if (emptyStateEl) {
    emptyStateEl.hidden = fileCount > 0;
  }
}

/** Adds a status row for a dropped file and returns a setter to update its status text/class as processing progresses. */
function addRow(fileName) {
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
  row.append(info);
  listEl.append(row);

  return (text, isProblem = false) => {
    statusEl.textContent = text;
    row.classList.toggle('is-problem', isProblem);
  };
}

/** Formats a gap distance for display: meters below 1km, one-decimal km above. */
function formatGapMeters(meters) {
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
async function readPhotoMetadata(file) {
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
 * own status row. Returns the extracted `{ lat, lon, timestamp, setRowStatus }`
 * for files with usable metadata, or `null` for anything that can't be
 * batched into a route-resolution call (never throws — failures are
 * reported on the row and swallowed here).
 */
async function readFile(file) {
  const setRowStatus = addRow(file.name);

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

  return { ...metadata, setRowStatus };
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
 * @param {(photoReadings: {lat: number, lon: number, timestamp: string}[]) => Promise<{status: 'ok'|'too_far', lat: number, lon: number, distanceFromStart: number, timestamp: string, gapM: number}[]>} opts.resolvePhotoAnchors -
 *   pyodideBridge.resolvePhotoAnchors, reused as-is
 * @param {(anchor: {lat: number, lon: number, distanceFromStart: number, timestamp: Date, source: string}) => number} opts.addAnchor -
 *   anchors.js's addAnchor, reused as-is
 * @param {(message: string, isError?: boolean) => void} opts.setStatus
 */
export function initPhotoDrop({ dropzoneEl, inputEl, listEl: list, emptyStateEl: emptyState, isTrackReady, resolvePhotoAnchors, addAnchor, setStatus }) {
  listEl = list;
  emptyStateEl = emptyState;
  setEmptyState();

  async function handleFiles(files) {
    if (!files || files.length === 0) {
      return;
    }
    if (!isTrackReady()) {
      setStatus('Upload a GPX route before adding photos.', true);
      return;
    }

    const readings = (await Promise.all([...files].map(readFile))).filter(Boolean);
    if (readings.length === 0) {
      return;
    }

    let results;
    try {
      results = await resolvePhotoAnchors(
        readings.map((r) => ({ lat: r.lat, lon: r.lon, timestamp: r.timestamp.toISOString() })),
      );
    } catch (error) {
      console.error(error);
      readings.forEach((r) => r.setRowStatus('Could not match to route', true));
      return;
    }

    readings.forEach((reading, index) => {
      const result = results[index];
      if (result.status !== 'ok') {
        reading.setRowStatus(`Too far from route (${formatGapMeters(result.gapM)} away)`, true);
        return;
      }
      addAnchor({
        lat: result.lat,
        lon: result.lon,
        distanceFromStart: result.distanceFromStart,
        timestamp: reading.timestamp,
        source: 'photo',
      });
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
