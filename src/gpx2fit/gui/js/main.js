import { parseGpx, resolveAnchorDistance, convert } from './pyodideBridge.js';
import * as mapModule from './map.js';
import { createTimeToggle } from './timeInput.js';
import * as anchorsModule from './anchors.js';

const dropzone = document.getElementById('dropzone');
const gpxFileInput = document.getElementById('gpxFileInput');
const fileNameEl = document.getElementById('fileName');
const routeSummaryEl = document.getElementById('routeSummary');
const summaryDistanceEl = document.getElementById('summaryDistance');
const summaryElevationEl = document.getElementById('summaryElevation');
const mapEmptyStateEl = document.getElementById('mapEmptyState');
const sportControlEl = document.getElementById('sportControl');
const startTimeInput = document.getElementById('startTimeInput');
const startTimeToggleContainer = document.getElementById('startTimeToggle');
const anchorListEl = document.getElementById('anchorList');
const anchorEmptyStateEl = document.getElementById('anchorEmptyState');
const runButton = document.getElementById('runButton');
const statusEl = document.getElementById('status');
const downloadLink = document.getElementById('downloadLink');
const themeToggle = document.getElementById('themeToggle');

let routePoints = null;
let sportValue = 'hiking';
let startTimeResult = { isValid: false };

anchorsModule.initAnchorList(anchorListEl, anchorEmptyStateEl);
mapModule.initMap('map');

/* ---------- theme ---------- */

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  themeToggle.textContent = theme === 'dark' ? '☀️' : '🌙';
  mapModule.setMapTheme(theme);
  const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
  mapModule.updateRouteColor(accent);
}

function initTheme() {
  const stored = localStorage.getItem('theme');
  if (stored === 'light' || stored === 'dark') {
    applyTheme(stored);
    return;
  }
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  applyTheme(prefersDark ? 'dark' : 'light');
}

themeToggle.addEventListener('click', () => {
  const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  localStorage.setItem('theme', next);
  applyTheme(next);
});

initTheme();

/* ---------- status ---------- */

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle('status-error', isError);
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

const startTimeToggle = createTimeToggle({
  container: startTimeToggleContainer,
  variant: 'durationOrEnd',
  getReferenceTime: () => (startTimeInput.value ? new Date(startTimeInput.value) : null),
  onChange: (result) => {
    startTimeResult = result;
    updateConvertAvailability();
  },
});

startTimeInput.addEventListener('input', () => startTimeToggle.refresh());

const defaultStart = new Date(Date.now() + 60_000);
startTimeInput.value = new Date(defaultStart.getTime() - defaultStart.getTimezoneOffset() * 60000)
  .toISOString()
  .slice(0, 16);
startTimeToggle.refresh();

/* ---------- file upload ---------- */

function findNearestRoutePoint(distance) {
  if (!routePoints || routePoints.length === 0) {
    return null;
  }
  let nearest = routePoints[0];
  let bestDelta = Math.abs(nearest.distance - distance);
  for (const point of routePoints) {
    const delta = Math.abs(point.distance - distance);
    if (delta < bestDelta) {
      nearest = point;
      bestDelta = delta;
    }
  }
  return nearest;
}

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
    routePoints = points;

    summaryDistanceEl.textContent = `${(summary.total_distance / 1000).toFixed(2)} km`;
    summaryElevationEl.textContent = `${Math.round(summary.total_elevation_gain)} m`;
    routeSummaryEl.hidden = false;
    mapEmptyStateEl.hidden = true;

    mapModule.renderRoute(points, handleRouteClick);
    setStatus('Route loaded. Set a start time and duration, or click the route to add anchors.');
  } catch (error) {
    console.error(error);
    setStatus(`Error: ${error instanceof Error ? error.message : String(error)}`, true);
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
gpxFileInput.addEventListener('change', () => handleFile(gpxFileInput.files[0]));

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
  handleFile(file);
});

/* ---------- anchor placement ---------- */

async function handleRouteClick(lat, lon) {
  if (!startTimeResult.isValid) {
    setStatus('Set a valid start time before adding anchors.', true);
    return;
  }

  try {
    const distanceFromStart = await resolveAnchorDistance(lat, lon);
    const snapped = findNearestRoutePoint(distanceFromStart) || { lat, lon };

    mapModule.openAnchorPopup(snapped.lat, snapped.lon, (container, close) => {
      const heading = document.createElement('p');
      heading.className = 'popover-heading';
      heading.textContent = `${(distanceFromStart / 1000).toFixed(2)} km`;
      container.append(heading);

      const toggleContainer = document.createElement('div');
      container.append(toggleContainer);

      const confirmBtn = document.createElement('button');
      confirmBtn.type = 'button';
      confirmBtn.className = 'primary-button popover-confirm';
      confirmBtn.textContent = 'Add anchor';
      confirmBtn.disabled = true;
      container.append(confirmBtn);

      let lastResult = { isValid: false };
      createTimeToggle({
        container: toggleContainer,
        variant: 'durationOrTimeOfDay',
        getReferenceTime: () => (startTimeInput.value ? new Date(startTimeInput.value) : null),
        onChange: (result) => {
          lastResult = result;
          confirmBtn.disabled = !result.isValid;
        },
      });

      confirmBtn.addEventListener('click', () => {
        if (!lastResult.isValid) {
          return;
        }
        anchorsModule.addAnchor({
          lat: snapped.lat,
          lon: snapped.lon,
          distanceFromStart,
          timestamp: lastResult.resolvedDate,
        });
        close();
      });
    });
  } catch (error) {
    console.error(error);
    setStatus(`Error: ${error instanceof Error ? error.message : String(error)}`, true);
  }
}

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
      source: 'user',
    }));

    const fitBytes = await convert({
      startIso: new Date(startTimeInput.value).toISOString(),
      durationSeconds: startTimeResult.durationSeconds,
      sportEnumName,
      anchors: anchorsPayload,
    });

    const blob = new Blob([fitBytes], { type: 'application/octet-stream' });
    const url = URL.createObjectURL(blob);
    downloadLink.href = url;
    downloadLink.hidden = false;
    downloadLink.textContent = `Download FIT (${blob.size} bytes)`;
    setStatus('FIT file generated successfully.');
  } catch (error) {
    console.error(error);
    setStatus(`Error: ${error instanceof Error ? error.message : String(error)}`, true);
  } finally {
    runButton.classList.remove('is-loading');
    runButton.textContent = originalLabel;
    updateConvertAvailability();
  }
});
