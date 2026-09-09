// Single source of truth for placed stops — a real pause at one location,
// entered either as a duration only or as an explicit start/end time. Mirrors
// anchors.js's responsibilities (sidebar list + map pin sync by id), kept as
// a separate module/list since a stop's data shape and rendering differ from
// a plain anchor's.

import * as mapModule from './map.js';
import { formatClock, formatDistanceKm, formatDuration } from './format.js';

/** @typedef {{ id: number, distanceFromStart: number, mode: 'duration'|'startEnd', durationSeconds?: number, arrival?: Date, departure?: Date }} Stop */

// Stops share map.js's single marker id-space with anchors.js; this offset
// keeps stop ids from ever colliding with anchor ids (which start at 1 and
// grow by ordinary placement counts, never anywhere near this range).
const ID_OFFSET = 1_000_000;

/** @type {Stop[]} */
let stops = [];
let nextId = ID_OFFSET + 1;
let listEl = null;
let emptyStateEl = null;

/**
 * Mounts the stop list into the given DOM elements and does the initial
 * (empty) render. Must be called once before `addStop`/`removeStop`.
 *
 * @param {HTMLElement} listElement - `<ul>` (or similar) to render rows into
 * @param {HTMLElement} emptyElement - element toggled visible when there are no stops
 */
export function initStopList(listElement, emptyElement) {
  listEl = listElement;
  emptyStateEl = emptyElement;
  render();
}

/** Stop ids in route order (nearest-to-start first) — matches the numbering shown on the map pins and sidebar rows. */
function sortedIds() {
  return [...stops].sort((a, b) => a.distanceFromStart - b.distanceFromStart).map((s) => s.id);
}

function highlightRow(id) {
  if (!listEl) {
    return;
  }
  listEl.querySelectorAll('.anchor-row').forEach((row) => {
    row.classList.toggle('is-active', row.dataset.id === String(id));
  });
}

function rowLabel(stop) {
  return stop.mode === 'duration'
    ? formatDuration(stop.durationSeconds)
    : `${formatClock(stop.arrival)}–${formatClock(stop.departure)}`;
}

function render() {
  stops.sort((a, b) => a.distanceFromStart - b.distanceFromStart);
  listEl.innerHTML = '';

  stops.forEach((stop, index) => {
    const row = document.createElement('li');
    row.className = 'anchor-row';
    row.dataset.id = String(stop.id);

    const info = document.createElement('div');
    info.className = 'anchor-row-info';

    const numberEl = document.createElement('span');
    numberEl.className = 'anchor-row-number';
    numberEl.textContent = String(index + 1);

    const textEl = document.createElement('div');
    textEl.className = 'anchor-row-text';

    const distanceEl = document.createElement('span');
    distanceEl.className = 'anchor-row-distance';
    distanceEl.textContent = `⏸ ${formatDistanceKm(stop.distanceFromStart)}`;

    const timeEl = document.createElement('span');
    timeEl.className = 'anchor-row-time';
    timeEl.textContent = rowLabel(stop);

    textEl.append(distanceEl, timeEl);
    info.append(numberEl, textEl);

    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'anchor-row-delete';
    deleteBtn.setAttribute('aria-label', 'Delete stop');
    deleteBtn.textContent = '×';
    deleteBtn.addEventListener('click', (event) => {
      event.stopPropagation();
      removeStop(stop.id);
    });

    row.append(info, deleteBtn);
    row.addEventListener('click', () => mapModule.panToMarker(stop.id));
    row.addEventListener('mouseenter', () => mapModule.highlightMarker(stop.id, true));
    row.addEventListener('mouseleave', () => mapModule.highlightMarker(stop.id, false));

    listEl.append(row);
  });

  emptyStateEl.hidden = stops.length > 0;
  mapModule.renumberMarkers(sortedIds());
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
  const id = nextId++;
  stops.push({ id, distanceFromStart, mode, durationSeconds, arrival, departure });
  mapModule.addMarker(id, lat, lon, stops.length, {
    kind: 'stop',
    onClick: (clickedId) => highlightRow(clickedId),
  });
  render();
  return id;
}

/** Removes the stop with the given id, its map pin, and re-renders the list. */
export function removeStop(id) {
  stops = stops.filter((s) => s.id !== id);
  mapModule.removeMarker(id);
  render();
}

/** Removes every stop and its map pin (e.g. before loading a new route) and re-renders the list. */
export function resetStops() {
  stops.forEach((s) => mapModule.removeMarker(s.id));
  stops = [];
  render();
}

/** @returns {Stop[]} a defensive copy of the current stops, in insertion order. */
export function getStops() {
  return stops.map((s) => ({ ...s }));
}
