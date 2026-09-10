// Single source of truth for placed stops — a real pause at one location,
// entered either as a duration only or as an explicit start/end time. All
// list/pin/id bookkeeping lives in markerList.js; this module only knows a
// stop's shape and how to render its row text.

import { createMarkerList } from './markerList.js';
import { formatClock, formatDistanceKm, formatDuration } from './format.js';

/** @typedef {{ id: number, distanceFromStart: number, mode: 'duration'|'startEnd', durationSeconds?: number, arrival?: Date, departure?: Date }} Stop */

// Stops share map.js's single marker id-space with anchors.js; this offset
// keeps stop ids from ever colliding with anchor ids (which start at 1 and
// grow by ordinary placement counts, never anywhere near this range).
const ID_OFFSET = 1_000_000;

function rowLabel(stop) {
  return stop.mode === 'duration'
    ? formatDuration(stop.durationSeconds)
    : `${formatClock(stop.arrival)}–${formatClock(stop.departure)}`;
}

const list = createMarkerList({
  idOffset: ID_OFFSET,
  markerKind: () => 'stop',
  deleteAriaLabel: 'Delete stop',
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
 * @returns {number} the assigned stop id, for later `removeStop` calls
 */
export function addStop({ lat, lon, distanceFromStart, mode, durationSeconds, arrival, departure }) {
  return list.add({ lat, lon, distanceFromStart, mode, durationSeconds, arrival, departure });
}

/** Removes the stop with the given id, its map pin, and re-renders the list. */
export function removeStop(id) {
  list.remove(id);
}

/** Removes every stop and its map pin (e.g. before loading a new route) and re-renders the list. */
export function resetStops() {
  list.reset();
}

/** @returns {Stop[]} a defensive copy of the current stops, in insertion order. */
export function getStops() {
  return list.getAll();
}
