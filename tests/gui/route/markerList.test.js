// Uses node:test's built-in module mocking (`mock.module`) to stub out
// map.js's Leaflet-backed exports, so createMarkerList's ordering/id-assignment
// logic can be tested without a real map. Requires running Node with
// --experimental-test-module-mocks (see the "test" script in package.json).

import assert from 'node:assert/strict';
import { beforeEach, describe, it, mock } from 'node:test';

import '../testUtils/domSetup.js';

// Records the order of calls across mocks, which per-mock call lists can't show.
const mapCallLog = [];

const mapMocks = {
  addMarker: mock.fn(),
  removeMarker: mock.fn(),
  moveMarker: mock.fn(),
  renumberMarkers: mock.fn(() => mapCallLog.push('renumberMarkers')),
  panToMarker: mock.fn(),
  highlightMarker: mock.fn(),
  setMarkerDimmed: mock.fn(() => mapCallLog.push('setMarkerDimmed')),
};

mock.module('../../../src/gpx2fit/gui/js/route/map.js', { namedExports: mapMocks });

const { createMarkerList } = await import('../../../src/gpx2fit/gui/js/route/markerList.js');

function mount({ idOffset, isActive, isDraggable, renderRowText = () => [], renderRowMedia } = {}) {
  const list = createMarkerList({
    idOffset,
    markerKind: () => 'anchor',
    deleteAriaLabel: 'Remove',
    renderRowText,
    renderRowMedia,
    isActive,
    isDraggable,
  });
  const listEl = document.createElement('ul');
  const emptyEl = document.createElement('p');
  document.body.append(listEl, emptyEl);
  list.init(listEl, emptyEl);
  return { list, listEl, emptyEl };
}

beforeEach(() => {
  for (const fn of Object.values(mapMocks)) {
    fn.mock.resetCalls();
  }
});

