// App entry point: wires DOM elements to the other modules, creates the
// shared app state (appState.js) they read, and runs the two flows that
// touch everything: loading a GPX and converting. Anything that grows into
// its own concern (the status line, remembered settings, anchor-popover UI)
// gets split into its own module rather than growing this file.

import {
  parseGpx, convert, resolveAnchorCandidates, resolvePhotoAnchors, warmUpPyodide,
} from './bridge/pyodideBridge.js';
import * as mapModule from './route/map.js';
import { createAppState } from './state/appState.js';
import { createStatus } from './shell/status.js';
import { initSmoothnessSetting, initSurfaceLookupSetting } from './state/settings.js';
import { createTimeToggle } from './inputs/timeInput.js';
import { createDateTimeField } from './inputs/dateTimeField.js';
import * as anchorsModule from './route/anchors.js';
import * as stopsModule from './route/stops.js';
import { effectiveStopTimes } from './route/activityWindow.js';
import { initTheme } from './shell/theme.js';
import { initGlass } from './shell/glass.js';
import { initShortcuts } from './shell/shortcuts.js';
import { initDevicePicker } from './devices/devicePicker.js';
import { createAnchorPlacer } from './placement/anchorPopovers.js';
import { initPhotoDrop, resetPhotoDrop } from './placement/photoAnchors.js';
import { wireDropzone } from './inputs/dropzone.js';
import { wireSegmented } from './inputs/segmented.js';
import { initProfilePanel, showProfile, hideProfile } from './profile/profilePanel.js';
import { coveredMapHeight, initMobileLayout, revealMap } from './shell/mobileLayout.js';
import {
  describeError,
  fitFileNameFromGpx,
  formatDistanceKm,
  formatFileSize,
  leftOutNote,
  statusWithWarnings,
} from './util/format.js';

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
const photoGalleryInputEl = document.getElementById('photoGalleryInput');
const photoGalleryLinkEl = document.getElementById('photoGalleryLink');
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

// The parsed route, the sport and the start time, read by the convert
// handler, the anchor placer and the photo drop.
const appState = createAppState({
  sport: sportControlEl.querySelector('.segmented-option.is-active')?.dataset.value,
});
const setStatus = createStatus(statusEl);

anchorsModule.initAnchorList(anchorListEl, anchorEmptyStateEl);
stopsModule.initStopList(stopListEl, stopEmptyStateEl);
// Every placed anchor and stop is re-checked against a new start–end at
// once, so one that no longer fits shows as inactive now rather than failing
// the conversion later.
appState.onWindowChange((activityWindow) => {
  anchorsModule.setActivityWindow(activityWindow);
  stopsModule.setActivityWindow(activityWindow);
});
mapModule.initMap('map');
// Every visitor uploads a GPX next, so start loading Python now.
warmUpPyodide();
initTheme(themeToggle);
initGlass();
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
  startTimeFieldEl,
  startTimeToggleEl: startTimeToggleContainer,
  appShellEl,
  profileCloseButton: profileCloseButtonEl,
  profileShowButton: profileShowButtonEl,
});

/* ---------- remembered settings ---------- */

initSurfaceLookupSetting(surfaceLookupToggleEl);
initSmoothnessSetting(smoothnessSliderEl, smoothnessValueEl);

/* ---------- start time control ---------- */

function updateConvertAvailability() {
  runButton.disabled = !(appState.route && appState.timeResult.isValid);
}

// The field and the toggle refer to each other: the toggle measures from the
// field's value, and the field refreshes the toggle. The field is built first,
// which is safe because createDateTimeField never calls onChange while it is
// being built, only on a later edit or setValue (by then the toggle exists).
const startTimeField = createDateTimeField({
  container: startTimeFieldEl,
  dateAriaLabel: 'Start date',
  timeAriaLabel: 'Start time',
  onChange: () => startTimeToggle.refresh(),
});

// The field's value as it is now. appState.startTime only catches up once the
// toggle reports, so anything the toggle itself calls while working out its
// result (the reference time, the stop total) reads the field.
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
    getTotalDistance: () => appState.route?.totalDistance ?? null,
    getStopSeconds: totalStopSeconds,
    getSport: () => appState.sport,
  },
  onChange: (result) => {
    // Re-checks the anchors and stops too, if the window moved (see onWindowChange above).
    appState.setTime(getStartTime(), result);
    updateConvertAvailability();
  },
});

// Placing or deleting a stop changes the avg-speed mode's derived duration,
// and neither happens through this module (see stops.js).
stopsModule.setStopsChangeListener(() => startTimeToggle.refresh());

startTimeField.setValue(new Date(Date.now() + 60_000));
startTimeToggle.refresh();

/* ---------- sport segmented control ---------- */

