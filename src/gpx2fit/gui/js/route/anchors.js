// The placed anchors — a known timestamp at some distance along the route.
// All list/pin/id bookkeeping lives in markerList.js; this module only knows
// an anchor's shape, how to render its row text, and whether it still fits
// the activity's current time window. main.js creates the one list and hands
// it to whoever reads or adds anchors.

import { createMarkerList } from './markerList.js';
import { effectiveAnchorTime, isAnchorActive } from './activityWindow.js';
import { formatDateTime, formatDistanceKm } from '../util/format.js';

/**
 * @typedef {{ id: number, distanceFromStart: number, timestamp: Date, offsetSeconds?: number, source: 'user'|'photo', thumbnailUrl?: string|null }} Anchor
 * `offsetSeconds` is set for an anchor entered as "Duration since start":
 * its time then follows the start. Otherwise, `timestamp` is a fixed clock
 * time (a time of day, or a photo's capture time). `thumbnailUrl` is a
 * photo anchor's picture, for display only: photoAnchors.js owns the object
 * URL and releases it when the photo goes, which also removes the anchor.
 */

/** A photo anchor's thumbnail for its row, or null (no picture: the row says 📷 instead). */
function photoThumbnail(anchor) {
  if (anchor.source !== 'photo' || !anchor.thumbnailUrl) {
    return null;
  }
  const img = document.createElement('img');
  img.className = 'photo-thumb';
  img.alt = '';
  img.src = anchor.thumbnailUrl;
  return img;
}

function renderAnchorRowText(anchor, activityWindow) {
  const distanceEl = document.createElement('span');
  distanceEl.className = 'list-row-distance';
  const prefix = anchor.source === 'photo' && !anchor.thumbnailUrl ? '📷 ' : '';
  distanceEl.textContent = prefix + formatDistanceKm(anchor.distanceFromStart);

  const timeEl = document.createElement('span');
  timeEl.className = 'list-row-time';
  const time = formatDateTime(effectiveAnchorTime(anchor, activityWindow?.start ?? null));
  timeEl.textContent = isAnchorActive(anchor, activityWindow)
    ? time
    : `${time} — outside the activity's time now, not used`;

  return [distanceEl, timeEl];
}

/**
 * Creates the anchor list, mounted into `listEl` and rendered (empty).
 *
 * @param {object} opts
 * @param {HTMLElement} opts.listEl - `<ul>` (or similar) to render rows into
 * @param {HTMLElement} opts.emptyEl - shown while there are no anchors
 * @param {import('./markerList.js').PinLayer} opts.pins - this list's pins
 *   (map.js's `createPinLayer()`)
 * @param {() => void} [opts.revealMap] - mobileLayout's, before a row pans to its pin
 */
export function createAnchorList({ listEl, emptyEl, pins, revealMap }) {
  const list = createMarkerList({
    listEl,
    emptyEl,
    pins,
    revealMap,
    markerKind: (anchor) => (anchor.source === 'photo' ? 'photo' : 'anchor'),
    deleteLabel: 'Delete anchor',
    isActive: isAnchorActive,
    // A photo's time is fixed, but where it was taken can be off (phone GPS,
    // or a spot tapped by hand), so a photo pin can be dragged along the route.
    isDraggable: (anchor) => anchor.source === 'photo',
    renderRowMedia: photoThumbnail,
    renderRowText: renderAnchorRowText,
  });

  return {
    /**
     * Adds a mid-route anchor: assigns it an id, adds its numbered pin, and
     * re-renders the sidebar list.
     *
     * @param {object} anchor
     * @param {number} anchor.lat
     * @param {number} anchor.lon
     * @param {number} anchor.distanceFromStart - meters along the route; used for sidebar/pin ordering
     * @param {Date} anchor.timestamp - resolved arrival time at this point
     * @param {number} [anchor.offsetSeconds] - seconds after the start, for an
     *   anchor entered as a duration; makes its time follow later start changes
     * @param {'user'|'photo'} [anchor.source] - how this anchor was created; defaults to 'user' (map-click/manual)
     * @param {string|null} [anchor.thumbnailUrl] - a photo anchor's picture, shown
     *   on its row and on the "Move photo?" card; never sent to convert()
     * @param {{ onRemove?: () => void }} [hooks] - `onRemove` runs when this
     *   anchor is deleted from its row's × (not on `resetAnchors`):
     *   photoAnchors.js uses it to drop the photo along with it
     * @returns {number} the assigned anchor id
     */
    addAnchor({ lat, lon, distanceFromStart, timestamp, offsetSeconds, source = 'user', thumbnailUrl }, hooks = {}) {
      return list.add({ lat, lon, distanceFromStart, timestamp, offsetSeconds, source, thumbnailUrl }, hooks);
    },

    /**
     * Moves an anchor to another point on the route, keeping its time.
     * @param {number} id
     * @param {{ lat: number, lon: number, distanceFromStart: number }} position
     */
    moveAnchor(id, position) {
      list.move(id, position);
    },

    /**
     * Registers what happens when a draggable (photo) pin is dropped: see
     * markerList's `setDragEndListener`. anchorPopovers.js asks the user to
     * confirm before calling `moveAnchor`.
     * @param {(anchor: Anchor, lat: number, lon: number, revert: () => void) => void} fn
     */
    setAnchorDragEndListener(fn) {
      list.setDragEndListener(fn);
    },

    /** Removes every anchor and its pin (e.g. before loading a new route) and re-renders the list. */
    resetAnchors() {
      list.reset();
    },

    /**
     * Sets the activity's current start–end and re-checks every anchor against
     * it (see markerList's `setActivityWindow`). An anchor outside is kept,
     * shown as inactive and left out of `getActiveAnchors()`.
     *
     * @param {{ start: Date, end: Date }|null} nextWindow - null while the start or
     *   duration isn't valid, which leaves every anchor active
     */
    setActivityWindow(nextWindow) {
      list.setActivityWindow(nextWindow);
    },

    /**
     * The anchors to send to convert(): only those inside the current window,
     * each with `timestamp` resolved against the current start.
     *
     * @returns {Anchor[]}
     */
    getActiveAnchors() {
      const start = list.getActivityWindow()?.start ?? null;
      return list.getActive().map((anchor) => ({ ...anchor, timestamp: effectiveAnchorTime(anchor, start) }));
    },

    /** @returns {number} how many anchors are currently left out of `getActiveAnchors()`. */
    countInactiveAnchors() {
      return list.countInactive();
    },
  };
}
