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
import { effectiveStopTimes } from './activityWindow.js';
import { initTheme } from './theme.js';
import { initShortcuts } from './shortcuts.js';
import { initDevicePicker } from './devicePicker.js';
import { createAnchorPlacer } from './anchorPopovers.js';
import { initPhotoDrop, resetPhotoDrop } from './photoAnchors.js';
import { initProfilePanel, showProfile, hideProfile } from './profilePanel.js';
import { coveredMapHeight, initMobileLayout, revealMap } from './mobileLayout.js';
import { describeError, fitFileNameFromGpx, formatDistanceKm, formatFileSize, leftOutNote } from './format.js';

const dropzone = document.getElementById('dropzone');
const gpxFileInput = document.getElementById('gpxFileInput');
const fileNameEl = document.getElementById('fileName');
const routeSummaryEl = document.getElementById('routeSummary');
const summaryDistanceEl = document.getElementById('summaryDistance');
const summaryElevationEl = document.getElementById('summaryElevation');
const mapEmptyStateEl = document.getElementById('mapEmptyState');
const sportControlEl = document.getElementById('sportControl');
const surfaceLookupToggleEl = document.getElementById('surfaceLookupToggle');
const smoothnessSliderEl = document.getElementById('smoothnessSlider');
const smoothnessValueEl = document.getElementById('smoothnessValue');
const deviceButtonEl = document.getElementById('deviceButton');
const deviceDialogEl = document.getElementById('deviceDialog');
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
const shortcutsToggle = document.getElementById('shortcutsToggle');
const shortcutsPanelEl = document.getElementById('shortcutsPanel');
const appShellEl = document.getElementById('appShell');
const profilePanelEl = document.getElementById('profilePanel');
const profileChartEl = document.getElementById('profileChart');
const profileAxisControlEl = document.getElementById('profileAxisControl');
const profileMetricControlEl = document.getElementById('profileMetricControl');
const profileSpeedLabelEl = document.getElementById('profileSpeedLabel');
const profileSpeedToggleEl = document.getElementById('profileSpeedToggle');
const profileElevationToggleEl = document.getElementById('profileElevationToggle');
const profileCloseButtonEl = document.getElementById('profileCloseButton');
const profileShowButtonEl = document.getElementById('profileShowButton');
const mapPanelEl = document.getElementById('mapPanel');
const sidebarEl = document.getElementById('sidebar');

// Route data from the most recently parsed GPX file, and the current values
// of the sport/start-time controls. Read by the convert handler and by the
// anchor placer (via the getters passed to createAnchorPlacer below).
let routePoints = null;
let totalDistance = null;
// Name of the GPX file behind routePoints, used to name the FIT download after
// it. Set only once a parse succeeds, so a failed upload can't rename the
// output of the route that is still loaded.
let gpxFileName = null;
let sportValue = 'hiking';
let startTimeResult = { isValid: false };

anchorsModule.initAnchorList(anchorListEl, anchorEmptyStateEl);
stopsModule.initStopList(stopListEl, stopEmptyStateEl);
mapModule.initMap('map');
initTheme(themeToggle);
initProfilePanel({
  shellEl: appShellEl,
  panelEl: profilePanelEl,
  chartEl: profileChartEl,
  axisControlEl: profileAxisControlEl,
  metricControlEl: profileMetricControlEl,
  speedLabelEl: profileSpeedLabelEl,
  speedToggleEl: profileSpeedToggleEl,
  elevationToggleEl: profileElevationToggleEl,
  closeButtonEl: profileCloseButtonEl,
  showButtonEl: profileShowButtonEl,
  mapModule,
});
initMobileLayout({
  sidebarEl,
  mapPanelEl,
  profilePanelEl,
  profileShowButtonEl,
  mapModule,
});
mapModule.setBottomInsetProvider(coveredMapHeight);
const devicePicker = initDevicePicker({ buttonEl: deviceButtonEl, dialogEl: deviceDialogEl });
initShortcuts({
  triggerButton: shortcutsToggle,
  panelContainer: shortcutsPanelEl,
  sportControlEl,
  runButton,
  gpxFileInput,
  photoFileInput: photoFileInputEl,
  deviceButton: deviceButtonEl,
});

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
    // The avg-speed mode's default unit follows the sport (min/km for
    // running, km/h otherwise) until the user picks one themselves.
    startTimeToggle.refresh();
  });
});

