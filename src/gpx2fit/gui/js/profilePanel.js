// The activity-profile panel over the bottom of the map: hidden until the
// first successful conversion, then slides up from the bottom of the screen,
// covering the map rather than shrinking it, so the map never re-zooms or
// loads new tiles (the animation itself is pure CSS, driven by the
// `.has-profile` class on the app shell — see styles.css). Owns the panel's
// open/closed state, its hide/show buttons, the pace/speed, Time/Distance,
// and per-series visibility toggles, and wiring the chart's hover to the
// map's hover dot. The chart itself lives in profileChart.js.

import { createProfileChart } from './profileChart.js';

const METRIC_LABELS = { pace: 'Pace (min/km)', speed: 'Speed (km/h)' };

let shell = null;
let panel = null;
let chart = null;
let showButton = null;
let metricControl = null;
let speedLabelEl = null;
let seriesToggles = null;
let map = null;
let hasData = false;
// Null until the user picks a unit themselves; until then each conversion
// uses its sport's default (pace for running, speed for hiking).
let chosenMetric = null;

function selectSegment(control, value) {
  control.querySelectorAll('.segmented-option').forEach((b) => {
    const active = b.dataset.value === value;
    b.classList.toggle('is-active', active);
    b.setAttribute('aria-selected', String(active));
  });
}

function wireSegmented(control, onSelect) {
  control.querySelectorAll('.segmented-option').forEach((btn) => {
    btn.addEventListener('click', () => {
      selectSegment(control, btn.dataset.value);
      onSelect(btn.dataset.value);
    });
  });
}

function applyMetric(metric) {
  selectSegment(metricControl, metric);
  speedLabelEl.textContent = METRIC_LABELS[metric];
  chart.setOptions({ metric });
}

function isSeriesShown(button) {
  return button.getAttribute('aria-pressed') === 'true';
}

/**
 * Wires up the profile panel. Call once at startup, after `mapModule.initMap`.
 *
 * @param {object} opts
 * @param {HTMLElement} opts.shellEl - the `.app-shell` that `.has-profile` goes on
 * @param {HTMLElement} opts.panelEl - the `#profilePanel` section
 * @param {HTMLElement} opts.chartEl - the element the chart renders into
 * @param {HTMLElement} opts.axisControlEl - the Time/Distance segmented control
 * @param {HTMLElement} opts.metricControlEl - the min/km vs km/h segmented control
 * @param {HTMLElement} opts.speedLabelEl - legend text that switches between speed and pace
 * @param {HTMLButtonElement} opts.speedToggleEl - legend button showing/hiding the speed line
 * @param {HTMLButtonElement} opts.elevationToggleEl - legend button showing/hiding elevation
 * @param {HTMLButtonElement} opts.closeButtonEl - hides the panel
 * @param {HTMLButtonElement} opts.showButtonEl - reopens the hidden panel
 * @param {{showHoverMarker: Function, hideHoverMarker: Function}} opts.mapModule
 */
export function initProfilePanel({
  shellEl,
  panelEl,
  chartEl,
  axisControlEl,
  metricControlEl,
  speedLabelEl: labelEl,
  speedToggleEl,
  elevationToggleEl,
  closeButtonEl,
  showButtonEl,
  mapModule,
}) {
  shell = shellEl;
  panel = panelEl;
  showButton = showButtonEl;
  metricControl = metricControlEl;
  speedLabelEl = labelEl;
  map = mapModule;

  chart = createProfileChart(chartEl, {
    onHover: (sample) => {
      if (sample) {
        map.showHoverMarker(sample.lat, sample.lon);
      } else {
        map.hideHoverMarker();
      }
    },
  });

  wireSegmented(axisControlEl, (axis) => chart.setOptions({ axis }));
  wireSegmented(metricControlEl, (metric) => {
    chosenMetric = metric;
    applyMetric(metric);
  });

  seriesToggles = { showSpeed: speedToggleEl, showElevation: elevationToggleEl };
  for (const [option, button] of Object.entries(seriesToggles)) {
    button.addEventListener('click', () => {
      const shown = !isSeriesShown(button);
      button.setAttribute('aria-pressed', String(shown));
      chart.setOptions({ [option]: shown });
      // The last series still showing can't be turned off, so the chart is never empty.
      for (const other of Object.values(seriesToggles)) {
        other.disabled = Object.values(seriesToggles).filter(isSeriesShown).length === 1 && isSeriesShown(other);
      }
    });
  }

  closeButtonEl.addEventListener('click', () => {
    setOpen(false);
    showButton.focus();
  });
  showButton.addEventListener('click', () => {
    setOpen(true);
    closeButtonEl.focus();
  });
}

/** @returns {boolean} whether the panel is currently open */
export function isProfileOpen() {
  return shell.classList.contains('has-profile');
}

function setOpen(open) {
  shell.classList.toggle('has-profile', open);
  panel.setAttribute('aria-hidden', String(!open));
  panel.inert = !open;
  showButton.hidden = open || !hasData;
  if (!open) {
    map.hideHoverMarker();
  }
}

/**
 * Shows the profile of a finished conversion, opening the panel if it's
 * closed (including if the user hid it). Called again after a re-conversion,
 * it just swaps the data in place.
 *
 * @param {object[]} samples - `profile` from pyodideBridge.js `convert()`
 * @param {'running'|'hiking'} sport - the sport the conversion ran with
 */
export function showProfile(samples, sport) {
  hasData = true;
  applyMetric(chosenMetric ?? (sport === 'running' ? 'pace' : 'speed'));
  chart.setData(samples);
  if (isProfileOpen()) {
    return;
  }
  // Starting the slide in the same task as the chart build (and the rest of
  // the post-conversion DOM work) drops its first frames, so it visibly
  // jumps. Wait until that work has been painted, then start it on a clean frame.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (hasData) {
      setOpen(true);
    }
  }));
}

/** Closes the panel and forgets its data, e.g. when a new GPX makes the shown profile stale. */
export function hideProfile() {
  hasData = false;
  setOpen(false);
}
