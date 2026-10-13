// Shared behavior for a "list of things placed on the route, each with a
// synced numbered map pin" — anchors.js and stops.js are both exactly this
// shape. (Assign id, sort by distanceFromStart, keep the pins in step,
// render a sidebar row with a delete button, hover/focus/click <-> pin highlight,
// and re-check every item against the activity window.) The only parts that
// differ between an anchor and a stop are what a row's text lines say, what
// pin "kind" it gets and when it counts as inside the window, so those are
// the only things callers provide.
//
// The pins come in as an adapter (map.js's createPinLayer), one per list, so
// each list has its own pin ids and nothing here touches Leaflet.

import { formatDistanceKm } from '../util/format.js';

/** @typedef {import('./activityWindow.js').ActivityWindow} ActivityWindow */

/**
 * What `add` takes for an item of type T: its fields without the id (the
 * list assigns that), plus where to put its pin. Distributes over a union
 * (a `Stop` is a duration or a start/end stop), which `Omit` alone doesn't.
 * @template T
 * @typedef {(T extends unknown ? Omit<T, 'id'> : never) & { lat: number, lon: number }} NewItem
 */

/**
 * What a list needs from the map: one layer of numbered pins, keyed by the
 * list's own ids. map.js's `createPinLayer()` returns one.
 * @typedef {{
 *   add: (id: number, lat: number, lon: number, number: number, opts: {
 *     kind: 'anchor'|'stop'|'photo',
 *     onClick: (id: number) => void,
 *     draggable: boolean,
 *     onDragEnd: (id: number, lat: number, lon: number, revert: () => void) => void,
 *   }) => void,
 *   move: (id: number, lat: number, lon: number) => void,
 *   remove: (id: number) => void,
 *   renumber: (orderedIds: number[]) => void,
 *   panTo: (id: number) => void,
 *   highlight: (id: number, isActive: boolean) => void,
 *   setDimmed: (id: number, dimmed: boolean) => void,
 * }} PinLayer
 */

/**
 * Creates the list, mounted into `listEl`, and renders it (empty).
 *
 * @template {{ id: number, distanceFromStart: number }} T - one item (an
 *   `Anchor`, a `Stop`); every callback and getter is typed with it
 * @param {object} opts
 * @param {HTMLElement} opts.listEl - `<ul>` (or similar) to render rows into
 * @param {HTMLElement} opts.emptyEl - shown while the list is empty
 * @param {PinLayer} opts.pins - this list's pins on the map
 * @param {() => void} [opts.revealMap] - brings the map into view (on a
 *   phone the sheet may cover it) before a row click pans to its pin
 * @param {(item: T) => 'anchor'|'stop'|'photo'} opts.markerKind - pin
 *   kind for a given item
 * @param {string} opts.deleteLabel - how each row's delete button starts its
 *   label ('Delete anchor'); the row's number and distance follow, so a
 *   screen reader can tell the buttons apart ('Delete anchor 3 at 4.20 km')
 * @param {(item: T, activityWindow: ActivityWindow|null) => HTMLElement[]} opts.renderRowText -
 *   builds the row's text content (typically a distance line and a time
 *   line) for one item, as of the current activity window. Phrasing
 *   elements only (`<span>`s): they go inside the row's button.
 * @param {(item: T) => HTMLElement|null} [opts.renderRowMedia] - an
 *   optional picture between the row's number and its text (a photo
 *   anchor's thumbnail), or null for none; phrasing too, like an `<img>`
 * @param {(item: T, activityWindow: ActivityWindow|null) => boolean} [opts.isActive] -
 *   false for an item that is kept but currently not used (e.g. an anchor
 *   outside the activity's time): its row gets `.is-inactive`, its pin is
 *   dimmed, and `getActive()` leaves it out. Re-evaluated on every render,
 *   and with every `setActivityWindow`.
 * @param {(item: T) => boolean} [opts.isDraggable] - true for an item
 *   whose pin can be dragged along the route; decided once, when it's added.
 *   A drop goes to the `setDragEndListener` listener, which decides whether
 *   the item really moves (see `move`).
 */
