// Single source of truth for placed anchors. Renders the sidebar list and
// keeps map pins numbered/synced whenever the array changes; row <-> pin
// cross-references happen by id, in both directions.

import * as mapModule from './map.js';

let anchors = [];
let nextId = 1;
let listEl = null;
let emptyStateEl = null;

export function initAnchorList(listElement, emptyElement) {
  listEl = listElement;
  emptyStateEl = emptyElement;
  render();
}

function sortedIds() {
  return [...anchors].sort((a, b) => a.distanceFromStart - b.distanceFromStart).map((a) => a.id);
}

function formatDistance(meters) {
  return `${(meters / 1000).toFixed(2)} km`;
}

function formatTime(date) {
  return date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
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
    distanceEl.textContent = formatDistance(anchor.distanceFromStart);

    const timeEl = document.createElement('span');
    timeEl.className = 'anchor-row-time';
    timeEl.textContent = formatTime(anchor.timestamp);

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

export function addAnchor({ lat, lon, distanceFromStart, timestamp }) {
  const id = nextId++;
  anchors.push({ id, distanceFromStart, timestamp });
  mapModule.addMarker(id, lat, lon, anchors.length, {
    onClick: (clickedId) => highlightRow(clickedId),
  });
  render();
  return id;
}

export function removeAnchor(id) {
  anchors = anchors.filter((a) => a.id !== id);
  mapModule.removeMarker(id);
  render();
}

export function getAnchors() {
  return anchors.map((a) => ({ ...a }));
}

export function hasAnchors() {
  return anchors.length > 0;
}
