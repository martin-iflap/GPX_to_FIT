// anchors.js and stops.js re-check every placed item against the activity's
// current start–end window. What matters is what reaches convert() — the
// getActive*() payloads — and what the row tells the user. Each list gets a
// pin layer that does nothing, since markerList.test.js covers the pins.

import assert from 'node:assert/strict';
import { beforeEach, describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import { createAnchorList } from '../../../src/gpx2fit/gui/js/route/anchors.js';
import { createStopList } from '../../../src/gpx2fit/gui/js/route/stops.js';

function createIdlePins() {
  const idle = () => {};
  return { add: idle, move: idle, remove: idle, renumber: idle, panTo: idle, highlight: idle, setDimmed: idle };
}

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
const anchorList = createAnchorList({ listEl: anchorListEl, emptyEl: anchorEmptyEl, pins: createIdlePins() });
const stopList = createStopList({ listEl: stopListEl, emptyEl: stopEmptyEl, pins: createIdlePins() });

function rowText(listEl, id) {
  return listEl.querySelector(`.list-row[data-id="${id}"]`).textContent;
}

function rowIsInactive(listEl, id) {
  return listEl.querySelector(`.list-row[data-id="${id}"]`).classList.contains('is-inactive');
}

beforeEach(() => {
  anchorList.resetAnchors();
  stopList.resetStops();
  anchorList.setActivityWindow({ start: START, end: END });
  stopList.setActivityWindow({ start: START, end: END });
});

describe('a photo anchor\'s row', () => {
  function rowOf(id) {
    return anchorListEl.querySelector(`.list-row[data-id="${id}"]`);
  }

  it('shows the photo\'s thumbnail instead of the camera glyph', () => {
    const id = anchorList.addAnchor({
      lat: 1, lon: 1, distanceFromStart: 500, timestamp: at(START, 30 * 60_000), source: 'photo', thumbnailUrl: 'blob:thumb',
    });

    assert.equal(rowOf(id).querySelector('img.photo-thumb').getAttribute('src'), 'blob:thumb');
    assert.doesNotMatch(rowOf(id).textContent, /📷/);
  });

  it('falls back to the camera glyph without a thumbnail', () => {
    const id = anchorList.addAnchor({
      lat: 1, lon: 1, distanceFromStart: 500, timestamp: at(START, 30 * 60_000), source: 'photo', thumbnailUrl: null,
    });

    assert.equal(rowOf(id).querySelector('img'), null);
    assert.match(rowOf(id).textContent, /📷/);
  });

  it('never shows a picture on an anchor that isn\'t a photo', () => {
    const id = anchorList.addAnchor({
      lat: 1, lon: 1, distanceFromStart: 500, timestamp: at(START, 30 * 60_000), thumbnailUrl: 'blob:thumb',
    });

    assert.equal(rowOf(id).querySelector('img'), null);
    assert.doesNotMatch(rowOf(id).textContent, /📷/);
  });
});

describe('anchors against the activity window', () => {
  it('leaves an anchor the start moved past out of the payload, and brings it back when the start moves back', () => {
    const photo = anchorList.addAnchor({
      lat: 1, lon: 1, distanceFromStart: 500, timestamp: at(START, 30 * 60_000), source: 'photo',
    });
    assert.deepEqual(anchorList.getActiveAnchors().map((a) => a.id), [photo]);

    anchorList.setActivityWindow({ start: at(START, HOUR_MS), end: at(END, HOUR_MS) });
    assert.deepEqual(anchorList.getActiveAnchors(), []);
    assert.equal(rowIsInactive(anchorListEl, photo), true);
    assert.match(rowText(anchorListEl, photo), /outside the activity's time/i);

    anchorList.setActivityWindow({ start: START, end: END });
    assert.deepEqual(anchorList.getActiveAnchors().map((a) => a.id), [photo]);
    assert.equal(rowIsInactive(anchorListEl, photo), false);
    assert.doesNotMatch(rowText(anchorListEl, photo), /outside the activity's time/i);
  });

  it('sends an offset anchor at start + offset, following the start', () => {
    anchorList.addAnchor({
      lat: 1, lon: 1, distanceFromStart: 500, timestamp: at(START, 30 * 60_000), offsetSeconds: 1800,
    });
    const laterStart = at(START, HOUR_MS);
    anchorList.setActivityWindow({ start: laterStart, end: at(END, HOUR_MS) });

    const [sent] = anchorList.getActiveAnchors();
    assert.equal(sent.timestamp.getTime(), laterStart.getTime() + 30 * 60_000);
  });

  it('shows an offset anchor\'s row at its moved time', () => {
    const id = anchorList.addAnchor({
      lat: 1, lon: 1, distanceFromStart: 500, timestamp: at(START, 30 * 60_000), offsetSeconds: 1800,
    });
    const before = rowText(anchorListEl, id);
    anchorList.setActivityWindow({ start: at(START, HOUR_MS), end: at(END, HOUR_MS) });
    assert.notEqual(rowText(anchorListEl, id), before);
  });

  it('counts how many anchors were left out', () => {
    anchorList.addAnchor({ lat: 1, lon: 1, distanceFromStart: 100, timestamp: at(START, 60_000) });
    anchorList.addAnchor({ lat: 1, lon: 1, distanceFromStart: 200, timestamp: at(END, 60_000) });
    anchorList.addAnchor({ lat: 1, lon: 1, distanceFromStart: 300, timestamp: at(END, 120_000) });
    assert.equal(anchorList.getActiveAnchors().length, 1);
    assert.equal(anchorList.countInactiveAnchors(), 2);
  });
});

describe('ids', () => {
  it('anchors and stops each number from 1, since each has its own pin layer', () => {
    const element = (tag) => document.body.appendChild(document.createElement(tag));
    const anchors = createAnchorList({ listEl: element('ul'), emptyEl: element('p'), pins: createIdlePins() });
    const stops = createStopList({ listEl: element('ul'), emptyEl: element('p'), pins: createIdlePins() });
    const anchorId = anchors.addAnchor({ lat: 1, lon: 1, distanceFromStart: 100, timestamp: at(START, 60_000) });
    const stopId = stops.addStop({ lat: 1, lon: 1, distanceFromStart: 200, mode: 'duration', durationSeconds: 60 });

    assert.equal(anchorId, 1);
    assert.equal(stopId, 1);
  });
});

describe('stops against the activity window', () => {
  it('always sends a duration-only stop', () => {
    stopList.addStop({ lat: 1, lon: 1, distanceFromStart: 100, mode: 'duration', durationSeconds: 600 });
    stopList.setActivityWindow({ start: at(START, 10 * HOUR_MS), end: at(END, 10 * HOUR_MS) });
    assert.equal(stopList.getActiveStops().length, 1);
    assert.equal(stopList.countInactiveStops(), 0);
  });

  it('leaves out a start/end stop the window moved away from, marking its row', () => {
    const id = stopList.addStop({
      lat: 1, lon: 1, distanceFromStart: 100, mode: 'startEnd',
      arrival: at(START, 30 * 60_000), departure: at(START, 40 * 60_000),
    });
    stopList.setActivityWindow({ start: at(START, HOUR_MS), end: at(END, HOUR_MS) });
    assert.deepEqual(stopList.getActiveStops(), []);
    assert.equal(stopList.countInactiveStops(), 1);
    assert.equal(rowIsInactive(stopListEl, id), true);
    assert.match(rowText(stopListEl, id), /outside the activity's time/i);
  });

  it('sends relative stop times resolved against the current start', () => {
    stopList.addStop({
      lat: 1, lon: 1, distanceFromStart: 100, mode: 'startEnd',
      arrival: at(START, 30 * 60_000), arrivalOffsetSeconds: 1800,
      departure: at(START, 40 * 60_000), departureOffsetSeconds: 2400,
    });
    const laterStart = at(START, HOUR_MS);
    stopList.setActivityWindow({ start: laterStart, end: at(END, HOUR_MS) });

    const [sent] = stopList.getActiveStops();
    assert.equal(sent.arrival.getTime(), laterStart.getTime() + 30 * 60_000);
    assert.equal(sent.departure.getTime(), laterStart.getTime() + 40 * 60_000);
  });
});