export function createMarkerList({
  listEl,
  emptyEl,
  pins,
  revealMap = () => {},
  markerKind,
  deleteLabel,
  renderRowText,
  renderRowMedia = () => null,
  isActive = () => true,
  isDraggable = () => false,
}) {
  /** @type {T[]} */
  let items = [];
  let nextId = 1;
  // The activity's current start–end, pushed in through setActivityWindow
  // (main.js forwards appState's). null until both are valid, which leaves
  // every item active.
  /** @type {ActivityWindow|null} */
  let activityWindow = null;
  // Fired on add/remove/move/reset for callers that derive something from the
  // list (the start-time control's avg-speed mode needs the total stopped
  // time). Deliberately not fired from render(), which creating the list
  // also calls: a listener registered later would then miss it.
  /** @type {(() => void)|null} */
  let changeListener = null;
  // Per-item callbacks for whoever created the item and needs to hear that
  // it's gone (a photo's row). Kept off the items themselves, so they never
  // reach getAll() and, through it, convert().
  /** @type {Map<number, () => void>} */
  const removeCallbacks = new Map();
  /** @type {((item: T, lat: number, lon: number, revert: () => void) => void)|null} */
  let dragEndListener = null;

  /** @param {number} id */
  function highlightRow(id) {
    listEl.querySelectorAll('.list-row').forEach((row) => {
      if (!(row instanceof HTMLElement)) {
        return;
      }
      row.classList.toggle('is-active', row.dataset.id === String(id));
    });
  }

  /**
   * @param {boolean} [renumber] - false skips renumbering the pins, for a
   *   re-render that can't have changed their order (see `setActivityWindow`)
   */
  function render(renumber = true) {
    // Route order (nearest the start first), which numbers the rows and pins.
    items.sort((a, b) => a.distanceFromStart - b.distanceFromStart);
    listEl.innerHTML = '';

    const inactiveIds = new Set(items.filter((item) => !isActive(item, activityWindow)).map((item) => item.id));

    items.forEach((item, index) => {
      const row = document.createElement('li');
      row.className = 'list-row';
      row.classList.toggle('is-inactive', inactiveIds.has(item.id));
      row.dataset.id = String(item.id);

      // A button, so the keyboard can pan to the pin too. Everything in it
      // is a <span> or an <img>: a button holds phrasing content only.
      const info = document.createElement('button');
      info.type = 'button';
      info.className = 'list-row-info';

      const numberEl = document.createElement('span');
      numberEl.className = 'list-row-number';
      numberEl.textContent = String(index + 1);

      const textEl = document.createElement('span');
      textEl.className = 'list-row-text';
      textEl.append(...renderRowText(item, activityWindow));

      const mediaEl = renderRowMedia(item);
      info.append(...(mediaEl ? [numberEl, mediaEl, textEl] : [numberEl, textEl]));

      const deleteBtn = document.createElement('button');
      deleteBtn.type = 'button';
      deleteBtn.className = 'list-row-delete';
      deleteBtn.setAttribute('aria-label', `${deleteLabel} ${index + 1} at ${formatDistanceKm(item.distanceFromStart)}`);
      deleteBtn.textContent = '×';
      deleteBtn.addEventListener('click', (event) => {
        event.stopPropagation();
        remove(item.id);
      });

      row.append(info, deleteBtn);
      info.addEventListener('click', () => {
        // On a phone the sheet may be covering most of the map; bring it
        // back into view first, or the pan happens out of sight.
        revealMap();
        pins.panTo(item.id);
      });
      // The pointer hovers the whole row; the keyboard focuses its button.
      row.addEventListener('mouseenter', () => pins.highlight(item.id, true));
      row.addEventListener('mouseleave', () => pins.highlight(item.id, false));
      info.addEventListener('focus', () => pins.highlight(item.id, true));
      info.addEventListener('blur', () => pins.highlight(item.id, false));

      listEl.append(row);
    });

    emptyEl.hidden = items.length > 0;
    if (renumber) {
      pins.renumber(items.map((item) => item.id));
    }
    // After renumbering: a new number replaces the pin's element, class and all.
    items.forEach((item) => pins.setDimmed(item.id, inactiveIds.has(item.id)));
  }

  /**
   * Adds an item: assigns it an id, adds a numbered pin at
   * `lat`/`lon`, and re-renders the sidebar list.
   *
   * @param {NewItem<T>} fields - all fields of the item, including `lat`/`lon`
   *   (consumed here for the pin, not stored) and `distanceFromStart`
   *   (used for sidebar/pin ordering)
   * @param {{ onRemove?: () => void }} [hooks] - `onRemove` runs once the
   *   item is removed by `remove` (its row's ×, or the caller), not on `reset`.
   * @returns {number} the assigned id, for later `remove` calls
   */
  function add({ lat, lon, ...rest }, { onRemove } = {}) {
    const id = nextId++;
    // T's own fields plus the id, which TypeScript can't prove for a generic T.
    const item = /** @type {T} */ (/** @type {unknown} */ ({ id, ...rest }));
    items.push(item);
    if (onRemove) {
      removeCallbacks.set(id, onRemove);
    }
    pins.add(id, lat, lon, items.length, {
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

  /**
   * Removes the item with the given id, its map pin, and re-renders the list.
   * @param {number} id
   */
  function remove(id) {
    items = items.filter((i) => i.id !== id);
    pins.remove(id);
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
    pins.move(id, lat, lon);
    render();
    changeListener?.();
  }

  /**
   * Removes every item and its map pin (e.g. before loading a new route) and
   * re-renders the list. No `onRemove` runs: whoever registered one is being
   * reset along with the route.
   */
  function reset() {
    items.forEach((i) => pins.remove(i.id));
    items = [];
    removeCallbacks.clear();
    render();
    changeListener?.();
  }

  /**
   * @returns {T[]} a defensive copy of the current items, in route order
   *   (`render()` keeps `items` sorted by distance from the start).
   */
  function getAll() {
    return items.map((i) => ({ ...i }));
  }

  /**
   * Registers the single listener called after every add/remove/move/reset.
   * @param {() => void} fn
   */
  function setChangeListener(fn) {
    changeListener = fn;
  }

  /**
   * Registers the single listener for a draggable pin dropped somewhere
   * new. It gets a copy of the item, where the pin was dropped (already on
   * the route), and `revert()`, which puts the pin back; it should end in
   * either `move` or `revert`. Without a listener every drop reverts.
   * @param {(item: T, lat: number, lon: number, revert: () => void) => void} fn
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

  /** @returns {T[]} copies of the items `isActive` accepts, in route order */
  function getActive() {
    return getAll().filter((item) => isActive(item, activityWindow));
  }

  /** @returns {number} how many items `getActive()` currently leaves out */
  function countInactive() {
    return items.length - getActive().length;
  }

  render();

  return {
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
