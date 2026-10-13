// The activity-profile panel over the bottom of the map: hidden until the
// first successful conversion, then slides up from the bottom of the screen,
// covering the map rather than shrinking it, so the map never re-zooms or
// loads new tiles (the animation itself is pure CSS, driven by the
// `.has-profile` class on the app shell — see css/profile.css). Owns the panel's
// open/closed state, its hide/show buttons, the pace/speed, Time/Distance,
// and per-series visibility toggles, and wiring the chart's hover to the
// map's hover dot. The chart itself lives in profileChart.js. main.js
// creates the one panel.

import { createProfileChart } from './profileChart.js';
import { wireSegmented } from '../inputs/segmented.js';

const METRIC_LABELS = { pace: 'Pace (min/km)', speed: 'Speed (km/h)' };

function isSeriesShown(button) {
  return button.getAttribute('aria-pressed') === 'true';
}

/**
 * Wires up the profile panel, hidden until the first `show`.
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
 * @param {{showHoverMarker: (lat: number, lon: number) => void, hideHoverMarker: () => void}} opts.map -
 *   map.js's hover dot, which follows the chart's hover
 * @returns {{
 *   show: (samples: object[], sport: 'running'|'hiking') => void,
 *   hide: () => void,
 * }}
 */
export function createProfilePanel({
  shellEl,
  panelEl,
  chartEl,
  axisControlEl,
  metricControlEl,
  speedLabelEl,
  speedToggleEl,
  elevationToggleEl,
  closeButtonEl,
  showButtonEl,
  map,
}) {
  let hasData = false;
  // Null until the user picks a unit themselves; until then each conversion
  // uses its sport's default (pace for running, speed for hiking).
  let chosenMetric = null;

  const chart = createProfileChart(chartEl, {
    onHover: (sample) => {
      if (sample) {
        map.showHoverMarker(sample.lat, sample.lon);
      } else {
        map.hideHoverMarker();
      }
    },
  });

  function applyMetric(metric) {
    metricControl.select(metric);
    speedLabelEl.textContent = METRIC_LABELS[metric];
    chart.setOptions({ metric });
  }

  function isOpen() {
    return shellEl.classList.contains('has-profile');
  }

  function setOpen(open) {
    shellEl.classList.toggle('has-profile', open);
    panelEl.setAttribute('aria-hidden', String(!open));
    panelEl.inert = !open;
    showButtonEl.hidden = open || !hasData;
    if (!open) {
      map.hideHoverMarker();
    }
  }

  wireSegmented(axisControlEl, (axis) => chart.setOptions({ axis: /** @type {'time'|'distance'} */ (axis) }));
  const metricControl = wireSegmented(metricControlEl, (metric) => {
    chosenMetric = metric;
    applyMetric(metric);
  });

  const seriesToggles = { showSpeed: speedToggleEl, showElevation: elevationToggleEl };
  for (const [option, button] of Object.entries(seriesToggles)) {
    button.addEventListener('click', () => {
      const shown = !isSeriesShown(button);
      button.setAttribute('aria-pressed', String(shown));
      chart.setOptions({ [option]: shown });
      // The last series still showing can't be turned off, so the chart is never empty.
      const shownCount = Object.values(seriesToggles).filter(isSeriesShown).length;
      for (const other of Object.values(seriesToggles)) {
        other.disabled = shownCount === 1 && isSeriesShown(other);
      }
    });
  }

  closeButtonEl.addEventListener('click', () => {
    setOpen(false);
    showButtonEl.focus();
  });
  showButtonEl.addEventListener('click', () => {
    setOpen(true);
    closeButtonEl.focus();
  });

  return {
    /**
     * Shows the profile of a finished conversion, opening the panel if it's
     * closed (including if the user hid it). Called again after a
     * re-conversion, it just swaps the data in place.
     *
     * @param {object[]} samples - `profile` from pyodideBridge.js `convert()`
     * @param {'running'|'hiking'} sport - the sport the conversion ran with
     */
    show(samples, sport) {
      hasData = true;
      applyMetric(chosenMetric ?? (sport === 'running' ? 'pace' : 'speed'));
      chart.setData(samples);
      if (isOpen()) {
        return;
      }
      // Starting the slide in the same task as the chart build (and the rest
      // of the post-conversion DOM work) drops its first frames, so it visibly
      // jumps. Wait until that work has been painted, then start it on a clean frame.
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (hasData) {
          setOpen(true);
        }
      }));
    },

    /** Closes the panel and forgets its data, e.g. when a new GPX makes the shown profile stale. */
    hide() {
      hasData = false;
      setOpen(false);
      // The samples would otherwise stay in memory until the next conversion.
      chart.setData([]);
    },
  };
}
