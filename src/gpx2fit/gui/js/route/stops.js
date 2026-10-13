// The placed stops — a real pause at one location, entered either as a
// duration only or as an explicit start/end time. All list/pin/id
// bookkeeping lives in markerList.js; this module only knows a stop's shape,
// how to render its row text, and whether it still fits the activity's
// current time window. main.js creates the one list.

import { createMarkerList } from './markerList.js';
import { effectiveStopTimes, isStopActive } from './activityWindow.js';
import { formatClock, formatDistanceKm, formatDuration } from '../util/format.js';

/**
 * @typedef {{ id: number, distanceFromStart: number, mode: 'duration'|'startEnd', durationSeconds?: number, arrival?: Date, departure?: Date, arrivalOffsetSeconds?: number, departureOffsetSeconds?: number }} Stop
 * `arrivalOffsetSeconds`/`departureOffsetSeconds` are set for a side entered
 * as "Duration since start", which then follows the start (see anchors.js).
 */

function rowLabel(stop, activityWindow) {
  if (stop.mode === 'duration') {
    return formatDuration(stop.durationSeconds);
  }
  const { arrival, departure } = effectiveStopTimes(stop, activityWindow?.start ?? null);
  const label = `${formatClock(arrival)}–${formatClock(departure)}`;
  return isStopActive(stop, activityWindow) ? label : `${label} — outside the activity's time now, not used`;
}

function renderStopRowText(stop, activityWindow) {
  const distanceEl = document.createElement('span');
  distanceEl.className = 'list-row-distance';
  distanceEl.textContent = `⏸ ${formatDistanceKm(stop.distanceFromStart)}`;

  const timeEl = document.createElement('span');
  timeEl.className = 'list-row-time';
  timeEl.textContent = rowLabel(stop, activityWindow);

  return [distanceEl, timeEl];
}

/**
 * Creates the stop list, mounted into `listEl` and rendered (empty).
 *
 * @param {object} opts
 * @param {HTMLElement} opts.listEl - `<ul>` (or similar) to render rows into
 * @param {HTMLElement} opts.emptyEl - shown while there are no stops
 * @param {import('./markerList.js').PinLayer} opts.pins - this list's pins
 *   (map.js's `createPinLayer()`), so its ids never meet the anchors'
 * @param {() => void} [opts.revealMap] - mobileLayout's, before a row pans to its pin
 */
export function createStopList({ listEl, emptyEl, pins, revealMap }) {
  const list = createMarkerList({
    listEl,
    emptyEl,
    pins,
    revealMap,
    markerKind: () => 'stop',
    deleteLabel: 'Delete stop',
    isActive: isStopActive,
    renderRowText: renderStopRowText,
  });

  return {
    /**
     * Adds a mid-route stop: assigns it an id, adds its numbered pin, and
     * re-renders the sidebar list.
     *
     * @param {object} stop
     * @param {number} stop.lat
     * @param {number} stop.lon
     * @param {number} stop.distanceFromStart - meters along the route; used for sidebar/pin ordering
     * @param {'duration'|'startEnd'} stop.mode
     * @param {number} [stop.durationSeconds] - for mode 'duration'
     * @param {Date} [stop.arrival] - for mode 'startEnd'
     * @param {Date} [stop.departure] - for mode 'startEnd'
     * @param {number} [stop.arrivalOffsetSeconds] - for an arrival entered as a duration since start
     * @param {number} [stop.departureOffsetSeconds] - for a departure entered as a duration since start
     * @returns {number} the assigned stop id
     */
    addStop({
      lat, lon, distanceFromStart, mode, durationSeconds, arrival, departure, arrivalOffsetSeconds, departureOffsetSeconds,
    }) {
      return list.add({
        lat, lon, distanceFromStart, mode, durationSeconds, arrival, departure, arrivalOffsetSeconds, departureOffsetSeconds,
      });
    },

    /** Removes every stop and its pin (e.g. before loading a new route) and re-renders the list. */
    resetStops() {
      list.reset();
    },

    /** @returns {Stop[]} a defensive copy of the current stops, in route order. */
    getStops() {
      return list.getAll();
    },

    /**
     * Registers a callback run after any stop is added, removed or cleared.
     * Stops are placed from `anchorPopovers.js` and deleted from their own row
     * button. So anything that derives a value from the stop list (the
     * start-time control's avg-speed mode, which adds the total stopped time on
     * top of the moving time) has no other way to hear about a change.
     *
     * @param {() => void} fn
     */
    setStopsChangeListener(fn) {
      list.setChangeListener(fn);
    },

    /**
     * Sets the activity's current start–end and re-checks every stop against it
     * (see markerList's `setActivityWindow`). Doesn't fire the change listener, so
     * main.js can call it from the very handler that listener triggers.
     *
     * @param {{ start: Date, end: Date }|null} nextWindow
     */
    setActivityWindow(nextWindow) {
      list.setActivityWindow(nextWindow);
    },

    /**
     * The stops to send to convert(): duration-only stops always, start/end
     * stops only while inside the current window, with their times resolved
     * against the current start.
     *
     * @returns {Stop[]}
     */
    getActiveStops() {
      const start = list.getActivityWindow()?.start ?? null;
      return list.getActive().map((stop) => (stop.mode === 'startEnd'
        ? { ...stop, ...effectiveStopTimes(stop, start) }
        : stop));
    },

    /** @returns {number} how many stops are currently left out of `getActiveStops()`. */
    countInactiveStops() {
      return list.countInactive();
    },
  };
}
