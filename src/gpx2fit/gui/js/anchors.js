// Single source of truth for placed anchors — a known timestamp at some
// distance along the route. All list/pin/id bookkeeping lives in
// markerList.js; this module only knows an anchor's shape, how to render its
// row text, and whether it still fits the activity's current time window.

import { createMarkerList } from './markerList.js';
import { effectiveAnchorTime, isAnchorActive, sameWindow } from './activityWindow.js';
import { formatDateTime, formatDistanceKm } from './format.js';

/**
 * @typedef {{ id: number, distanceFromStart: number, timestamp: Date, offsetSeconds?: number, source: 'user'|'photo' }} Anchor
 * `offsetSeconds` is set for an anchor entered as "Duration since start":
 * its time then follows the start. Otherwise, `timestamp` is a fixed clock
 * time (a time of day, or a photo's capture time).
 */

// The activity's current start–end, pushed in by main.js whenever the start
// time or duration changes. null until both are valid.
let activityWindow = null;

const list = createMarkerList({
  markerKind: (anchor) => (anchor.source === 'photo' ? 'photo' : 'anchor'),
  deleteAriaLabel: 'Delete anchor',
  isInactive: (anchor) => !isAnchorActive(anchor, activityWindow),
  renderRowText: (anchor) => {
    const distanceEl = document.createElement('span');
    distanceEl.className = 'anchor-row-distance';
    const prefix = anchor.source === 'photo' ? '📷 ' : '';
    distanceEl.textContent = prefix + formatDistanceKm(anchor.distanceFromStart);

    const timeEl = document.createElement('span');
    timeEl.className = 'anchor-row-time';
    const time = formatDateTime(effectiveAnchorTime(anchor, activityWindow?.start ?? null));
    timeEl.textContent = isAnchorActive(anchor, activityWindow)
      ? time
      : `${time} — outside the activity's time now, not used`;

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
 * @param {number} [anchor.offsetSeconds] - seconds after the start, for an
 *   anchor entered as a duration; makes its time follow later start changes
 * @param {'user'|'photo'} [anchor.source] - how this anchor was created; defaults to 'user' (map-click/manual)
 * @returns {number} the assigned anchor id, for later `removeAnchor` calls
 */
export function addAnchor({ lat, lon, distanceFromStart, timestamp, offsetSeconds, source = 'user' }) {
  return list.add({ lat, lon, distanceFromStart, timestamp, offsetSeconds, source });
}

/** Removes the anchor with the given id, its map pin, and re-renders the list. */
export function removeAnchor(id) {
  list.remove(id);
}

/** Removes every anchor and its map pin (e.g. before loading a new route) and re-renders the list. */
export function resetAnchors() {
  list.reset();
}


/**
 * Sets the activity's current start–end and re-checks every anchor against
 * it. An anchor outside is kept, shown as inactive and left out of
 * `getActiveAnchors()`; moving the window back makes it active again.
 *
 * A window equal to the current one is a no-op, so an unrelated start-time
 * change (a sport switch, an added stop) doesn't re-render the list.
 *
 * @param {{ start: Date, end: Date }|null} window - null while the start or
 *   duration isn't valid, which leaves every anchor active
 */
export function setActivityWindow(window) {
  if (sameWindow(activityWindow, window)) {
    return;
  }
  activityWindow = window;
  list.refresh();
}

/**
 * The anchors to send to convert(): only those inside the current window,
 * each with `timestamp` resolved against the current start.
 *
 * @returns {(*&{timestamp: Date})[]}
 */
export function getActiveAnchors() {
  return list.getAll()
    .filter((anchor) => isAnchorActive(anchor, activityWindow))
    .map((anchor) => ({ ...anchor, timestamp: effectiveAnchorTime(anchor, activityWindow?.start ?? null) }));
}

/** @returns {number} how many anchors are currently left out of `getActiveAnchors()`. */
export function countInactiveAnchors() {
  return list.getAll().filter((anchor) => !isAnchorActive(anchor, activityWindow)).length;
}
