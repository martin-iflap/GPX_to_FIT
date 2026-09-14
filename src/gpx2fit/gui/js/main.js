// App entry point: wires DOM elements to the other modules and owns the
// small bits of state (parsed route, chosen sport, chosen start time) that
// several of them need to read. Anything that grows into its own concern
// (theme, anchor-popover UI) gets split into its own module rather than
// growing this file — see theme.js and anchorPopovers.js.

import { parseGpx, convert, resolvePhotoAnchors } from './pyodideBridge.js';
import * as mapModule from './map.js';
import { createTimeToggle } from './timeInput.js';
import { createDateTimeField } from './dateTimeField.js';
import * as anchorsModule from './anchors.js';
import * as stopsModule from './stops.js';
import { initTheme } from './theme.js';
import { createAnchorPlacer } from './anchorPopovers.js';
import { initPhotoDrop, resetPhotoDrop } from './photoAnchors.js';
import { describeError, formatDistanceKm, formatFileSize } from './format.js';

const dropzone = document.getElementById('dropzone');
const gpxFileInput = document.getElementById('gpxFileInput');
const fileNameEl = document.getElementById('fileName');
const routeSummaryEl = document.getElementById('routeSummary');
const summaryDistanceEl = document.getElementById('summaryDistance');
const summaryElevationEl = document.getElementById('summaryElevation');
const mapEmptyStateEl = document.getElementById('mapEmptyState');
const sportControlEl = document.getElementById('sportControl');
const deviceInputEl = document.getElementById('deviceInput');
const startTimeFieldEl = document.getElementById('startTimeField');
const startTimeToggleContainer = document.getElementById('startTimeToggle');
const anchorListEl = document.getElementById('anchorList');
const anchorEmptyStateEl = document.getElementById('anchorEmptyState');
const stopListEl = document.getElementById('stopList');
const stopEmptyStateEl = document.getElementById('stopEmptyState');
const photoDropzoneEl = document.getElementById('photoDropzone');
const photoFileInputEl = document.getElementById('photoFileInput');
const photoListEl = document.getElementById('photoList');
const photoEmptyStateEl = document.getElementById('photoEmptyState');
const runButton = document.getElementById('runButton');
const statusEl = document.getElementById('status');
const downloadLink = document.getElementById('downloadLink');
const themeToggle = document.getElementById('themeToggle');

// Route data from the most recently parsed GPX file, and the current values
// of the sport/start-time controls. Read by the convert handler and by the
// anchor placer (via the getters passed to createAnchorPlacer below).
let routePoints = null;
let totalDistance = null;
let sportValue = 'hiking';
let startTimeResult = { isValid: false };

anchorsModule.initAnchorList(anchorListEl, anchorEmptyStateEl);
stopsModule.initStopList(stopListEl, stopEmptyStateEl);
mapModule.initMap('map');
initTheme(themeToggle);

/* ---------- status ---------- */

const STATUS_ERROR_TIMEOUT_MS = 10000;
let statusTimeoutId = null;

/**
 * Updates the status line.
 *
 * @param {string} message
 * @param {'error'|'input'|false} [kind] - `'error'` for an unexpected/
 *   internal failure (styled red — see describeError in format.js), `'input'`
 *   for a problem traceable to something the user entered or hasn't set up
 *   yet (styled amber, so it visibly reads as "fix this" rather than "this
 *   app is broken"), or omitted/false for a normal, non-error status.
 *
 * Errors are transient nudges, not permanent state, so they clear themselves
 * after a few seconds instead of sitting there until the next unrelated
 * status update happens to overwrite them.
 */
function setStatus(message, kind = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle('status-error', kind === 'error');
  statusEl.classList.toggle('status-input', kind === 'input');

  if (statusTimeoutId) {
    clearTimeout(statusTimeoutId);
    statusTimeoutId = null;
  }
  if (kind) {
    statusTimeoutId = setTimeout(() => {
      statusEl.textContent = 'Ready.';
      statusEl.classList.remove('status-error', 'status-input');
      statusTimeoutId = null;
    }, STATUS_ERROR_TIMEOUT_MS);
  }
}

/* ---------- sport segmented control ---------- */

sportControlEl.querySelectorAll('.segmented-option').forEach((btn) => {
  btn.addEventListener('click', () => {
    sportControlEl.querySelectorAll('.segmented-option').forEach((b) => {
      b.classList.toggle('is-active', b === btn);
      b.setAttribute('aria-selected', String(b === btn));
    });
    sportValue = btn.dataset.value;
  });
});

/* ---------- start time control ---------- */

function updateConvertAvailability() {
  runButton.disabled = !(routePoints && startTimeResult.isValid);
}

const startTimeField = createDateTimeField({
  container: startTimeFieldEl,
  dateAriaLabel: 'Start date',
  timeAriaLabel: 'Start time',
  onChange: () => startTimeToggle.refresh(),
});

function getStartTime() {
  return startTimeField.getValue();
}

