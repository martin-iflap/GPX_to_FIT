import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { createAppState } from '../../../src/gpx2fit/gui/js/state/appState.js';

const START = new Date(2024, 5, 1, 8, 0, 0);
const END = new Date(2024, 5, 1, 10, 0, 0);
const VALID = { isValid: true, mode: 'duration', resolvedDate: END, durationSeconds: 7200 };

/** A state plus every window its listener was given, in order. */
function stateWithListener() {
  const state = createAppState();
  const windows = [];
  state.onWindowChange((activityWindow) => windows.push(activityWindow));
  return { state, windows };
}

describe('createAppState', () => {
  it('starts with no route, no start time, no window and the given sport', () => {
    const state = createAppState({ sport: 'running' });
    assert.equal(state.route, null);
    assert.equal(state.startTime, null);
    assert.equal(state.timeResult.isValid, false);
    assert.equal(state.activityWindow(), null);
    assert.equal(state.durationSeconds(), null);
    assert.equal(state.sport, 'running');
  });

  it('defaults the sport to hiking, the HTML\'s default tab', () => {
    assert.equal(createAppState().sport, 'hiking');
    assert.equal(createAppState({ sport: undefined }).sport, 'hiking');
  });

  it('keeps the route and sport it is given', () => {
    const state = createAppState();
    const route = { points: [], totalDistance: 1234, fileName: 'a.gpx' };
    state.setRoute(route);
    state.setSport('running');
    assert.equal(state.route, route);
    assert.equal(state.sport, 'running');
  });
});

describe('the activity window', () => {
  it('runs from the start to the result\'s end while both are valid', () => {
    const state = createAppState();
    state.setTime(START, VALID);
    assert.deepEqual(state.activityWindow(), { start: START, end: END });
    assert.equal(state.durationSeconds(), 7200);
  });

  it('is null while the result is invalid, even with a start', () => {
    const state = createAppState();
    state.setTime(START, { isValid: false });
    assert.equal(state.activityWindow(), null);
    assert.equal(state.durationSeconds(), null);
    // The start still counts on its own: an anchor entered as a duration resolves against it.
    assert.equal(state.startTime, START);
  });

  it('is null without a start', () => {
    const state = createAppState();
    state.setTime(null, VALID);
    assert.equal(state.activityWindow(), null);
  });
});

describe('onWindowChange', () => {
  it('fires with the new window when it moves', () => {
    const { state, windows } = stateWithListener();
    state.setTime(START, VALID);
    const later = new Date(END.getTime() + 60_000);
    state.setTime(START, { ...VALID, resolvedDate: later });
    assert.deepEqual(windows, [{ start: START, end: END }, { start: START, end: later }]);
  });

  it('does not fire for a window with equal times, so the lists aren\'t re-rendered for nothing', () => {
    const { state, windows } = stateWithListener();
    state.setTime(START, VALID);
    // A sport switch or an added stop reports the same window again, in new Date objects.
    state.setTime(new Date(START.getTime()), { ...VALID, resolvedDate: new Date(END.getTime()) });
    assert.equal(windows.length, 1);
  });

  it('fires null once when the window becomes invalid, and not again while it stays so', () => {
    const { state, windows } = stateWithListener();
    state.setTime(START, VALID);
    state.setTime(START, { isValid: false });
    state.setTime(null, { isValid: false });
    assert.deepEqual(windows, [{ start: START, end: END }, null]);
  });

  it('is not called for the initial (null) window', () => {
    const { state, windows } = stateWithListener();
    state.setTime(null, { isValid: false });
    assert.deepEqual(windows, []);
  });

  it('calls every listener', () => {
    const state = createAppState();
    let calls = 0;
    state.onWindowChange(() => {
      calls += 1;
    });
    state.onWindowChange(() => {
      calls += 1;
    });
    state.setTime(START, VALID);
    assert.equal(calls, 2);
  });

  it('sees the new start and result already stored', () => {
    const state = createAppState();
    let seen = null;
    state.onWindowChange(() => {
      seen = { start: state.startTime, result: state.timeResult };
    });
    state.setTime(START, VALID);
    assert.deepEqual(seen, { start: START, result: VALID });
  });
});
