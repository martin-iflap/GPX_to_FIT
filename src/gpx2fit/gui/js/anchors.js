// Single source of truth for placed anchors — a known timestamp at some
// distance along the route. All list/pin/id bookkeeping lives in
// markerList.js; this module only knows an anchor's shape and how to
// render its row text.

import { createMarkerList } from './markerList.js';
import { formatDateTime, formatDistanceKm } from './format.js';

/** @typedef {{ id: number, distanceFromStart: number, timestamp: Date, source: 'user'|'photo' }} Anchor */

const list = createMarkerList({
  markerKind: (anchor) => (anchor.source === 'photo' ? 'photo' : 'anchor'),
  deleteAriaLabel: 'Delete anchor',
  renderRowText: (anchor) => {
    const distanceEl = document.createElement('span');
    distanceEl.className = 'anchor-row-distance';
    const prefix = anchor.source === 'photo' ? '📷 ' : '';
    distanceEl.textContent = prefix + formatDistanceKm(anchor.distanceFromStart);

    const timeEl = document.createElement('span');
    timeEl.className = 'anchor-row-time';
    timeEl.textContent = formatDateTime(anchor.timestamp);

    return [distanceEl, timeEl];
  },
});

/**
 * Mounts the anchor list into the given DOM elements and does the initial
 * (empty) render. Must be called once before `addAnchor`/`removeAnchor`.
 *
 * @param {HTMLElement} listElement - `<ul>` (or similar) to render rows into
 * @param {HTMLElement} emptyElement - element toggled visible when there are no anchors
 */
export function initAnchorList(listElement, emptyElement) {
  list.init(listElement, emptyElement);
}

/**
 * Adds a mid-route anchor: assigns it an id, drops a numbered pin on the
 * map, and re-renders the sidebar list.
 *
 * @param {object} anchor
 * @param {number} anchor.lat
 * @param {number} anchor.lon
 * @param {number} anchor.distanceFromStart - meters along the route; used for sidebar/pin ordering
 * @param {Date} anchor.timestamp - resolved arrival time at this point
 * @param {'user'|'photo'} [anchor.source] - how this anchor was created; defaults to 'user' (map-click/manual)
 * @returns {number} the assigned anchor id, for later `removeAnchor` calls
 */
export function addAnchor({ lat, lon, distanceFromStart, timestamp, source = 'user' }) {
  return list.add({ lat, lon, distanceFromStart, timestamp, source });
}

/** Removes the anchor with the given id, its map pin, and re-renders the list. */
export function removeAnchor(id) {
  list.remove(id);
}

/** Removes every anchor and its map pin (e.g. before loading a new route) and re-renders the list. */
export function resetAnchors() {
  list.reset();
}

/** @returns {Anchor[]} a defensive copy of the current anchors, in insertion order. */
export function getAnchors() {
  return list.getAll();
}
