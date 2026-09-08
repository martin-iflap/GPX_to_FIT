// Single source of truth for placed anchors. Renders the sidebar list and
// keeps map pins numbered/synced whenever the array changes; row <-> pin
// cross-references happen by id, in both directions.

import * as mapModule from './map.js';
import { formatDateTime, formatDistanceKm } from './format.js';

/** @typedef {{ id: number, distanceFromStart: number, timestamp: Date, source: 'user'|'photo' }} Anchor */

/** @type {Anchor[]} */
let anchors = [];
let nextId = 1;
let listEl = null;
let emptyStateEl = null;

/**
 * Mounts the anchor list into the given DOM elements and does the initial
 * (empty) render. Must be called once before `addAnchor`/`removeAnchor`.
 *
 * @param {HTMLElement} listElement - `<ul>` (or similar) to render rows into
 * @param {HTMLElement} emptyElement - element toggled visible when there are no anchors
 */
export function initAnchorList(listElement, emptyElement) {
  listEl = listElement;
  emptyStateEl = emptyElement;
  render();
}

/** Anchor ids in route order (nearest-to-start first) — matches the numbering shown on the map pins and sidebar rows. */
function sortedIds() {
  return [...anchors].sort((a, b) => a.distanceFromStart - b.distanceFromStart).map((a) => a.id);
}

function highlightRow(id) {
  if (!listEl) {
    return;
  }
  listEl.querySelectorAll('.anchor-row').forEach((row) => {
    row.classList.toggle('is-active', row.dataset.id === String(id));
  });
}

function render() {
  anchors.sort((a, b) => a.distanceFromStart - b.distanceFromStart);
  listEl.innerHTML = '';

  anchors.forEach((anchor, index) => {
    const row = document.createElement('li');
    row.className = 'anchor-row';
    row.dataset.id = String(anchor.id);

    const info = document.createElement('div');
    info.className = 'anchor-row-info';

    const numberEl = document.createElement('span');
    numberEl.className = 'anchor-row-number';
    numberEl.textContent = String(index + 1);

    const textEl = document.createElement('div');
    textEl.className = 'anchor-row-text';

    const distanceEl = document.createElement('span');
    distanceEl.className = 'anchor-row-distance';
    const prefix = anchor.source === 'photo' ? '📷 ' : '';
    distanceEl.textContent = prefix + formatDistanceKm(anchor.distanceFromStart);

    const timeEl = document.createElement('span');
    timeEl.className = 'anchor-row-time';
    timeEl.textContent = formatDateTime(anchor.timestamp);

    textEl.append(distanceEl, timeEl);
    info.append(numberEl, textEl);

    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'anchor-row-delete';
    deleteBtn.setAttribute('aria-label', 'Delete anchor');
    deleteBtn.textContent = '×';
    deleteBtn.addEventListener('click', (event) => {
      event.stopPropagation();
      removeAnchor(anchor.id);
    });

    row.append(info, deleteBtn);
    row.addEventListener('click', () => mapModule.panToMarker(anchor.id));
    row.addEventListener('mouseenter', () => mapModule.highlightMarker(anchor.id, true));
    row.addEventListener('mouseleave', () => mapModule.highlightMarker(anchor.id, false));

    listEl.append(row);
  });

  emptyStateEl.hidden = anchors.length > 0;
  mapModule.renumberMarkers(sortedIds());
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
  const id = nextId++;
  anchors.push({ id, distanceFromStart, timestamp, source });
  mapModule.addMarker(id, lat, lon, anchors.length, {
    kind: source === 'photo' ? 'photo' : 'anchor',
    onClick: (clickedId) => highlightRow(clickedId),
  });
  render();
  return id;
}

/** Removes the anchor with the given id, its map pin, and re-renders the list. */
export function removeAnchor(id) {
  anchors = anchors.filter((a) => a.id !== id);
  mapModule.removeMarker(id);
  render();
}

/** @returns {Anchor[]} a defensive copy of the current anchors, in insertion order. */
export function getAnchors() {
  return anchors.map((a) => ({ ...a }));
}