describe('createMarkerList', () => {
  it('assigns strictly increasing ids starting at idOffset + 1', () => {
    const { list } = mount({ idOffset: 1_000_000 });
    const id1 = list.add({ lat: 1, lon: 1, distanceFromStart: 0 });
    const id2 = list.add({ lat: 2, lon: 2, distanceFromStart: 10 });
    assert.equal(id1, 1_000_001);
    assert.equal(id2, 1_000_002);
  });

  it('orders items by distanceFromStart regardless of insertion order', () => {
    const { list } = mount();
    const later = list.add({ lat: 1, lon: 1, distanceFromStart: 500 });
    const earlier = list.add({ lat: 2, lon: 2, distanceFromStart: 100 });
    const middle = list.add({ lat: 3, lon: 3, distanceFromStart: 300 });

    const all = list.getAll();
    assert.deepEqual(all.map((i) => i.id), [earlier, middle, later]);
  });

  it('keeps stable relative order for a tie in distanceFromStart', () => {
    const { list } = mount();
    const first = list.add({ lat: 1, lon: 1, distanceFromStart: 100 });
    const second = list.add({ lat: 2, lon: 2, distanceFromStart: 100 });

    assert.deepEqual(list.getAll().map((i) => i.id), [first, second]);
  });

  it('calls renumberMarkers with ids in distance order after add and remove', () => {
    const { list } = mount();
    const later = list.add({ lat: 1, lon: 1, distanceFromStart: 500 });
    const earlier = list.add({ lat: 2, lon: 2, distanceFromStart: 100 });

    const lastCall = mapMocks.renumberMarkers.mock.calls.at(-1);
    assert.deepEqual(lastCall.arguments[0], [earlier, later]);

    list.remove(later);
    const afterRemoveCall = mapMocks.renumberMarkers.mock.calls.at(-1);
    assert.deepEqual(afterRemoveCall.arguments[0], [earlier]);
    assert.equal(mapMocks.removeMarker.mock.calls.at(-1).arguments[0], later);
  });

  it('remove drops only the targeted item', () => {
    const { list } = mount();
    const a = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
    const b = list.add({ lat: 2, lon: 2, distanceFromStart: 20 });
    list.remove(a);
    assert.deepEqual(list.getAll().map((i) => i.id), [b]);
  });

  it('reset clears every item and its map pin', () => {
    const { list } = mount();
    const a = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
    const b = list.add({ lat: 2, lon: 2, distanceFromStart: 20 });
    list.reset();
    assert.deepEqual(list.getAll(), []);
    const removedIds = mapMocks.removeMarker.mock.calls.map((c) => c.arguments[0]);
    assert.ok(removedIds.includes(a));
    assert.ok(removedIds.includes(b));
  });

  it('puts an item\'s picture between its number and its text, and nothing for an item without one', () => {
    const { list, listEl } = mount({
      renderRowText: () => [Object.assign(document.createElement('span'), { className: 'text' })],
      renderRowMedia: (item) => (item.picture ? Object.assign(document.createElement('img'), { className: 'pic' }) : null),
    });
    const withPicture = list.add({ lat: 1, lon: 1, distanceFromStart: 10, picture: true });
    const without = list.add({ lat: 2, lon: 2, distanceFromStart: 20 });

    const classesOf = (id) => [...listEl.querySelector(`.anchor-row[data-id="${id}"] .anchor-row-info`).children]
      .map((el) => el.className);
    assert.deepEqual(classesOf(withPicture), ['anchor-row-number', 'pic', 'anchor-row-text']);
    assert.deepEqual(classesOf(without), ['anchor-row-number', 'anchor-row-text']);
  });

  it('getAll returns a defensive copy', () => {
    const { list } = mount();
    list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
    const snapshot = list.getAll();
    snapshot[0].distanceFromStart = 999;
    assert.equal(list.getAll()[0].distanceFromStart, 10);
  });

  describe('inactive items', () => {
    function rowFor(listEl, id) {
      return listEl.querySelector(`.anchor-row[data-id="${id}"]`);
    }

    function lastDimmedState(id) {
      const call = mapMocks.setMarkerDimmed.mock.calls.findLast((c) => c.arguments[0] === id);
      return call ? call.arguments[1] : undefined;
    }

    it('marks only the inactive item\'s row and dims only its pin', () => {
      const { list, listEl } = mount({ isActive: (item) => item.distanceFromStart <= 100 });
      const active = list.add({ lat: 1, lon: 1, distanceFromStart: 50 });
      const inactive = list.add({ lat: 2, lon: 2, distanceFromStart: 200 });

      assert.equal(rowFor(listEl, active).classList.contains('is-inactive'), false);
      assert.equal(rowFor(listEl, inactive).classList.contains('is-inactive'), true);
      assert.equal(lastDimmedState(active), false);
      assert.equal(lastDimmedState(inactive), true);
    });

    it('dims after renumbering, since renumbering replaces the pin icon', () => {
      const { list } = mount({ isActive: () => false });
      mapCallLog.length = 0;
      list.add({ lat: 1, lon: 1, distanceFromStart: 50 });

      const lastRenumber = mapCallLog.lastIndexOf('renumberMarkers');
      const lastDim = mapCallLog.lastIndexOf('setMarkerDimmed');
      assert.notEqual(lastRenumber, -1);
      assert.ok(lastDim > lastRenumber, `expected dim after renumber, got ${mapCallLog.join(' → ')}`);
    });

    // An item is active while the window holds its `time`.
    const AWAY = { start: new Date(2000), end: new Date(3000) };
    const AROUND = { start: new Date(0), end: new Date(2000) };
    const inWindow = (item, activityWindow) => !activityWindow
      || (activityWindow.start < item.time && item.time < activityWindow.end);

    it('setActivityWindow re-checks every item: inactive, then active again', () => {
      const { list, listEl } = mount({ isActive: inWindow });
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 50, time: new Date(1000) });
      assert.equal(rowFor(listEl, id).classList.contains('is-inactive'), false);

      list.setActivityWindow(AWAY);
      assert.equal(rowFor(listEl, id).classList.contains('is-inactive'), true);
      assert.equal(lastDimmedState(id), true);

      list.setActivityWindow(AROUND);
      assert.equal(rowFor(listEl, id).classList.contains('is-inactive'), false);
      assert.equal(lastDimmedState(id), false);
    });

    it('getActive and countInactive follow the window', () => {
      const { list } = mount({ isActive: inWindow });
      const early = list.add({ lat: 1, lon: 1, distanceFromStart: 50, time: new Date(1000) });
      list.add({ lat: 2, lon: 2, distanceFromStart: 60, time: new Date(2500) });

      list.setActivityWindow(AROUND);
      assert.deepEqual(list.getActive().map((i) => i.id), [early]);
      assert.equal(list.countInactive(), 1);
      assert.equal(list.getActivityWindow(), AROUND);

      list.setActivityWindow(null);
      assert.equal(list.getActive().length, 2);
      assert.equal(list.countInactive(), 0);
    });

    it('getActive returns copies', () => {
      const { list } = mount();
      list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      list.getActive()[0].distanceFromStart = 999;
      assert.equal(list.getAll()[0].distanceFromStart, 10);
    });

    it('setActivityWindow leaves pin numbering alone: distances, and so the order, cannot have changed', () => {
      const { list } = mount({ isActive: inWindow });
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 50, time: new Date(1000) });
      mapMocks.renumberMarkers.mock.resetCalls();
      mapMocks.setMarkerDimmed.mock.resetCalls();

      list.setActivityWindow(AWAY);
      assert.equal(mapMocks.renumberMarkers.mock.callCount(), 0);
      // The pin's dimming still follows, on the icon it already has.
      assert.equal(lastDimmedState(id), true);
    });

    it('renders the row text against the window it was given', () => {
      const { list, listEl } = mount({
        renderRowText: (item, activityWindow) => {
          const el = document.createElement('span');
          el.className = 'text';
          el.textContent = activityWindow ? `from ${activityWindow.start.getTime()}` : 'no window';
          return [el];
        },
      });
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 50 });
      const text = () => rowFor(listEl, id).querySelector('.text').textContent;
      assert.equal(text(), 'no window');
      list.setActivityWindow(AWAY);
      assert.equal(text(), 'from 2000');
    });

    it('setActivityWindow does not fire the change listener: nothing was added or removed', () => {
      const { list } = mount();
      list.add({ lat: 1, lon: 1, distanceFromStart: 50 });
      let calls = 0;
      list.setChangeListener(() => {
        calls += 1;
      });
      list.setActivityWindow(AWAY);
      assert.equal(calls, 0);
    });

    it('without an isActive option, nothing is ever marked inactive', () => {
      const { list, listEl } = mount();
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 50 });
      list.setActivityWindow(AWAY);
      assert.equal(rowFor(listEl, id).classList.contains('is-inactive'), false);
      assert.notEqual(lastDimmedState(id), true);
    });
  });

  describe('change listener', () => {
    it('fires on add, remove and reset, and never before it is registered', () => {
      const { list } = mount();
      // init() already ran and rendered; a listener registered now must not
      // have missed anything, which is why the listener isn't fired from
      // render() — main.js registers it well after initStopList().
      let calls = 0;
      list.setChangeListener(() => {
        calls += 1;
      });
      assert.equal(calls, 0);

      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      assert.equal(calls, 1);
      list.remove(id);
      assert.equal(calls, 2);
      list.reset();
      assert.equal(calls, 3);
    });

    it('sees the new state, not the old one', () => {
      const { list } = mount();
      let seen = null;
      list.setChangeListener(() => {
        seen = list.getAll().length;
      });
      list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      assert.equal(seen, 1);
    });

    it('is optional — a list without one still works', () => {
      const { list } = mount();
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      list.remove(id);
      assert.deepEqual(list.getAll(), []);
    });

    it('fires on move', () => {
      const { list } = mount();
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      let calls = 0;
      list.setChangeListener(() => {
        calls += 1;
      });
      list.move(id, { lat: 2, lon: 2, distanceFromStart: 20 });
      assert.equal(calls, 1);
    });
  });

  describe('onRemove hook', () => {
    it('runs once when its item is removed, after the list has dropped it', () => {
      const { list } = mount();
      let seenLength = null;
      let calls = 0;
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 }, {
        onRemove: () => {
          calls += 1;
          seenLength = list.getAll().length;
        },
      });
      list.remove(id);
      list.remove(id);

      assert.equal(calls, 1);
      assert.equal(seenLength, 0);
    });

    it('runs from the row\'s own delete button', () => {
      const { list, listEl } = mount();
      let calls = 0;
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 }, { onRemove: () => { calls += 1; } });
      listEl.querySelector(`.anchor-row[data-id="${id}"] .anchor-row-delete`).click();

      assert.equal(calls, 1);
    });

    it('runs only for the removed item', () => {
      const { list } = mount();
      const calls = [];
      const a = list.add({ lat: 1, lon: 1, distanceFromStart: 10 }, { onRemove: () => calls.push('a') });
      list.add({ lat: 2, lon: 2, distanceFromStart: 20 }, { onRemove: () => calls.push('b') });
      list.remove(a);

      assert.deepEqual(calls, ['a']);
    });

    it('does not run on reset, nor for an item added after it', () => {
      const { list } = mount();
      let calls = 0;
      list.add({ lat: 1, lon: 1, distanceFromStart: 10 }, { onRemove: () => { calls += 1; } });
      list.reset();
      const later = list.add({ lat: 2, lon: 2, distanceFromStart: 20 });
      list.remove(later);

      assert.equal(calls, 0);
    });

    it('never reaches getAll', () => {
      const { list } = mount();
      list.add({ lat: 1, lon: 1, distanceFromStart: 10 }, { onRemove: () => {} });

      assert.deepEqual(Object.keys(list.getAll()[0]).sort(), ['distanceFromStart', 'id']);
    });
  });

  describe('move', () => {
    it('moves the pin, takes the new distance and re-sorts and renumbers', () => {
      const { list } = mount();
      const a = list.add({ lat: 1, lon: 1, distanceFromStart: 100 });
      const b = list.add({ lat: 2, lon: 2, distanceFromStart: 200 });
      list.move(a, { lat: 3, lon: 4, distanceFromStart: 300 });

      assert.deepEqual(mapMocks.moveMarker.mock.calls.at(-1).arguments, [a, 3, 4]);
      assert.deepEqual(list.getAll().map((i) => [i.id, i.distanceFromStart]), [[b, 200], [a, 300]]);
      assert.deepEqual(mapMocks.renumberMarkers.mock.calls.at(-1).arguments[0], [b, a]);
    });

    it('keeps the item\'s other fields', () => {
      const { list } = mount();
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 100, source: 'photo' });
      list.move(id, { lat: 2, lon: 2, distanceFromStart: 150 });

      assert.equal(list.getAll()[0].source, 'photo');
    });

    it('ignores an unknown id', () => {
      const { list } = mount();
      list.add({ lat: 1, lon: 1, distanceFromStart: 100 });
      list.move(999, { lat: 2, lon: 2, distanceFromStart: 150 });

      assert.equal(mapMocks.moveMarker.mock.callCount(), 0);
      assert.equal(list.getAll()[0].distanceFromStart, 100);
    });
  });

  describe('draggable pins', () => {
    function markerOptions(id) {
      return mapMocks.addMarker.mock.calls.find((c) => c.arguments[0] === id).arguments[4];
    }

    it('asks isDraggable per item', () => {
      const { list } = mount({ isDraggable: (item) => item.source === 'photo' });
      const photo = list.add({ lat: 1, lon: 1, distanceFromStart: 10, source: 'photo' });
      const user = list.add({ lat: 2, lon: 2, distanceFromStart: 20, source: 'user' });

      assert.equal(markerOptions(photo).draggable, true);
      assert.equal(markerOptions(user).draggable, false);
    });

    it('is off without an isDraggable option', () => {
      const { list } = mount();
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });

      assert.equal(markerOptions(id).draggable, false);
    });

    it('hands a drop to the drag-end listener with a copy of the current item and the revert', () => {
      const { list } = mount({ isDraggable: () => true });
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10, source: 'photo' });
      list.move(id, { lat: 1, lon: 1, distanceFromStart: 15 });
      const received = [];
      list.setDragEndListener((...args) => received.push(args));
      const revert = mock.fn();
      markerOptions(id).onDragEnd(id, 5, 6, revert);

      assert.equal(received.length, 1);
      const [item, lat, lon, passedRevert] = received[0];
      assert.deepEqual(item, { id, distanceFromStart: 15, source: 'photo' });
      assert.equal(lat, 5);
      assert.equal(lon, 6);
      assert.equal(passedRevert, revert);
      assert.equal(revert.mock.callCount(), 0);

      item.distanceFromStart = 999;
      assert.equal(list.getAll()[0].distanceFromStart, 15);
    });

    it('reverts the drop when there is no listener', () => {
      const { list } = mount({ isDraggable: () => true });
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      const revert = mock.fn();
      markerOptions(id).onDragEnd(id, 5, 6, revert);

      assert.equal(revert.mock.callCount(), 1);
    });

    it('reverts a drop of an item that is gone', () => {
      const { list } = mount({ isDraggable: () => true });
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      const listener = mock.fn();
      list.setDragEndListener(listener);
      const { onDragEnd } = markerOptions(id);
      list.remove(id);
      const revert = mock.fn();
      onDragEnd(id, 5, 6, revert);

      assert.equal(listener.mock.callCount(), 0);
      assert.equal(revert.mock.callCount(), 1);
    });
  });
});
