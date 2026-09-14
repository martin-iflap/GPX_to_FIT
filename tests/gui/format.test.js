import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  describeError,
  formatClock,
  formatDateTime,
  formatDistanceKm,
  formatDuration,
  formatFileSize,
  pad,
} from '../../src/gpx2fit/gui/js/format.js';

describe('pad', () => {
  it('zero-pads single digits', () => {
    assert.equal(pad(5), '05');
  });

  it('leaves two-digit numbers unchanged', () => {
    assert.equal(pad(23), '23');
  });
});

describe('formatClock', () => {
  it('formats hours and minutes as 24h HH:MM', () => {
    assert.equal(formatClock(new Date(2024, 0, 1, 9, 5)), '09:05');
    assert.equal(formatClock(new Date(2024, 0, 1, 23, 59)), '23:59');
  });
});

describe('formatDateTime', () => {
  it('includes month, day, hour, and minute', () => {
    const formatted = formatDateTime(new Date(2024, 8, 5, 14, 30));
    assert.ok(formatted.includes('Sep'));
    assert.ok(formatted.includes('5'));
    assert.match(formatted, /2:30|14:30/);
  });
});

describe('formatDuration', () => {
  it('clamps negative input to zero', () => {
    assert.equal(formatDuration(-100), '0h 00m');
  });

  it('formats a whole number of hours and minutes', () => {
    assert.equal(formatDuration(3661), '1h 01m');
  });

  it('rounds before dividing at the hour boundary', () => {
    // 3599.6 rounds to 3600s, i.e. exactly 1h 00m, not 0h 60m.
    assert.equal(formatDuration(3599.6), '1h 00m');
  });

  it('formats zero as 0h 00m', () => {
    assert.equal(formatDuration(0), '0h 00m');
  });
});

describe('formatDistanceKm', () => {
  it('converts meters to km with 2 decimals', () => {
    assert.equal(formatDistanceKm(12345), '12.35 km');
  });

  it('formats zero meters', () => {
    assert.equal(formatDistanceKm(0), '0.00 km');
  });
});

describe('formatFileSize', () => {
  it('shows whole byte counts without a decimal', () => {
    assert.equal(formatFileSize(500), '500 bytes');
  });

  it('formats zero bytes', () => {
    assert.equal(formatFileSize(0), '0 bytes');
  });

  it('crosses into KB at exactly 1024', () => {
    assert.equal(formatFileSize(1024), '1.0 KB');
  });

  it('stays in bytes just below the 1024 boundary', () => {
    assert.equal(formatFileSize(1023), '1023 bytes');
  });

  it('crosses into MB at exactly 1024^2', () => {
    assert.equal(formatFileSize(1024 * 1024), '1.0 MB');
  });

  it('caps at GB rather than continuing past it', () => {
    assert.equal(formatFileSize(1024 * 1024 * 1024 * 1024), '1024.0 GB');
  });
});

describe('describeError', () => {
  it('shows the message as-is and kind "input" for an isInputError-tagged error', () => {
    const error = new Error('Anchor at 4.2km is earlier than the anchor before it.');
    error.isInputError = true;
    assert.deepEqual(describeError(error), {
      message: 'Anchor at 4.2km is earlier than the anchor before it.',
      kind: 'input',
    });
  });

  it('prefixes a generic Error with "Error:" and kind "error"', () => {
    assert.deepEqual(describeError(new Error('boom')), { message: 'Error: boom', kind: 'error' });
  });

  it('stringifies a non-Error thrown value', () => {
    assert.deepEqual(describeError('plain string'), { message: 'Error: plain string', kind: 'error' });
  });
});