const startTimeToggle = createTimeToggle({
  container: startTimeToggleContainer,
  variant: 'durationOrEnd',
  getReferenceTime: getStartTime,
  onChange: (result) => {
    startTimeResult = result;
    updateConvertAvailability();
  },
});

startTimeField.setValue(new Date(Date.now() + 60_000));
startTimeToggle.refresh();

/* ---------- anchor placement ---------- */

const anchorPlacer = createAnchorPlacer({
  getStartTime,
  getStartTimeResult: () => startTimeResult,
  getTotalDistance: () => totalDistance,
  setStatus,
});

/* ---------- photo drop ---------- */

initPhotoDrop({
  dropzoneEl: photoDropzoneEl,
  inputEl: photoFileInputEl,
  listEl: photoListEl,
  emptyStateEl: photoEmptyStateEl,
  isTrackReady: () => routePoints !== null,
  getStartTime,
  getStartTimeResult: () => startTimeResult,
  resolvePhotoAnchors,
  addAnchor: anchorsModule.addAnchor,
  removeAnchor: anchorsModule.removeAnchor,
  setStatus,
});

/* ---------- file upload ---------- */

async function handleFile(file) {
  if (!file) {
    return;
  }
  try {
    setStatus('Parsing GPX…');
    fileNameEl.textContent = file.name;
    fileNameEl.hidden = false;

    const bytes = new Uint8Array(await file.arrayBuffer());
    const { points, summary } = await parseGpx(bytes);

    // Anchors/stops/photo rows reference distances along the *previous*
    // route — a new upload invalidates all of them, so drop them before
    // wiring up the new one rather than letting them silently carry over.
    anchorsModule.resetAnchors();
    stopsModule.resetStops();
    resetPhotoDrop();

    routePoints = points;
    totalDistance = summary.total_distance;

    summaryDistanceEl.textContent = formatDistanceKm(summary.total_distance);
    summaryElevationEl.textContent = `${Math.round(summary.total_elevation_gain)} m`;
    routeSummaryEl.hidden = false;
    mapEmptyStateEl.hidden = true;

    mapModule.renderRoute(points, anchorPlacer.handleRouteClick);
    setStatus('Route loaded. Set a start time and duration, or click the route to add anchors.');
  } catch (error) {
    console.error(error);
    const { message, kind } = describeError(error);
    setStatus(message, kind);
  }
  updateConvertAvailability();
}

dropzone.addEventListener('click', () => gpxFileInput.click());
dropzone.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    gpxFileInput.click();
  }
});
// handleFile is async but fire-and-forget here: it reports its own errors via
// setStatus and never rejects, so there's nothing for the caller to await.
gpxFileInput.addEventListener('change', () => void handleFile(gpxFileInput.files[0]));

['dragenter', 'dragover'].forEach((eventName) => {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add('is-dragover');
  });
});
['dragleave', 'drop'].forEach((eventName) => {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.remove('is-dragover');
  });
});
dropzone.addEventListener('drop', (event) => {
  const file = event.dataTransfer.files && event.dataTransfer.files[0];
  void handleFile(file);
});

/* ---------- convert ---------- */

runButton.addEventListener('click', async () => {
  if (!startTimeResult.isValid || !routePoints) {
    return;
  }

  downloadLink.hidden = true;
  downloadLink.removeAttribute('href');
  runButton.disabled = true;
  runButton.classList.add('is-loading');
  const originalLabel = runButton.textContent;
  runButton.textContent = 'Converting…';

  try {
    setStatus('Converting…');
    const sportEnumName = sportValue === 'running' ? 'RUNNING' : 'HIKING';
    const anchorsPayload = anchorsModule.getAnchors().map((a) => ({
      distanceFromStart: a.distanceFromStart,
      timestamp: a.timestamp.toISOString(),
      source: a.source ?? 'user',
    }));
    const stopsPayload = stopsModule.getStops().map((s) => ({
      distanceFromStart: s.distanceFromStart,
      durationSeconds: s.mode === 'duration' ? s.durationSeconds : undefined,
      startIso: s.mode === 'startEnd' ? s.arrival.toISOString() : undefined,
      endIso: s.mode === 'startEnd' ? s.departure.toISOString() : undefined,
    }));

    const fitBytes = await convert({
      startIso: startTimeField.getValue().toISOString(),
      durationSeconds: startTimeResult.durationSeconds,
      sportEnumName,
      anchors: anchorsPayload,
      stops: stopsPayload,
      device: deviceInputEl.value.trim() || undefined,
    });

    const blob = new Blob([fitBytes], { type: 'application/octet-stream' });
    downloadLink.href = URL.createObjectURL(blob);
    downloadLink.hidden = false;
    downloadLink.textContent = `Download FIT (${formatFileSize(blob.size)})`;
    setStatus('FIT file generated successfully.');
    downloadLink.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (error) {
    console.error(error);
    const { message, kind } = describeError(error);
    setStatus(message, kind);
  } finally {
    runButton.classList.remove('is-loading');
    runButton.textContent = originalLabel;
    updateConvertAvailability();
  }
});
