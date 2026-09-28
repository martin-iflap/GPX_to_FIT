// anchors.js and stops.js re-check every placed item against the activity's
// current start–end window. What matters is what reaches convert() — the
// getActive*() payloads — and what the row tells the user. map.js is mocked
// as in markerList.test.js.

import assert from 'node:assert/strict';
import { beforeEach, describe, it, mock } from 'node:test';

import './testUtils/domSetup.js';

mock.module('../../src/gpx2fit/gui/js/map.js', {
  namedExports: {
    addMarker: mock.fn(),
    removeMarker: mock.fn(),
    renumberMarkers: mock.fn(),
    panToMarker: mock.fn(),
    highlightMarker: mock.fn(),
    setMarkerDimmed: mock.fn(),
  },
});

const anchorsModule = await import('../../src/gpx2fit/gui/js/anchors.js');
const stopsModule = await import('../../src/gpx2fit/gui/js/stops.js');

const START = new Date(2024, 5, 1, 8, 0, 0);
const END = new Date(2024, 5, 1, 10, 0, 0);
const HOUR_MS = 3_600_000;

function at(base, ms) {
  return new Date(base.getTime() + ms);
}

const anchorListEl = document.createElement('ul');
const anchorEmptyEl = document.createElement('p');
const stopListEl = document.createElement('ul');
const stopEmptyEl = document.createElement('p');
document.body.append(anchorListEl, anchorEmptyEl, stopListEl, stopEmptyEl);
anchorsModule.initAnchorList(anchorListEl, anchorEmptyEl);
stopsModule.initStopList(stopListEl, stopEmptyEl);

function rowText(listEl, id) {
  return listEl.querySelector(`.anchor-row[data-id="${id}"]`).textContent;
}

function rowIsInactive(listEl, id) {
  return listEl.querySelector(`.anchor-row[data-id="${id}"]`).classList.contains('is-inactive');
}

beforeEach(() => {
  anchorsModule.resetAnchors();
  stopsModule.resetStops();
  anchorsModule.setActivityWindow({ start: START, end: END });
  stopsModule.setActivityWindow({ start: START, end: END });
});

