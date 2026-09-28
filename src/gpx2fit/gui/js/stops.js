// Single source of truth for placed stops — a real pause at one location,
// entered either as a duration only or as an explicit start/end time. All
// list/pin/id bookkeeping lives in markerList.js; this module only knows a
// stop's shape, how to render its row text, and whether it still fits the
// activity's current time window.

import { createMarkerList } from './markerList.js';
import { effectiveStopTimes, isStopActive, sameWindow } from './activityWindow.js';
import { formatClock, formatDistanceKm, formatDuration } from './format.js';

/**
 * @typedef {{ id: number, distanceFromStart: number, mode: 'duration'|'startEnd', durationSeconds?: number, arrival?: Date, departure?: Date, arrivalOffsetSeconds?: number, departureOffsetSeconds?: number }} Stop
 * `arrivalOffsetSeconds`/`departureOffsetSeconds` are set for a side entered
 * as "Duration since start", which then follows the start (see anchors.js).
 */

// Stops share map.js single marker id-space with anchors.js; this offset
// keeps stop ids from ever colliding with anchor ids (which start at 1 and
// grow by ordinary placement counts, never anywhere near this range).
const ID_OFFSET = 1_000_000;

// The activity's current start–end, pushed in by main.js; see anchors.js.
let activityWindow = null;

function rowLabel(stop) {
  if (stop.mode === 'duration') {
    return formatDuration(stop.durationSeconds);
  }
  const { arrival, departure } = effectiveStopTimes(stop, activityWindow?.start ?? null);
  const label = `${formatClock(arrival)}–${formatClock(departure)}`;
  return isStopActive(stop, activityWindow) ? label : `${label} — outside the activity's time now, not used`;
}

const list = createMarkerList({
  idOffset: ID_OFFSET,
  markerKind: () => 'stop',
  deleteAriaLabel: 'Delete stop',
  isInactive: (stop) => !isStopActive(stop, activityWindow),
  renderRowText: (stop) => {
    const distanceEl = document.createElement('span');
    distanceEl.className = 'anchor-row-distance';
    distanceEl.textContent = `⏸ ${formatDistanceKm(stop.distanceFromStart)}`;

    const timeEl = document.createElement('span');
    timeEl.className = 'anchor-row-time';
    timeEl.textContent = rowLabel(stop);

    return [distanceEl, timeEl];
  },
});

/**
 * Mounts the stop list into the given DOM elements and does the initial
 * (empty) render. Must be called once before `addStop`/`removeStop`.
 *
 * @param {HTMLElement} listElement - `<ul>` (or similar) to render rows into
 * @param {HTMLElement} emptyElement - element toggled visible when there are no stops
 */
export function initStopList(listElement, emptyElement) {
  list.init(listElement, emptyElement);
}

/**
 * Adds a mid-route stop: assigns it an id, drops a numbered pin on the map,
 * and re-renders the sidebar list.
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
 * @returns {number} the assigned stop id, for later `removeStop` calls
 */
export function addStop({
  lat, lon, distanceFromStart, mode, durationSeconds, arrival, departure, arrivalOffsetSeconds, departureOffsetSeconds,
}) {
  return list.add({
    lat, lon, distanceFromStart, mode, durationSeconds, arrival, departure, arrivalOffsetSeconds, departureOffsetSeconds,
  });
}

/** Removes the stop with the given id, its map pin, and re-renders the list. */
export function removeStop(id) {
  list.remove(id); // if this function is not needed remove it.
}

/** Removes every stop and its map pin (e.g. before loading a new route) and re-renders the list. */
export function resetStops() {
  list.reset();
}

/** @returns {Stop[]} a defensive copy of the current stops, in insertion order. */
export function getStops() {
  return list.getAll();
}

/**
 * Registers a callback run after any stop is added, removed or cleared.
 * Stops are placed from `anchorPopovers.js` and deleted from their own row
 * button. So anything that derives a value from the stop list (the
 * start-time control's avg-speed mode, which adds the total stopped time on
 * top of the moving time) has no other way to hear about a change.
 *
 * @param {() => void} fn
 */
export function setStopsChangeListener(fn) {
  list.setChangeListener(fn);
}

/**
 * Sets the activity's current start–end and re-checks every stop against it
 * (see anchors.js's setActivityWindow). Doesn't fire the change listener, so
 * main.js can call it from the very handler that listener triggers.
 *
 * A window equal to the current one is a no-op, so an unrelated start-time
 * change (a sport switch, an added stop) doesn't re-render the list.
 *
 * @param {{ start: Date, end: Date }|null} window
 */
export function setActivityWindow(window) {
  if (sameWindow(activityWindow, window)) {
    return;
  }
  activityWindow = window;
  list.refresh();
}

/**
 * The stops to send to convert(): duration-only stops always, start/end
 * stops only while inside the current window, with their times resolved
 * against the current start.
 *
 * @returns {(*|{arrival: Date, departure: Date}|Object)[]}
 */
export function getActiveStops() {
  return list.getAll()
    .filter((stop) => isStopActive(stop, activityWindow))
    .map((stop) => (stop.mode === 'startEnd'
      ? { ...stop, ...effectiveStopTimes(stop, activityWindow?.start ?? null) }
      : stop));
}

/** @returns {number} how many stops are currently left out of `getActiveStops()`. */
export function countInactiveStops() {
  return list.getAll().filter((stop) => !isStopActive(stop, activityWindow)).length;
}
