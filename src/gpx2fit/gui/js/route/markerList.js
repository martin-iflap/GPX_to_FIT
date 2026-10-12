// Shared behavior for a "list of things placed on the route, each with a
// synced numbered map pin" — anchors.js and stops.js are both exactly this
// shape. (Assign id, sort by distanceFromStart, sync map.js marker
// id-space, render a sidebar row with a delete button, hover/click <->
// pin highlight, and re-check every item against the activity window.) The
// only parts that differ between an anchor and a stop are what a row's text
// lines say, what marker "kind" it gets and when it counts as inside the
// window, so those are the only things callers provide.

import * as mapModule from './map.js';
import { revealMap } from '../shell/mobileLayout.js';

/** @typedef {import('./activityWindow.js').ActivityWindow} ActivityWindow */

/**
 * @param {object} opts
 * @param {number} [opts.idOffset] - starting id minus 1; keeps this list's ids
 *   from colliding with another list sharing map.js marker id-space (e.g.
 *   stops.js offsets by 1_000_000 to stay clear of anchors.js ids)
 * @param {(item: object) => 'anchor'|'stop'|'photo'} opts.markerKind - marker
 *   kind for a given item, passed to mapModule.addMarker
 * @param {string} opts.deleteAriaLabel - aria-label for each row's delete button
 * @param {(item: object, activityWindow: ActivityWindow|null) => HTMLElement[]} opts.renderRowText -
 *   builds the row's text content (typically a distance line and a time *   line) for one item, as of the current activity window
 * @param {(item: object) => HTMLElement|null} [opts.renderRowMedia] - an
 *   optional picture between the row's number and its text (a photo
 *   anchor's thumbnail), or null for none
 * @param {(item: object, activityWindow: ActivityWindow|null) => boolean} [opts.isActive] -
 *   false for an item that is kept but currently not used (e.g. an anchor
 *   outside the activity's time): its row gets `.is-inactive`, its pin is
 *   dimmed, and `getActive()` leaves it out. Re-evaluated on every render,
 *   and with every `setActivityWindow`.
 * @param {(item: object) => boolean} [opts.isDraggable] - true for an item
 *   whose pin can be dragged along the route; decided once, when it's added.
 *   A drop goes to the `setDragEndListener` listener, which decides whether
 *   the item really moves (see `move`).
 */
