import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { computeDayCount, dayOffsetBetween, toDateInputValue } from '../../../src/gpx2fit/gui/js/util/dates.js';

// Pinned so the DST cases mean the same on every machine: Node reads TZ
// afresh for each Date conversion.
process.env.TZ = 'Europe/Prague';

describe('dayOffsetBetween', () => {
  it('is 0 on the same calendar day, whatever the times', () => {
    assert.equal(dayOffsetBetween(new Date(2024, 5, 1, 0, 0), new Date(2024, 5, 1, 23, 59)), 0);
    assert.equal(dayOffsetBetween(new Date(2024, 5, 1, 23, 59), new Date(2024, 5, 1, 0, 0)), 0);
  });

  it('counts calendar days, not 24-hour spans', () => {
    assert.equal(dayOffsetBetween(new Date(2024, 5, 1, 22, 0), new Date(2024, 5, 2, 0, 15)), 1);
    assert.equal(dayOffsetBetween(new Date(2024, 5, 1, 8, 0), new Date(2024, 5, 4, 7, 0)), 3);
  });

  it('goes negative for a day before the reference', () => {
    assert.equal(dayOffsetBetween(new Date(2024, 5, 2, 6, 0), new Date(2024, 5, 1, 23, 50)), -1);
  });

  // Spring forward makes 31 March 2024 a 23-hour day in Prague, so its
  // midnight and the next are 23 h apart: still one calendar day.
  it('counts a day across a daylight-saving change as one day', () => {
    assert.equal(dayOffsetBetween(new Date(2024, 2, 31, 22, 0), new Date(2024, 3, 1, 9, 0)), 1);
    assert.equal(dayOffsetBetween(new Date(2024, 9, 27, 22, 0), new Date(2024, 9, 28, 9, 0)), 1);
  });
});

describe('computeDayCount', () => {
  const reference = new Date(2024, 0, 1, 22, 0); // Jan 1, 22:00

  it('returns 1 when the duration is unknown, zero, or negative', () => {
    assert.equal(computeDayCount(reference, null), 1);
    assert.equal(computeDayCount(reference, undefined), 1);
    assert.equal(computeDayCount(reference, 0), 1);
    assert.equal(computeDayCount(reference, -100), 1);
  });

  it('returns 1 for a duration that stays within the same calendar day', () => {
    assert.equal(computeDayCount(new Date(2024, 0, 1, 8, 0), 3600), 1);
  });

  it('returns 2 for an overnight span crossing midnight, even under 24h', () => {
    // 22:00 + 4h = 02:00 the next day.
    assert.equal(computeDayCount(reference, 4 * 3600), 2);
  });

  it('returns the correct count for a multi-day span', () => {
    // Jan 1 08:00 + 50h lands on Jan 3.
    assert.equal(computeDayCount(new Date(2024, 0, 1, 8, 0), 50 * 3600), 3);
  });

  // Spring forward: 31 March 2024 is a 23-hour day in Prague, so 30 March
  // 22:00 + 24 h is 31 March 23:00, and + 25 h is already 1 April 00:00.
  it('counts calendar days across a daylight-saving change', () => {
    assert.equal(computeDayCount(new Date(2024, 2, 30, 22, 0), 24 * 3600), 2);
    assert.equal(computeDayCount(new Date(2024, 2, 30, 22, 0), 25 * 3600), 3);
  });
});

describe('toDateInputValue', () => {
  it('writes the local calendar day as YYYY-MM-DD, zero-padded', () => {
    assert.equal(toDateInputValue(new Date(2024, 0, 5, 23, 59)), '2024-01-05');
    assert.equal(toDateInputValue(new Date(2024, 11, 31, 0, 0)), '2024-12-31');
  });
});
