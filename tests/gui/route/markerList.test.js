// createMarkerList against a fake pins adapter (what map.js's createPinLayer
// returns), so the ordering, id and window logic is tested without a map.

import assert from 'node:assert/strict';
import { describe, it, mock } from 'node:test';

import '../testUtils/domSetup.js';
import { createMarkerList } from '../../../src/gpx2fit/gui/js/route/markerList.js';

/**
 * Records every call, in order, as `[method, ...args]`, so a test can check
 * both what one method got and what came before what.
 */
function createFakePins() {
  const log = [];
  const record = (method) => (...args) => {
    log.push([method, ...args]);
  };
  return {
    log,
    calls: (method) => log.filter(([name]) => name === method).map(([, ...args]) => args),
    add: record('add'),
    move: record('move'),
    remove: record('remove'),
    renumber: record('renumber'),
    panTo: record('panTo'),
    highlight: record('highlight'),
    setDimmed: record('setDimmed'),
  };
}

function mount({
  isActive, isDraggable, renderRowText = () => [], renderRowMedia, revealMap, prepare = () => {},
} = {}) {
  const pins = createFakePins();
  const listEl = document.createElement('ul');
  const emptyEl = document.createElement('p');
  document.body.append(listEl, emptyEl);
  prepare(listEl, emptyEl);
  const list = createMarkerList({
    listEl,
    emptyEl,
    pins,
    revealMap,
    markerKind: () => 'anchor',
    deleteLabel: 'Delete item',
    renderRowText,
    renderRowMedia,
    isActive,
    isDraggable,
  });
  return { list, listEl, emptyEl, pins };
}

function rowFor(listEl, id) {
  return listEl.querySelector(`.list-row[data-id="${id}"]`);
}

/** The row's own button, which pans to its pin. */
function mainButton(listEl, id) {
  return rowFor(listEl, id).querySelector('.list-row-info');
}

