import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  effectiveAnchorTime,
  effectiveStopTimes,
  isAnchorActive,
  isStopActive,
  isStrictlyInside,
  sameWindow,
} from '../../src/gpx2fit/gui/js/activityWindow.js';

const START = new Date(2024, 5, 1, 8, 0, 0);
const END = new Date(2024, 5, 1, 10, 0, 0);
const WINDOW = { start: START, end: END };

function at(base, ms) {
  return new Date(base.getTime() + ms);
}

describe('isStrictlyInside', () => {
  it('rejects the start and the end themselves — they are anchors already', () => {
    assert.equal(isStrictlyInside(START, WINDOW), false);
    assert.equal(isStrictlyInside(END, WINDOW), false);
  });

  it('accepts 1 ms inside either edge', () => {
    assert.equal(isStrictlyInside(at(START, 1), WINDOW), true);
    assert.equal(isStrictlyInside(at(END, -1), WINDOW), true);
  });

  it('rejects 1 ms outside either edge', () => {
    assert.equal(isStrictlyInside(at(START, -1), WINDOW), false);
    assert.equal(isStrictlyInside(at(END, 1), WINDOW), false);
  });

  it('treats an unknown window as no constraint', () => {
    assert.equal(isStrictlyInside(at(START, -86_400_000), null), true);
  });
});

describe('effectiveAnchorTime', () => {
  it('resolves an offset anchor against whichever start it is given', () => {
    const anchor = { timestamp: at(START, 1_800_000), offsetSeconds: 1800 };
    const laterStart = at(START, 3_600_000);

    assert.equal(effectiveAnchorTime(anchor, START).getTime(), START.getTime() + 1_800_000);
    assert.equal(effectiveAnchorTime(anchor, laterStart).getTime(), laterStart.getTime() + 1_800_000);
  });

  it('leaves an absolute anchor (time of day, photo) where it is', () => {
    const timestamp = at(START, 1_800_000);
    const anchor = { timestamp };
    assert.equal(effectiveAnchorTime(anchor, at(START, 3_600_000)).getTime(), timestamp.getTime());
  });

  it('falls back to the stored timestamp while no start is known', () => {
    const timestamp = at(START, 1_800_000);
    assert.equal(effectiveAnchorTime({ timestamp, offsetSeconds: 60 }, null).getTime(), timestamp.getTime());
  });
});

describe('effectiveStopTimes', () => {
  it('resolves each side independently — one relative, one absolute', () => {
    const departure = at(START, 5_400_000);
    const stop = {
      mode: 'startEnd',
      arrival: at(START, 3_600_000),
      arrivalOffsetSeconds: 3600,
      departure,
    };
    const laterStart = at(START, 600_000);

    const { arrival, departure: resolvedDeparture } = effectiveStopTimes(stop, laterStart);
    assert.equal(arrival.getTime(), laterStart.getTime() + 3_600_000);
    assert.equal(resolvedDeparture.getTime(), departure.getTime());
  });
});

describe('isAnchorActive', () => {
  it('goes inactive when the window moves past it, and comes back when it moves back', () => {
    const anchor = { timestamp: at(START, 1_800_000) };
    const movedLater = { start: at(START, 3_600_000), end: at(END, 3_600_000) };

    assert.equal(isAnchorActive(anchor, WINDOW), true);
    assert.equal(isAnchorActive(anchor, movedLater), false);
    assert.equal(isAnchorActive(anchor, WINDOW), true);
  });

  it('keeps an offset anchor active when the start moves, since it moves too', () => {
    const anchor = { timestamp: at(START, 1_800_000), offsetSeconds: 1800 };
    const movedLater = { start: at(START, 3_600_000), end: at(END, 3_600_000) };
    assert.equal(isAnchorActive(anchor, movedLater), true);
  });

  it('turns an offset anchor inactive once the duration shrinks below its offset', () => {
    const anchor = { timestamp: at(START, 1_800_000), offsetSeconds: 1800 };
    assert.equal(isAnchorActive(anchor, { start: START, end: at(START, 1_800_000) }), false);
    assert.equal(isAnchorActive(anchor, { start: START, end: at(START, 1_800_001) }), true);
  });

  it('is active while the window is unknown', () => {
    assert.equal(isAnchorActive({ timestamp: at(START, -86_400_000) }, null), true);
  });
});

describe('isStopActive', () => {
  it('always counts a duration-only stop, which has no clock time to check', () => {
    const stop = { mode: 'duration', durationSeconds: 600 };
    assert.equal(isStopActive(stop, { start: START, end: at(START, 1000) }), true);
  });

  it('counts a start/end stop only when both its times are strictly inside', () => {
    const inside = { mode: 'startEnd', arrival: at(START, 60_000), departure: at(END, -60_000) };
    const departsAfterEnd = { mode: 'startEnd', arrival: at(START, 60_000), departure: at(END, 1) };
    const arrivesOnStart = { mode: 'startEnd', arrival: START, departure: at(START, 60_000) };

    assert.equal(isStopActive(inside, WINDOW), true);
    assert.equal(isStopActive(departsAfterEnd, WINDOW), false);
    assert.equal(isStopActive(arrivesOnStart, WINDOW), false);
  });
});

describe('sameWindow', () => {
  it('treats two windows with equal times as the same, even as different Date objects', () => {
    assert.equal(sameWindow(WINDOW, { start: new Date(START.getTime()), end: new Date(END.getTime()) }), true);
  });

  it('tells windows apart by 1 ms at either edge', () => {
    assert.equal(sameWindow(WINDOW, { start: at(START, 1), end: END }), false);
    assert.equal(sameWindow(WINDOW, { start: START, end: at(END, 1) }), false);
  });

  it('treats null as the same only as another null', () => {
    assert.equal(sameWindow(null, null), true);
    assert.equal(sameWindow(null, WINDOW), false);
    assert.equal(sameWindow(WINDOW, null), false);
  });
});