/* ---------- surface lookup toggle ---------- */

// The one setting that sends route data off the device (to Valhalla), so the
// user's choice is remembered across visits. Storage can throw or come back
// empty (private window, blocked site data); then it's just on by default.
const SURFACE_LOOKUP_STORAGE_KEY = 'surfaceLookup';

try {
  if (localStorage.getItem(SURFACE_LOOKUP_STORAGE_KEY) === 'off') {
    surfaceLookupToggleEl.checked = false;
  }
} catch {
  // Keep the default.
}

surfaceLookupToggleEl.addEventListener('change', () => {
  try {
    localStorage.setItem(SURFACE_LOOKUP_STORAGE_KEY, surfaceLookupToggleEl.checked ? 'on' : 'off');
  } catch {
    // Not remembered this time; the checkbox itself still applies.
  }
});

/* ---------- pace smoothness slider ---------- */

// Remembered like the surface toggle: someone who prefers a smoother (or
// rougher) pace likely wants it for every route. The slider's own min/max
// bound the value, so only a stored value inside them is restored.
const SMOOTHNESS_STORAGE_KEY = 'smoothness';

try {
  const stored = Number(localStorage.getItem(SMOOTHNESS_STORAGE_KEY));
  if (Number.isInteger(stored) && stored >= Number(smoothnessSliderEl.min) && stored <= Number(smoothnessSliderEl.max)) {
    smoothnessSliderEl.value = String(stored);
  }
} catch {
  // Keep the default.
}
smoothnessValueEl.textContent = smoothnessSliderEl.value;

smoothnessSliderEl.addEventListener('input', () => {
  smoothnessValueEl.textContent = smoothnessSliderEl.value;
});