describe('createMarkerList', () => {
  it('renders itself, empty, as soon as it is created', () => {
    const { listEl, emptyEl } = mount({
      prepare: (list, empty) => {
        list.innerHTML = '<li>left over</li>';
        empty.hidden = true;
      },
    });
    assert.equal(listEl.children.length, 0);
    assert.equal(emptyEl.hidden, false);
  });

  it('assigns strictly increasing ids from 1', () => {
    const { list } = mount();
    const id1 = list.add({ lat: 1, lon: 1, distanceFromStart: 0 });
    const id2 = list.add({ lat: 2, lon: 2, distanceFromStart: 10 });
    assert.equal(id1, 1);
    assert.equal(id2, 2);
  });

  it('keeps its pins to its own pin layer, so two lists can use the same ids', () => {
    const anchors = mount();
    const stops = mount();
    const anchorId = anchors.list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
    const stopId = stops.list.add({ lat: 2, lon: 2, distanceFromStart: 20 });
    assert.equal(anchorId, stopId);

    stops.list.remove(stopId);
    assert.deepEqual(stops.pins.calls('remove'), [[stopId]]);
    assert.deepEqual(anchors.pins.calls('remove'), []);
    assert.deepEqual(anchors.list.getAll().map((i) => i.id), [anchorId]);
  });

  it('adds a numbered pin of the item\'s kind at its position', () => {
    const { list, pins } = mount();
    list.add({ lat: 1, lon: 1, distanceFromStart: 500 });
    const id = list.add({ lat: 46.5, lon: 8.1, distanceFromStart: 100 });

    const [pinId, lat, lon, number, opts] = pins.calls('add').at(-1);
    assert.deepEqual([pinId, lat, lon, number, opts.kind], [id, 46.5, 8.1, 2, 'anchor']);
  });

  it('orders items by distanceFromStart regardless of insertion order', () => {
    const { list } = mount();
    const later = list.add({ lat: 1, lon: 1, distanceFromStart: 500 });
    const earlier = list.add({ lat: 2, lon: 2, distanceFromStart: 100 });
    const middle = list.add({ lat: 3, lon: 3, distanceFromStart: 300 });

    assert.deepEqual(list.getAll().map((i) => i.id), [earlier, middle, later]);
  });

  it('keeps stable relative order for a tie in distanceFromStart', () => {
    const { list } = mount();
    const first = list.add({ lat: 1, lon: 1, distanceFromStart: 100 });
    const second = list.add({ lat: 2, lon: 2, distanceFromStart: 100 });

    assert.deepEqual(list.getAll().map((i) => i.id), [first, second]);
  });

  it('renumbers the pins in distance order after add and remove', () => {
    const { list, pins } = mount();
    const later = list.add({ lat: 1, lon: 1, distanceFromStart: 500 });
    const earlier = list.add({ lat: 2, lon: 2, distanceFromStart: 100 });
    assert.deepEqual(pins.calls('renumber').at(-1), [[earlier, later]]);

    list.remove(later);
    assert.deepEqual(pins.calls('renumber').at(-1), [[earlier]]);
    assert.deepEqual(pins.calls('remove').at(-1), [later]);
  });

  it('remove drops only the targeted item', () => {
    const { list } = mount();
    const a = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
    const b = list.add({ lat: 2, lon: 2, distanceFromStart: 20 });
    list.remove(a);
    assert.deepEqual(list.getAll().map((i) => i.id), [b]);
  });

  it('reset clears every item and its pin', () => {
    const { list, pins, emptyEl } = mount();
    const a = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
    const b = list.add({ lat: 2, lon: 2, distanceFromStart: 20 });
    list.reset();
    assert.deepEqual(list.getAll(), []);
    assert.deepEqual(pins.calls('remove').map(([id]) => id).sort(), [a, b]);
    assert.equal(emptyEl.hidden, false);
  });

  it('puts an item\'s picture between its number and its text, and nothing for an item without one', () => {
    const { list, listEl } = mount({
      renderRowText: () => [Object.assign(document.createElement('span'), { className: 'text' })],
      renderRowMedia: (item) => (item.picture ? Object.assign(document.createElement('img'), { className: 'pic' }) : null),
    });
    const withPicture = list.add({ lat: 1, lon: 1, distanceFromStart: 10, picture: true });
    const without = list.add({ lat: 2, lon: 2, distanceFromStart: 20 });

    const classesOf = (id) => [...rowFor(listEl, id).querySelector('.list-row-info').children]
      .map((el) => el.className);
    assert.deepEqual(classesOf(withPicture), ['list-row-number', 'pic', 'list-row-text']);
    assert.deepEqual(classesOf(without), ['list-row-number', 'list-row-text']);
  });

  it('getAll returns a defensive copy', () => {
    const { list } = mount();
    list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
    const snapshot = list.getAll();
    snapshot[0].distanceFromStart = 999;
    assert.equal(list.getAll()[0].distanceFromStart, 10);
  });

  describe('rows and pins point at each other', () => {
    it('a row click brings the map into view first, then pans to that row\'s pin', () => {
      const { list, listEl, pins } = mount({ revealMap: () => pins.log.push(['revealMap']) });
      list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      const id = list.add({ lat: 2, lon: 2, distanceFromStart: 20 });
      pins.log.length = 0;
      mainButton(listEl, id).click();

      assert.deepEqual(pins.log, [['revealMap'], ['panTo', id]]);
    });

    it("pans from a real button, so the keyboard can reach it, and doesn't submit a form", () => {
      const { list, listEl } = mount();
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      const button = mainButton(listEl, id);
      assert.equal(button.tagName, 'BUTTON');
      assert.equal(button.type, 'button');
      assert.equal(rowFor(listEl, id).querySelectorAll('button').length, 2, 'pan and delete, nothing else');
    });

    it('holds only phrasing content in that button, as HTML requires', () => {
      const { list, listEl } = mount({
        renderRowText: () => [document.createElement('span')],
        renderRowMedia: () => document.createElement('img'),
      });
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      const blocks = mainButton(listEl, id).querySelectorAll('div, p, ul, li');
      assert.equal(blocks.length, 0);
    });

    it('focusing the button highlights its pin, and leaving it un-highlights it', () => {
      const { list, listEl, pins } = mount();
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      mainButton(listEl, id).dispatchEvent(new FocusEvent('focus'));
      mainButton(listEl, id).dispatchEvent(new FocusEvent('blur'));

      assert.deepEqual(pins.calls('highlight'), [[id, true], [id, false]]);
    });

    it('a click on the row around the button does nothing', () => {
      const { list, listEl, pins } = mount();
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      rowFor(listEl, id).click();
      assert.deepEqual(pins.calls('panTo'), []);
    });

    it('hovering a row highlights its pin, and leaving it un-highlights it', () => {
      const { list, listEl, pins } = mount();
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      rowFor(listEl, id).dispatchEvent(new Event('mouseenter'));
      rowFor(listEl, id).dispatchEvent(new Event('mouseleave'));

      assert.deepEqual(pins.calls('highlight'), [[id, true], [id, false]]);
    });

    it('a pin click highlights its row only', () => {
      const { list, listEl, pins } = mount();
      const a = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      const b = list.add({ lat: 2, lon: 2, distanceFromStart: 20 });
      const [, , , , { onClick }] = pins.calls('add').find(([id]) => id === b);
      onClick(b);

      assert.equal(rowFor(listEl, b).classList.contains('is-active'), true);
      assert.equal(rowFor(listEl, a).classList.contains('is-active'), false);
    });

    it('the row\'s delete button removes the item without also panning to it', () => {
      const { list, listEl, pins } = mount();
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      rowFor(listEl, id).querySelector('.list-row-delete').click();

      assert.deepEqual(list.getAll(), []);
      assert.deepEqual(pins.calls('panTo'), []);
    });
  });

  describe("each delete button's label names its row", () => {
    const deleteLabel = (listEl, id) => rowFor(listEl, id).querySelector('.list-row-delete').getAttribute('aria-label');

    it('by its number and distance', () => {
      const { list, listEl } = mount();
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 4200 });
      assert.equal(deleteLabel(listEl, id), 'Delete item 1 at 4.20 km');
    });

    it('renumbered when an item lands before it', () => {
      const { list, listEl } = mount();
      const later = list.add({ lat: 1, lon: 1, distanceFromStart: 4200 });
      list.add({ lat: 2, lon: 2, distanceFromStart: 1000 });
      assert.equal(deleteLabel(listEl, later), 'Delete item 2 at 4.20 km');
    });

    it('and follows a move', () => {
      const { list, listEl } = mount();
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 4200 });
      list.move(id, { lat: 3, lon: 3, distanceFromStart: 750 });
      assert.equal(deleteLabel(listEl, id), 'Delete item 1 at 0.75 km');
    });
  });

  describe('inactive items', () => {
    function lastDimmedState(pins, id) {
      return pins.calls('setDimmed').findLast(([dimmedId]) => dimmedId === id)?.[1];
    }

    it('marks only the inactive item\'s row and dims only its pin', () => {
      const { list, listEl, pins } = mount({ isActive: (item) => item.distanceFromStart <= 100 });
      const active = list.add({ lat: 1, lon: 1, distanceFromStart: 50 });
      const inactive = list.add({ lat: 2, lon: 2, distanceFromStart: 200 });

      assert.equal(rowFor(listEl, active).classList.contains('is-inactive'), false);
      assert.equal(rowFor(listEl, inactive).classList.contains('is-inactive'), true);
      assert.equal(lastDimmedState(pins, active), false);
      assert.equal(lastDimmedState(pins, inactive), true);
    });

    it('dims after renumbering, since renumbering replaces the pin icon', () => {
      const { list, pins } = mount({ isActive: () => false });
      pins.log.length = 0;
      list.add({ lat: 1, lon: 1, distanceFromStart: 50 });

      const methods = pins.log.map(([method]) => method);
      const lastRenumber = methods.lastIndexOf('renumber');
      assert.notEqual(lastRenumber, -1);
      assert.ok(methods.lastIndexOf('setDimmed') > lastRenumber, `expected dim after renumber, got ${methods.join(' → ')}`);
    });

    // An item is active while the window holds its `time`.
    const AWAY = { start: new Date(2000), end: new Date(3000) };
    const AROUND = { start: new Date(0), end: new Date(2000) };
    const inWindow = (item, activityWindow) => !activityWindow
      || (activityWindow.start < item.time && item.time < activityWindow.end);

    it('setActivityWindow re-checks every item: inactive, then active again', () => {
      const { list, listEl, pins } = mount({ isActive: inWindow });
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 50, time: new Date(1000) });
      assert.equal(rowFor(listEl, id).classList.contains('is-inactive'), false);

      list.setActivityWindow(AWAY);
      assert.equal(rowFor(listEl, id).classList.contains('is-inactive'), true);
      assert.equal(lastDimmedState(pins, id), true);

      list.setActivityWindow(AROUND);
      assert.equal(rowFor(listEl, id).classList.contains('is-inactive'), false);
      assert.equal(lastDimmedState(pins, id), false);
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
      const { list, pins } = mount({ isActive: inWindow });
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 50, time: new Date(1000) });
      pins.log.length = 0;

      list.setActivityWindow(AWAY);
      assert.deepEqual(pins.calls('renumber'), []);
      // The pin's dimming still follows, on the icon it already has.
      assert.equal(lastDimmedState(pins, id), true);
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
      const { list, listEl, pins } = mount();
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 50 });
      list.setActivityWindow(AWAY);
      assert.equal(rowFor(listEl, id).classList.contains('is-inactive'), false);
      assert.notEqual(lastDimmedState(pins, id), true);
    });
  });

  describe('change listener', () => {
    it('fires on add, remove and reset, and never before it is registered', () => {
      const { list } = mount();
      // Creating the list already rendered it; a listener registered now must
      // not have missed anything, which is why the listener isn't fired from
      // render() (main.js registers it after creating the stop list).
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
      rowFor(listEl, id).querySelector('.list-row-delete').click();

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
      const { list, pins } = mount();
      const a = list.add({ lat: 1, lon: 1, distanceFromStart: 100 });
      const b = list.add({ lat: 2, lon: 2, distanceFromStart: 200 });
      list.move(a, { lat: 3, lon: 4, distanceFromStart: 300 });

      assert.deepEqual(pins.calls('move').at(-1), [a, 3, 4]);
      assert.deepEqual(list.getAll().map((i) => [i.id, i.distanceFromStart]), [[b, 200], [a, 300]]);
      assert.deepEqual(pins.calls('renumber').at(-1), [[b, a]]);
    });

    it('keeps the item\'s other fields', () => {
      const { list } = mount();
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 100, source: 'photo' });
      list.move(id, { lat: 2, lon: 2, distanceFromStart: 150 });

      assert.equal(list.getAll()[0].source, 'photo');
    });

    it('ignores an unknown id', () => {
      const { list, pins } = mount();
      list.add({ lat: 1, lon: 1, distanceFromStart: 100 });
      list.move(999, { lat: 2, lon: 2, distanceFromStart: 150 });

      assert.deepEqual(pins.calls('move'), []);
      assert.equal(list.getAll()[0].distanceFromStart, 100);
    });
  });

  describe('draggable pins', () => {
    function pinOptions(pins, id) {
      return pins.calls('add').find(([pinId]) => pinId === id)[4];
    }

    it('asks isDraggable per item', () => {
      const { list, pins } = mount({ isDraggable: (item) => item.source === 'photo' });
      const photo = list.add({ lat: 1, lon: 1, distanceFromStart: 10, source: 'photo' });
      const user = list.add({ lat: 2, lon: 2, distanceFromStart: 20, source: 'user' });

      assert.equal(pinOptions(pins, photo).draggable, true);
      assert.equal(pinOptions(pins, user).draggable, false);
    });

    it('is off without an isDraggable option', () => {
      const { list, pins } = mount();
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });

      assert.equal(pinOptions(pins, id).draggable, false);
    });

    it('hands a drop to the drag-end listener with a copy of the current item and the revert', () => {
      const { list, pins } = mount({ isDraggable: () => true });
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10, source: 'photo' });
      list.move(id, { lat: 1, lon: 1, distanceFromStart: 15 });
      const received = [];
      list.setDragEndListener((...args) => received.push(args));
      const revert = mock.fn();
      pinOptions(pins, id).onDragEnd(id, 5, 6, revert);

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
      const { list, pins } = mount({ isDraggable: () => true });
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      const revert = mock.fn();
      pinOptions(pins, id).onDragEnd(id, 5, 6, revert);

      assert.equal(revert.mock.callCount(), 1);
    });

    it('reverts a drop of an item that is gone', () => {
      const { list, pins } = mount({ isDraggable: () => true });
      const id = list.add({ lat: 1, lon: 1, distanceFromStart: 10 });
      const listener = mock.fn();
      list.setDragEndListener(listener);
      const { onDragEnd } = pinOptions(pins, id);
      list.remove(id);
      const revert = mock.fn();
      onDragEnd(id, 5, 6, revert);

      assert.equal(listener.mock.callCount(), 0);
      assert.equal(revert.mock.callCount(), 1);
    });
  });
});