// After the start-time toggle, which a sport change refreshes.
wireSegmented(sportControlEl, (sport) => {
  appState.setSport(sport);
  // The avg-speed mode's default unit follows the sport (min/km for
  // running, km/h otherwise) until the user picks one themselves.
  startTimeToggle.refresh();
});

/* ---------- anchor placement ---------- */

const anchorPlacer = createAnchorPlacer({
  appState,
  anchors: anchorsModule,
  stops: stopsModule,
  map: mapModule,
  resolveAnchorCandidates,
  revealMap,
  setStatus,
  placementBannerEl: document.getElementById('photoPlacementBanner'),
});
// A dragged photo pin only moves once the user confirms where.
anchorsModule.setAnchorDragEndListener(anchorPlacer.handleAnchorDrag);

/* ---------- photo drop ---------- */

initPhotoDrop({
  dropzoneEl: photoDropzoneEl,
  inputEl: photoFileInputEl,
  galleryInputEl: photoGalleryInputEl,
  galleryLinkEl: photoGalleryLinkEl,
  listEl: photoListEl,
  emptyStateEl: photoEmptyStateEl,
  summaryEl: document.getElementById('photoSummary'),
  appState,
  resolvePhotoAnchors,
  addAnchor: anchorsModule.addAnchor,
  placePhoto: anchorPlacer.placePhoto,
  cancelPhotoPlacement: anchorPlacer.cancelPhotoPlacement,
  choosePhotoPass: anchorPlacer.choosePhotoPass,
  setStatus,
});

/* ---------- file upload ---------- */

async function handleFile(file) {
  if (!file) {
    return;
  }
  try {
    setStatus('Parsing GPX…');

    const bytes = new Uint8Array(await file.arrayBuffer());
    const { points, summary, warnings } = await parseGpx(bytes);

    // Anchors/stops/photo rows reference distances along the *previous*
    // route — a new upload invalidates all of them, so drop them before
    // wiring up the new one rather than letting them silently carry over.
    anchorsModule.resetAnchors();
    stopsModule.resetStops();
    resetPhotoDrop();
    hideProfile();

    appState.setRoute({ points, totalDistance: summary.total_distance, fileName: file.name });
    // Only now: a file that fails to parse leaves the previous route loaded,
    // and it should still show that route's name.
    fileNameEl.textContent = file.name;
    fileNameEl.hidden = false;

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
    const loaded = statusWithWarnings(
      'Route loaded. Set a start time and a duration, end time or average speed, or tap the route to add anchors.',
      warnings,
    );
    setStatus(loaded.message, loaded.kind);
  } catch (error) {
    console.error(error);
    const { message, kind } = describeError(error);
    setStatus(message, kind);
  }
  updateConvertAvailability();
}

// handleFile is async but fire-and-forget here: it reports its own errors via
// setStatus and never rejects, so there's nothing for the caller to await.
wireDropzone(dropzone, gpxFileInput, (files) => void handleFile(files[0]));

/* ---------- convert ---------- */

runButton.addEventListener('click', async () => {
  // Re-resolved here rather than reusing appState.timeResult: in
  // avg-speed mode the duration is derived from the route and the stop list,
  // so this is the one place guaranteed to be reading them as they are right
  // now. getResult() is pure — it only re-reads the fields.
  const resolvedTime = startTimeToggle.getResult();
  if (!resolvedTime.isValid || !appState.route) {
    // The cached result was stale; re-render it so the button and preview
    // say why, instead of starting a conversion that can't run.
    startTimeToggle.refresh();
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

    // Captured now, so flipping the sport toggle afterward can't relabel this
    // run's chart, and a GPX loaded meanwhile can't rename its file.
    const convertedSport = appState.sport;
    const { fileName } = appState.route;
    const { fitBytes, profile, warnings } = await convert({
      startIso: startTimeField.getValue().toISOString(),
      durationSeconds: resolvedTime.durationSeconds,
      sport: convertedSport,
      anchors: anchorsPayload,
      stops: stopsPayload,
      device: devicePicker.getConvertDevice(),
      surfaceLookup: surfaceLookupToggleEl.checked,
      smoothness: Number(smoothnessSliderEl.value),
    });

    const blob = new Blob([fitBytes], { type: 'application/octet-stream' });
    downloadLink.href = URL.createObjectURL(blob);
    downloadLink.download = fitFileNameFromGpx(fileName);
    downloadLink.hidden = false;
    downloadLink.textContent = `Download FIT (${formatFileSize(blob.size)})`;
    showProfile(profile, convertedSport);
    const generated = statusWithWarnings(
      `FIT file generated successfully.${leftOutNote(anchorsModule.countInactiveAnchors(), stopsModule.countInactiveStops())}`,
      warnings,
    );
    setStatus(generated.message, generated.kind);
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
