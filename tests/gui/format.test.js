import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  describeError,
  fitFileNameFromGpx,
  formatClock,
  formatDateTime,
  formatDistanceKm,
  formatDuration,
  formatElapsed,
  formatFileSize,
  formatPace,
  formatSpeedKmh,
  leftOutNote,
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

describe('formatElapsed', () => {
  it('formats as H:MM', () => {
    assert.equal(formatElapsed(3725), '1:02');
    assert.equal(formatElapsed(59 * 60), '0:59');
  });

  it('clamps negative input to zero', () => {
    assert.equal(formatElapsed(-5), '0:00');
  });
});

describe('formatSpeedKmh', () => {
  it('converts m/s to km/h with one decimal', () => {
    assert.equal(formatSpeedKmh(1.25), '4.5 km/h');
    assert.equal(formatSpeedKmh(0), '0.0 km/h');
  });
});

describe('formatPace', () => {
  it('converts m/s to min:ss per km', () => {
    assert.equal(formatPace(2.5), '6:40 /km');
    assert.equal(formatPace(1000 / 300), '5:00 /km');
  });

  it('has no finite pace at zero speed', () => {
    assert.equal(formatPace(0), '–:–– /km');
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

describe('fitFileNameFromGpx', () => {
  it('reuses the GPX name with a .fit extension', () => {
    assert.equal(fitFileNameFromGpx('Mont Blanc loop.gpx'), 'Mont Blanc loop.fit');
  });

  it('matches the extension case-insensitively', () => {
    assert.equal(fitFileNameFromGpx('Morning run.GPX'), 'Morning run.fit');
  });

  it('keeps a name that has no extension at all', () => {
    assert.equal(fitFileNameFromGpx('Morning run'), 'Morning run.fit');
  });

  it('keeps dots inside the name', () => {
    assert.equal(fitFileNameFromGpx('Stage 2.1 - ridge.gpx'), 'Stage 2.1 - ridge.fit');
  });

  it('falls back to activity.fit for generic export names', () => {
    for (const name of ['export.gpx', 'Export.gpx', 'track.gpx', 'route.gpx', 'untitled.gpx']) {
      assert.equal(fitFileNameFromGpx(name), 'activity.fit');
    }
  });

  it('treats underscores and spaces as separators when matching generic names', () => {
    assert.equal(fitFileNameFromGpx('my_track.gpx'), 'activity.fit');
    assert.equal(fitFileNameFromGpx('GPX Export.gpx'), 'activity.fit');
  });

  it('ignores a trailing copy or id number when matching generic names', () => {
    assert.equal(fitFileNameFromGpx('export (1).gpx'), 'activity.fit');
    assert.equal(fitFileNameFromGpx('export-2.gpx'), 'activity.fit');
    assert.equal(fitFileNameFromGpx('activity_12345678.gpx'), 'activity.fit');
  });

  it('keeps a real name that merely ends in a number', () => {
    assert.equal(fitFileNameFromGpx('Ben Nevis 2024.gpx'), 'Ben Nevis 2024.fit');
  });

  it('falls back when there is no name', () => {
    assert.equal(fitFileNameFromGpx(null), 'activity.fit');
    assert.equal(fitFileNameFromGpx(undefined), 'activity.fit');
    assert.equal(fitFileNameFromGpx(''), 'activity.fit');
    assert.equal(fitFileNameFromGpx('.gpx'), 'activity.fit');
  });

  it('strips characters that are not valid in a file name', () => {
    assert.equal(fitFileNameFromGpx('A/B: ridge?.gpx'), 'AB ridge.fit');
  });

  it('trims surrounding whitespace and trailing dots', () => {
    assert.equal(fitFileNameFromGpx('  Ridge walk ...gpx  '), 'Ridge walk.fit');
  });

  it('truncates an overlong name', () => {
    const longName = `${'a'.repeat(150)}.gpx`;
    assert.equal(fitFileNameFromGpx(longName), `${'a'.repeat(100)}.fit`);
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

describe('leftOutNote', () => {
  it('says nothing when nothing was left out', () => {
    assert.equal(leftOutNote(0, 0), '');
  });

  it('names a single anchor or stop in the singular', () => {
    assert.equal(leftOutNote(1, 0), " 1 anchor outside the activity's time was left out.");
    assert.equal(leftOutNote(0, 1), " 1 stop outside the activity's time was left out.");
  });

  it('uses plurals and joins both kinds', () => {
    assert.equal(leftOutNote(2, 0), " 2 anchors outside the activity's time were left out.");
    assert.equal(leftOutNote(2, 3), " 2 anchors and 3 stops outside the activity's time were left out.");
    assert.equal(leftOutNote(1, 1), " 1 anchor and 1 stop outside the activity's time were left out.");
  });
});