describe('anchors against the activity window', () => {
  it('leaves an anchor the start moved past out of the payload, and brings it back when the start moves back', () => {
    const photo = anchorsModule.addAnchor({
      lat: 1, lon: 1, distanceFromStart: 500, timestamp: at(START, 30 * 60_000), source: 'photo',
    });
    assert.deepEqual(anchorsModule.getActiveAnchors().map((a) => a.id), [photo]);

    anchorsModule.setActivityWindow({ start: at(START, HOUR_MS), end: at(END, HOUR_MS) });
    assert.deepEqual(anchorsModule.getActiveAnchors(), []);
    assert.equal(rowIsInactive(anchorListEl, photo), true);
    assert.match(rowText(anchorListEl, photo), /outside the activity's time/i);

    anchorsModule.setActivityWindow({ start: START, end: END });
    assert.deepEqual(anchorsModule.getActiveAnchors().map((a) => a.id), [photo]);
    assert.equal(rowIsInactive(anchorListEl, photo), false);
    assert.doesNotMatch(rowText(anchorListEl, photo), /outside the activity's time/i);
  });

  it('sends an offset anchor at start + offset, following the start', () => {
    anchorsModule.addAnchor({
      lat: 1, lon: 1, distanceFromStart: 500, timestamp: at(START, 30 * 60_000), offsetSeconds: 1800,
    });
    const laterStart = at(START, HOUR_MS);
    anchorsModule.setActivityWindow({ start: laterStart, end: at(END, HOUR_MS) });

    const [sent] = anchorsModule.getActiveAnchors();
    assert.equal(sent.timestamp.getTime(), laterStart.getTime() + 30 * 60_000);
  });

  it('shows an offset anchor\'s row at its moved time', () => {
    const id = anchorsModule.addAnchor({
      lat: 1, lon: 1, distanceFromStart: 500, timestamp: at(START, 30 * 60_000), offsetSeconds: 1800,
    });
    const before = rowText(anchorListEl, id);
    anchorsModule.setActivityWindow({ start: at(START, HOUR_MS), end: at(END, HOUR_MS) });
    assert.notEqual(rowText(anchorListEl, id), before);
  });

  it('counts how many anchors were left out', () => {
    anchorsModule.addAnchor({ lat: 1, lon: 1, distanceFromStart: 100, timestamp: at(START, 60_000) });
    anchorsModule.addAnchor({ lat: 1, lon: 1, distanceFromStart: 200, timestamp: at(END, 60_000) });
    anchorsModule.addAnchor({ lat: 1, lon: 1, distanceFromStart: 300, timestamp: at(END, 120_000) });
    assert.equal(anchorsModule.getActiveAnchors().length, 1);
    assert.equal(anchorsModule.countInactiveAnchors(), 2);
  });
});

describe('setActivityWindow with an unchanged window', () => {
  // A render replaces every row element, so an untouched row is the same node.
  function rowNode(listEl, id) {
    return listEl.querySelector(`.anchor-row[data-id="${id}"]`);
  }

  it('does not re-render the anchor list for equal times', () => {
    const id = anchorsModule.addAnchor({ lat: 1, lon: 1, distanceFromStart: 500, timestamp: at(START, 60_000) });
    const before = rowNode(anchorListEl, id);
    anchorsModule.setActivityWindow({ start: new Date(START.getTime()), end: new Date(END.getTime()) });
    assert.equal(rowNode(anchorListEl, id), before);
  });

  it('does not re-render the stop list for equal times', () => {
    const id = stopsModule.addStop({ lat: 1, lon: 1, distanceFromStart: 100, mode: 'duration', durationSeconds: 600 });
    const before = rowNode(stopListEl, id);
    stopsModule.setActivityWindow({ start: new Date(START.getTime()), end: new Date(END.getTime()) });
    assert.equal(rowNode(stopListEl, id), before);
  });

  it('still re-renders both lists when the window does move', () => {
    const anchorId = anchorsModule.addAnchor({ lat: 1, lon: 1, distanceFromStart: 500, timestamp: at(START, 60_000) });
    const stopId = stopsModule.addStop({ lat: 1, lon: 1, distanceFromStart: 100, mode: 'duration', durationSeconds: 600 });
    const anchorBefore = rowNode(anchorListEl, anchorId);
    const stopBefore = rowNode(stopListEl, stopId);
    const moved = { start: at(START, 1000), end: END };
    anchorsModule.setActivityWindow(moved);
    stopsModule.setActivityWindow(moved);
    assert.notEqual(rowNode(anchorListEl, anchorId), anchorBefore);
    assert.notEqual(rowNode(stopListEl, stopId), stopBefore);
  });
});

describe('stops against the activity window', () => {
  it('always sends a duration-only stop', () => {
    stopsModule.addStop({ lat: 1, lon: 1, distanceFromStart: 100, mode: 'duration', durationSeconds: 600 });
    stopsModule.setActivityWindow({ start: at(START, 10 * HOUR_MS), end: at(END, 10 * HOUR_MS) });
    assert.equal(stopsModule.getActiveStops().length, 1);
    assert.equal(stopsModule.countInactiveStops(), 0);
  });

  it('leaves out a start/end stop the window moved away from, marking its row', () => {
    const id = stopsModule.addStop({
      lat: 1, lon: 1, distanceFromStart: 100, mode: 'startEnd',
      arrival: at(START, 30 * 60_000), departure: at(START, 40 * 60_000),
    });
    stopsModule.setActivityWindow({ start: at(START, HOUR_MS), end: at(END, HOUR_MS) });
    assert.deepEqual(stopsModule.getActiveStops(), []);
    assert.equal(stopsModule.countInactiveStops(), 1);
    assert.equal(rowIsInactive(stopListEl, id), true);
    assert.match(rowText(stopListEl, id), /outside the activity's time/i);
  });

  it('sends relative stop times resolved against the current start', () => {
    stopsModule.addStop({
      lat: 1, lon: 1, distanceFromStart: 100, mode: 'startEnd',
      arrival: at(START, 30 * 60_000), arrivalOffsetSeconds: 1800,
      departure: at(START, 40 * 60_000), departureOffsetSeconds: 2400,
    });
    const laterStart = at(START, HOUR_MS);
    stopsModule.setActivityWindow({ start: laterStart, end: at(END, HOUR_MS) });

    const [sent] = stopsModule.getActiveStops();
    assert.equal(sent.arrival.getTime(), laterStart.getTime() + 30 * 60_000);
    assert.equal(sent.departure.getTime(), laterStart.getTime() + 40 * 60_000);
  });
});