export function createMarkerList({
  idOffset = 0,
  markerKind,
  deleteAriaLabel,
  renderRowText,
  renderRowMedia = () => null,
  isActive = () => true,
  isDraggable = () => false,
}) {
  let items = [];
  let nextId = idOffset + 1;
  let listEl = null;
  let emptyStateEl = null;
  // The activity's current start–end, pushed in through setActivityWindow
  // (main.js forwards appState's). null until both are valid, which leaves
  // every item active.
  /** @type {ActivityWindow|null} */
  let activityWindow = null;
  // Fired on add/remove/move/reset for callers that derive something from the
  // list (the start-time control's avg-speed mode needs the total stopped
  // time). Deliberately not fired from render(), which init() also calls at
  // mount — a listener registered later would then miss it, and one
  // registered earlier would run before the caller's own state exists.
  let changeListener = null;
  // Per-item callbacks for whoever created the item and needs to hear that
  // it's gone (a photo's row). Kept off the items themselves, so they never
  // reach getAll() and, through it, convert().
  const removeCallbacks = new Map();
  let dragEndListener = null;

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

  /**
   * @param {boolean} [renumber] - false skips renumbering the pins, for a
   *   re-render that can't have changed their order (see `setActivityWindow`)
   */
  function render(renumber = true) {
    items.sort((a, b) => a.distanceFromStart - b.distanceFromStart);
    listEl.innerHTML = '';

    const inactiveIds = new Set(items.filter((item) => !isActive(item, activityWindow)).map((item) => item.id));

    items.forEach((item, index) => {
      const row = document.createElement('li');
      row.className = 'anchor-row';
      row.classList.toggle('is-inactive', inactiveIds.has(item.id));
      row.dataset.id = String(item.id);

      const info = document.createElement('div');
      info.className = 'anchor-row-info';

      const numberEl = document.createElement('span');
      numberEl.className = 'anchor-row-number';
      numberEl.textContent = String(index + 1);

      const textEl = document.createElement('div');
      textEl.className = 'anchor-row-text';
      textEl.append(...renderRowText(item, activityWindow));

      const mediaEl = renderRowMedia(item);
      info.append(...[numberEl, mediaEl, textEl].filter(Boolean));

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
      row.addEventListener('click', () => {
        // On a phone the sheet may be covering most of the map; bring it
        // back into view first, or the pan happens out of sight.
        revealMap();
        mapModule.panToMarker(item.id);
      });
      row.addEventListener('mouseenter', () => mapModule.highlightMarker(item.id, true));
      row.addEventListener('mouseleave', () => mapModule.highlightMarker(item.id, false));

      listEl.append(row);
    });

    emptyStateEl.hidden = items.length > 0;
    if (renumber) {
      mapModule.renumberMarkers(sortedIds());
    }
    // After renumbering: setIcon replaces the pin's element, class and all.
    items.forEach((item) => mapModule.setMarkerDimmed(item.id, inactiveIds.has(item.id)));
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
   * @param {{ onRemove?: () => void }} [hooks] - `onRemove` runs once the
   *   item is removed by `remove` (its row's ×, or the caller), not on `reset`.
   * @returns {number} the assigned id, for later `remove` calls
   */
  function add({ lat, lon, ...rest }, { onRemove } = {}) {
    const id = nextId++;
    const item = { id, ...rest };
    items.push(item);
    if (onRemove) {
      removeCallbacks.set(id, onRemove);
    }
    mapModule.addMarker(id, lat, lon, items.length, {
      kind: markerKind(item),
      onClick: (clickedId) => highlightRow(clickedId),
      draggable: isDraggable(item),
      onDragEnd: (droppedId, droppedLat, droppedLon, revert) => {
        const dropped = items.find((i) => i.id === droppedId);
        if (dropped && dragEndListener) {
          dragEndListener({ ...dropped }, droppedLat, droppedLon, revert);
        } else {
          revert();
        }
      },
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
    // Last, so the callback already sees the list without this item.
    const onRemove = removeCallbacks.get(id);
    removeCallbacks.delete(id);
    onRemove?.();
  }

  /**
   * Moves an item to another point on the route: its pin, its distance and
   * so its place in the numbering. Unknown ids are ignored.
   * @param {number} id
   * @param {{ lat: number, lon: number, distanceFromStart: number }} position
   */
  function move(id, { lat, lon, distanceFromStart }) {
    const item = items.find((i) => i.id === id);
    if (!item) {
      return;
    }
    item.distanceFromStart = distanceFromStart;
    mapModule.moveMarker(id, lat, lon);
    render();
    changeListener?.();
  }

  /**
   * Removes every item and its map pin (e.g. before loading a new route) and
   * re-renders the list. No `onRemove` runs: whoever registered one is being
   * reset along with the route.
   */
  function reset() {
    items.forEach((i) => mapModule.removeMarker(i.id));
    items = [];
    removeCallbacks.clear();
    render();
    changeListener?.();
  }

  /**
   * @returns {object[]} a defensive copy of the current items, in route order
   *   (`render()` keeps `items` sorted by distance from the start).
   */
  function getAll() {
    return items.map((i) => ({ ...i }));
  }

  /** Registers the single listener called after every add/remove/move/reset. */
  function setChangeListener(fn) {
    changeListener = fn;
  }

  /**
   * Registers the single listener for a draggable pin dropped somewhere
   * new. It gets a copy of the item, where the pin was dropped (already on
   * the route), and `revert()`, which puts the pin back; it should end in
   * either `move` or `revert`. Without a listener every drop reverts.
   * @param {(item: object, lat: number, lon: number, revert: () => void) => void} fn
   */
  function setDragEndListener(fn) {
    dragEndListener = fn;
  }

  /**
   * Sets the activity's current start–end and re-renders every row and pin
   * against it. An item outside is kept, shown as inactive and left out of
   * `getActive()`; moving the window back makes it active again.
   *
   * Doesn't fire the change listener: no item was added or removed, and the
   * listener's own work may be what moved the window. Nor does it renumber
   * the pins, whose order only an add/remove/move can change; that skips a
   * Leaflet `setIcon` per pin. Every call re-renders, so the caller skips an
   * unchanged window (appState's `onWindowChange` only fires on a change).
   *
   * @param {ActivityWindow|null} nextWindow
   */
  function setActivityWindow(nextWindow) {
    activityWindow = nextWindow;
    render(false);
  }

  /** @returns {ActivityWindow|null} the window last given to `setActivityWindow` */
  function getActivityWindow() {
    return activityWindow;
  }

  /** @returns {object[]} copies of the items `isActive` accepts, in route order */
  function getActive() {
    return getAll().filter((item) => isActive(item, activityWindow));
  }

  /** @returns {number} how many items `getActive()` currently leaves out */
  function countInactive() {
    return items.length - getActive().length;
  }

  return {
    init,
    add,
    remove,
    move,
    reset,
    getAll,
    getActive,
    countInactive,
    setChangeListener,
    setDragEndListener,
    setActivityWindow,
    getActivityWindow,
  };
}
