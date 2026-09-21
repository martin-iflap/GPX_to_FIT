// Shared behavior for a "list of things placed on the route, each with a
// synced numbered map pin" — anchors.js and stops.js are both exactly this
// shape. (Assign id, sort by distanceFromStart, sync map.js marker
// id-space, render a sidebar row with a delete button, hover/click <->
// pin highlight.) The only part that actually differs between an anchor
// and a stop is what a row's two text lines say and what marker "kind" it
// gets, so that's the only thing callers provide.

import * as mapModule from './map.js';

/**
 * @param {object} opts
 * @param {number} [opts.idOffset] - starting id minus 1; keeps this list's ids
 *   from colliding with another list sharing map.js marker id-space (e.g.
 *   stops.js offsets by 1_000_000 to stay clear of anchors.js ids)
 * @param {(item: object) => 'anchor'|'stop'|'photo'} opts.markerKind - marker
 *   kind for a given item, passed to mapModule.addMarker
 * @param {string} opts.deleteAriaLabel - aria-label for each row's delete button
 * @param {(item: object) => HTMLElement[]} opts.renderRowText - builds the
 *   row's text content (typically a distance line and a timeline) for one item
 */
export function createMarkerList({ idOffset = 0, markerKind, deleteAriaLabel, renderRowText }) {
  let items = [];
  let nextId = idOffset + 1;
  let listEl = null;
  let emptyStateEl = null;
  // Fired on add/remove/reset for callers that derive something from the list
  // (the start-time control's avg-speed mode needs the total stopped time).
  // Deliberately not fired from render(), which init() also calls at mount —
  // a listener registered later would then miss it, and one registered
  // earlier would run before the caller's own state exists.
  let changeListener = null;

  /** Item ids in route order (nearest-to-start first) — matches the numbering shown on the map pins and sidebar rows. */
  function sortedIds() {
    return [...items].sort((a, b) => a.distanceFromStart - b.distanceFromStart).map((i) => i.id);
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
    items.sort((a, b) => a.distanceFromStart - b.distanceFromStart);
    listEl.innerHTML = '';

    items.forEach((item, index) => {
      const row = document.createElement('li');
      row.className = 'anchor-row';
      row.dataset.id = String(item.id);

      const info = document.createElement('div');
      info.className = 'anchor-row-info';

      const numberEl = document.createElement('span');
      numberEl.className = 'anchor-row-number';
      numberEl.textContent = String(index + 1);

      const textEl = document.createElement('div');
      textEl.className = 'anchor-row-text';
      textEl.append(...renderRowText(item));

      info.append(numberEl, textEl);

      const deleteBtn = document.createElement('button');
      deleteBtn.type = 'button';
      deleteBtn.className = 'anchor-row-delete';
      deleteBtn.setAttribute('aria-label', deleteAriaLabel);
      deleteBtn.textContent = '×';
      deleteBtn.addEventListener('click', (event) => {
        event.stopPropagation();
        remove(item.id);
      });

      row.append(info, deleteBtn);
      row.addEventListener('click', () => mapModule.panToMarker(item.id));
      row.addEventListener('mouseenter', () => mapModule.highlightMarker(item.id, true));
      row.addEventListener('mouseleave', () => mapModule.highlightMarker(item.id, false));

      listEl.append(row);
    });

    emptyStateEl.hidden = items.length > 0;
    mapModule.renumberMarkers(sortedIds());
  }

  /**
   * Mounts the list into the given DOM elements and does the initial (empty)
   * render. Must be called once before `add`/`remove`.
   */
  function init(listElement, emptyElement) {
    listEl = listElement;
    emptyStateEl = emptyElement;
    render();
  }

  /**
   * Adds an item: assigns it an id, drops a numbered pin on the map at
   * `lat`/`lon`, and re-renders the sidebar list.
   *
   * @param {object} fields - all fields of the item, including `lat`/`lon`
   *   (consumed here for the pin, not stored) and `distanceFromStart`
   *   (used for sidebar/pin ordering)
   * @returns {number} the assigned id, for later `remove` calls
   */
  function add({ lat, lon, ...rest }) {
    const id = nextId++;
    const item = { id, ...rest };
    items.push(item);
    mapModule.addMarker(id, lat, lon, items.length, {
      kind: markerKind(item),
      onClick: (clickedId) => highlightRow(clickedId),
    });
    render();
    changeListener?.();
    return id;
  }

  /** Removes the item with the given id, its map pin, and re-renders the list. */
  function remove(id) {
    items = items.filter((i) => i.id !== id);
    mapModule.removeMarker(id);
    render();
    changeListener?.();
  }

  /** Removes every item and its map pin (e.g. before loading a new route) and re-renders the list. */
  function reset() {
    items.forEach((i) => mapModule.removeMarker(i.id));
    items = [];
    render();
    changeListener?.();
  }

  /** @returns {object[]} a defensive copy of the current items, in insertion order. */
  function getAll() {
    return items.map((i) => ({ ...i }));
  }

  /** Registers the single listener called after every add/remove/reset. */
  function setChangeListener(fn) {
    changeListener = fn;
  }

  return { init, add, remove, reset, getAll, setChangeListener };
}
