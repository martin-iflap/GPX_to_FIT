import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  estimateArrivalDate,
  isRouteEnd,
  offsetSecondsOf,
  toggleDefaultsFromEstimate,
} from '../../../src/gpx2fit/gui/js/placement/placementEstimates.js';

const START = new Date(2024, 5, 1, 10, 0);

describe('estimateArrivalDate', () => {
  const activity = { start: START, durationSeconds: 4 * 3600, totalDistance: 20_000 };

  it('places a point at its share of the distance, at a uniform pace', () => {
    // A quarter of the way along a 4 h activity is 1 h in.
    assert.deepEqual(estimateArrivalDate(5_000, activity), new Date(2024, 5, 1, 11, 0));
    assert.deepEqual(estimateArrivalDate(0, activity), START);
    assert.deepEqual(estimateArrivalDate(20_000, activity), new Date(2024, 5, 1, 14, 0));
  });

  it('has no estimate without a start, a duration or a route', () => {
    assert.equal(estimateArrivalDate(5_000, { ...activity, start: null }), null);
    assert.equal(estimateArrivalDate(5_000, { ...activity, durationSeconds: null }), null);
    assert.equal(estimateArrivalDate(5_000, { ...activity, totalDistance: null }), null);
    assert.equal(estimateArrivalDate(5_000, { ...activity, totalDistance: 0 }), null);
  });
});

describe('isRouteEnd', () => {
  it('is true only on the first and the last point', () => {
    assert.equal(isRouteEnd(0, 12_000), true);
    assert.equal(isRouteEnd(12_000, 12_000), true);
    assert.equal(isRouteEnd(2, 12_000), false);
    assert.equal(isRouteEnd(11_998, 12_000), false);
  });

  it('knows no finish before a route is loaded', () => {
    assert.equal(isRouteEnd(500, undefined), false);
  });
});

describe('toggleDefaultsFromEstimate', () => {
  it('pre-fills the time of day, the duration since the start and the day', () => {
    const estimate = new Date(2024, 5, 1, 12, 34, 50);

    assert.deepEqual(toggleDefaultsFromEstimate(estimate, START), {
      initialTimeOfDay: { hours: 12, minutes: 34 },
      initialDurationSeconds: 2 * 3600 + 34 * 60 + 50,
      initialDayOffset: 0,
    });
  });

  it('puts an estimate past midnight on the following day', () => {
    const lateStart = new Date(2024, 5, 1, 22, 0);
    const estimate = new Date(2024, 5, 2, 1, 15);

    const defaults = toggleDefaultsFromEstimate(estimate, lateStart);

    assert.equal(defaults.initialDayOffset, 1);
    assert.deepEqual(defaults.initialTimeOfDay, { hours: 1, minutes: 15 });
    assert.equal(defaults.initialDurationSeconds, 3 * 3600 + 15 * 60);
  });

  it('pre-fills nothing without an estimate', () => {
    assert.deepEqual(toggleDefaultsFromEstimate(null, START), {});
  });

  it('keeps the time of day but no start-relative values without a start', () => {
    const estimate = new Date(2024, 5, 1, 12, 0);

    assert.deepEqual(toggleDefaultsFromEstimate(estimate, null), {
      initialTimeOfDay: { hours: 12, minutes: 0 },
    });
  });
});

describe('offsetSecondsOf', () => {
  it('keeps the offset of a time entered as a duration since the start', () => {
    assert.equal(offsetSecondsOf({ isValid: true, mode: 'duration', durationSeconds: 900 }), 900);
  });

  it('has none for a time of day, which stays a fixed clock time', () => {
    assert.equal(offsetSecondsOf({ isValid: true, mode: 'timeOfDay', durationSeconds: 900 }), undefined);
  });
});