smoothnessSliderEl.addEventListener('change', () => {
  try {
    localStorage.setItem(SMOOTHNESS_STORAGE_KEY, smoothnessSliderEl.value);
  } catch {
    // Not remembered this time; the slider itself still applies.
  }
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

/**
 * Total time the activity spends stopped, across both stop modes. The
 * avg-speed entry adds this on top of the moving time, so the speed the user
 * types is a moving speed rather than a stops-included one.
 */
function totalStopSeconds() {
  // Every stop, active or not: whether a stop is active depends on the end
  // time, which in avg-speed mode depends on this very total.
  return stopsModule.getStops().reduce((total, stop) => {
    if (stop.mode === 'duration') {
      return total + (Number.isFinite(stop.durationSeconds) ? stop.durationSeconds : 0);
    }
    // Resolved against the current start, since a side entered as a duration follows it.
    const { arrival, departure } = effectiveStopTimes(stop, getStartTime());
    const seconds = (departure.getTime() - arrival.getTime()) / 1000;
    return total + (Number.isFinite(seconds) ? seconds : 0);
  }, 0);
}

const startTimeToggle = createTimeToggle({
  container: startTimeToggleContainer,
  variant: 'durationOrEnd',
  getReferenceTime: getStartTime,
  speedMode: {
    getTotalDistance: () => totalDistance,
    getStopSeconds: totalStopSeconds,
    getSport: () => sportValue,
  },
  onChange: (result) => {
    startTimeResult = result;
    updateConvertAvailability();
    // Every placed anchor and stop is re-checked against the new start–end
    // at once, so one that no longer fits shows as inactive now rather than
    // failing the conversion later.
    const start = getStartTime();
    const activityWindow = result.isValid && start ? { start, end: result.resolvedDate } : null;
    anchorsModule.setActivityWindow(activityWindow);
    stopsModule.setActivityWindow(activityWindow);
  },
});

// Placing or deleting a stop changes the avg-speed mode's derived duration,
// and neither happens through this module (see stops.js).
stopsModule.setStopsChangeListener(() => startTimeToggle.refresh());

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
    hideProfile();

    routePoints = points;
    totalDistance = summary.total_distance;
    gpxFileName = file.name;

    summaryDistanceEl.textContent = formatDistanceKm(summary.total_distance);
    summaryElevationEl.textContent = `${Math.round(summary.total_elevation_gain)} m`;
    routeSummaryEl.hidden = false;
    mapEmptyStateEl.hidden = true;

    // The avg-speed mode divides by this route's distance, so it only has an
    // answer once a GPX has actually parsed.
    startTimeToggle.refresh();

    // Before rendering, so the route is fitted to the map the sheet will leave visible.
    revealMap();
    mapModule.renderRoute(points, anchorPlacer.handleRouteClick);
    setStatus('Route loaded. Set a start time and a duration, end time or average speed, or tap the route to add anchors.');
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
gpxFileInput.addEventListener('change', () => {
  const file = gpxFileInput.files[0];
  // Cleared so that choosing the same file again (e.g. to start over) still
  // fires 'change'. The File object stays readable after this.
  gpxFileInput.value = '';
  void handleFile(file);
});

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
  // Each conversion's Blob stays in memory for as long as a URL points at it.
  if (downloadLink.href) {
    URL.revokeObjectURL(downloadLink.href);
  }
  downloadLink.removeAttribute('href');
  runButton.disabled = true;
  runButton.classList.add('is-loading');
  const originalLabel = runButton.textContent;
  runButton.textContent = 'Converting…';

  try {
    setStatus('Converting…');
    // Re-resolved here rather than reusing the cached startTimeResult: in
    // avg-speed mode the duration is derived from the route and the stop
    // list, so this is the one place guaranteed to be reading them as they
    // are right now. getResult() is pure — it only re-reads the fields.
    const resolvedTime = startTimeToggle.getResult();
    if (!resolvedTime.isValid) {
      return;
    }
    const sportEnumName = sportValue === 'running' ? 'RUNNING' : 'HIKING';
    // Only the anchors and stops inside the current start–end; the rest stay
    // in their lists, marked inactive, and are reported below.
    const anchorsPayload = anchorsModule.getActiveAnchors().map((a) => ({
      distanceFromStart: a.distanceFromStart,
      timestamp: a.timestamp.toISOString(),
      source: a.source ?? 'user',
    }));
    const stopsPayload = stopsModule.getActiveStops().map((s) => ({
      distanceFromStart: s.distanceFromStart,
      durationSeconds: s.mode === 'duration' ? s.durationSeconds : undefined,
      startIso: s.mode === 'startEnd' ? s.arrival.toISOString() : undefined,
      endIso: s.mode === 'startEnd' ? s.departure.toISOString() : undefined,
    }));

    // Captured now, so flipping the sport toggle afterward can't relabel this run's chart.
    const convertedSport = sportValue;
    const { fitBytes, profile } = await convert({
      startIso: startTimeField.getValue().toISOString(),
      durationSeconds: resolvedTime.durationSeconds,
      sportEnumName,
      anchors: anchorsPayload,
      stops: stopsPayload,
      device: devicePicker.getConvertDevice(),
      surfaceLookup: surfaceLookupToggleEl.checked,
      smoothness: Number(smoothnessSliderEl.value),
    });

    const blob = new Blob([fitBytes], { type: 'application/octet-stream' });
    downloadLink.href = URL.createObjectURL(blob);
    downloadLink.download = fitFileNameFromGpx(gpxFileName);
    downloadLink.hidden = false;
    downloadLink.textContent = `Download FIT (${formatFileSize(blob.size)})`;
    showProfile(profile, convertedSport);
    setStatus(`FIT file generated successfully.${leftOutNote(anchorsModule.countInactiveAnchors(), stopsModule.countInactiveStops())}`);
    downloadLink.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    // So the Ctrl+Enter path ends one plain Enter away from the file, rather
    // than leaving focus on a button that just disabled itself.
    downloadLink.focus();
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
