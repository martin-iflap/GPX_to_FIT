// Uses node:test's built-in module mocking (`mock.module`) to stub out
// map.js's Leaflet-backed exports, so createMarkerList's ordering/id-assignment
// logic can be tested without a real map. Requires running Node with
// --experimental-test-module-mocks (see the "test" script in package.json).

import assert from 'node:assert/strict';
import { beforeEach, describe, it, mock } from 'node:test';

import './testUtils/domSetup.js';

const mapMocks = {
  addMarker: mock.fn(),
  removeMarker: mock.fn(),
  renumberMarkers: mock.fn(),
  panToMarker: mock.fn(),
  highlightMarker: mock.fn(),
};

mock.module('../../src/gpx2fit/gui/js/map.js', { namedExports: mapMocks });

const { createMarkerList } = await import('../../src/gpx2fit/gui/js/markerList.js');

function mount({ idOffset } = {}) {
  const list = createMarkerList({
    idOffset,
    markerKind: () => 'anchor',
    deleteAriaLabel: 'Remove',
    renderRowText: () => [],
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
});
