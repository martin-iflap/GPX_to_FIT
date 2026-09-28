// Uses node:test's built-in module mocking (`mock.module`) to stub out
// map.js's Leaflet-backed exports, so createMarkerList's ordering/id-assignment
// logic can be tested without a real map. Requires running Node with
// --experimental-test-module-mocks (see the "test" script in package.json).

import assert from 'node:assert/strict';
import { beforeEach, describe, it, mock } from 'node:test';

import './testUtils/domSetup.js';

// Records the order of calls across mocks, which per-mock call lists can't show.
const mapCallLog = [];

const mapMocks = {
  addMarker: mock.fn(),
  removeMarker: mock.fn(),
  renumberMarkers: mock.fn(() => mapCallLog.push('renumberMarkers')),
  panToMarker: mock.fn(),
  highlightMarker: mock.fn(),
  setMarkerDimmed: mock.fn(() => mapCallLog.push('setMarkerDimmed')),
};

mock.module('../../src/gpx2fit/gui/js/map.js', { namedExports: mapMocks });

const { createMarkerList } = await import('../../src/gpx2fit/gui/js/markerList.js');

function mount({ idOffset, isInactive, renderRowText = () => [] } = {}) {
  const list = createMarkerList({
    idOffset,
    markerKind: () => 'anchor',
    deleteAriaLabel: 'Remove',
    renderRowText,
    isInactive,
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
      const { list, listEl } = mount({ isInactive: (item) => item.distanceFromStart > 100 });
      const active = list.add({ lat: 1, lon: 1, distanceFromStart: 50 });
      const inactive = list.add({ lat: 2, lon: 2, distanceFromStart: 200 });

      assert.equal(rowFor(listEl, active).classList.contains('is-inactive'), false);
      assert.equal(rowFor(listEl, inactive).classList.contains('is-inactive'), true);
      assert.equal(lastDimmedState(active), false);
      assert.equal(lastDimmedState(inactive), true);
    });

    it('dims after renumbering, since renumbering replaces the pin icon', () => {
      const { list } = mount({ isInactive: () => true });
      mapCallLog.length = 0;
      list.add({ lat: 1, lon: 1, distanceFromStart: 50 });

      const lastRenumber = mapCallLog.lastIndexOf('renumberMarkers');
      const lastDim = mapCallLog.lastIndexOf('setMarkerDimmed');
      assert.notEqual(lastRenumber, -1);
      assert.ok(lastDim > lastRenumber, `expected dim after renumber, got ${mapCallLog.join(' → ')}`);
    });

    it('refresh() re-evaluates the predicate: inactive, then active again', () => {
      let windowMovedAway = false;
      const { list, listEl } = mount({ isInactive: () => windowMovedAway });
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 50 });
      assert.equal(rowFor(listEl, id).classList.contains('is-inactive'), false);

      windowMovedAway = true;
      list.refresh();
      assert.equal(rowFor(listEl, id).classList.contains('is-inactive'), true);
      assert.equal(lastDimmedState(id), true);

      windowMovedAway = false;
      list.refresh();
      assert.equal(rowFor(listEl, id).classList.contains('is-inactive'), false);
      assert.equal(lastDimmedState(id), false);
    });

    it('refresh() leaves pin numbering alone — distances, and so the order, cannot have changed', () => {
      let windowMovedAway = false;
      const { list } = mount({ isInactive: () => windowMovedAway });
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 50 });
      mapMocks.renumberMarkers.mock.resetCalls();
      mapMocks.setMarkerDimmed.mock.resetCalls();

      windowMovedAway = true;
      list.refresh();
      assert.equal(mapMocks.renumberMarkers.mock.callCount(), 0);
      // The pin's dimming still follows, on the icon it already has.
      assert.equal(lastDimmedState(id), true);
    });

    it('refresh() re-renders the row text from current state', () => {
      let label = 'before';
      const { list, listEl } = mount({
        renderRowText: () => {
          const el = document.createElement('span');
          el.textContent = label;
          return [el];
        },
      });
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 50 });
      label = 'after';
      list.refresh();
      assert.equal(rowFor(listEl, id).textContent.includes('after'), true);
    });

    it('refresh() does not fire the change listener — nothing was added or removed', () => {
      const { list } = mount();
      list.add({ lat: 1, lon: 1, distanceFromStart: 50 });
      let calls = 0;
      list.setChangeListener(() => {
        calls += 1;
      });
      list.refresh();
      assert.equal(calls, 0);
    });

    it('without an isInactive option, nothing is ever marked inactive', () => {
      const { list, listEl } = mount();
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 50 });
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
  });
});
