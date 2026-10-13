// Shared formatting helpers for dates, durations, distances, and file names.
// Kept in one place because the sidebar (anchors.js), the time toggle
// (timeInput.js), and the anchor-popover flow (anchorPopovers.js) all need to
// render the exact same "meters -> km" and "Date -> clock/duration" strings,
// and used to each carry their own slightly-drifting copy.

/**
 * Zero-pads a non-negative integer to at least 2 digits, e.g. pad(5) -> "05".
 * @param {number} n
 * @returns {string}
 */
export function pad(n) {
  return String(n).padStart(2, '0');
}

/**
 * Formats a Date as a 24-hour "HH:MM" clock string in local time.
 * @param {Date} date
 * @returns {string}
 */
export function formatClock(date) {
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/**
 * Formats a Date as "HH:MM:SS" in local time: a photo's capture time, which
 * is exact to the second.
 * @param {Date} date
 * @returns {string}
 */
export function formatClockSeconds(date) {
  return `${formatClock(date)}:${pad(date.getSeconds())}`;
}

/**
 * Formats a Date as a short local date + time, e.g. "Sep 5, 14:30". Always a
 * 24-hour clock, like formatClock, so anchor and stop rows read alike.
 * @param {Date} date
 * @returns {string}
 */
export function formatDateTime(date) {
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  });
}

/**
 * Formats a duration given in seconds as "Xh MMm". Negative input clamps to zero.
 * @param {number} totalSeconds
 * @returns {string}
 */
export function formatDuration(totalSeconds) {
  const clamped = Math.max(0, Math.round(totalSeconds));
  const hours = Math.floor(clamped / 3600);
  const minutes = Math.floor((clamped % 3600) / 60);
  return `${hours}h ${pad(minutes)}m`;
}

/**
 * Formats a distance given in meters as kilometers with 2 decimal places, e.g. "12.34 km".
 * @param {number} meters
 * @returns {string}
 */
export function formatDistanceKm(meters) {
  return `${(meters / 1000).toFixed(2)} km`;
}

/**
 * Formats a photo's distance from the route: whole meters below 1 km,
 * one-decimal kilometers above, e.g. 250 -> "250 m", 2500 -> "2.5 km".
 * @param {number} meters
 * @returns {string}
 */
export function formatGapMeters(meters) {
  if (meters >= 1000) {
    return `${(meters / 1000).toFixed(1)} km`;
  }
  return `${Math.round(meters)} m`;
}

/**
 * Formats elapsed seconds as "H:MM", e.g. 3725 -> "1:02". Negative input clamps to zero.
 * @param {number} totalSeconds
 * @returns {string}
 */
export function formatElapsed(totalSeconds) {
  const clamped = Math.max(0, Math.round(totalSeconds));
  const hours = Math.floor(clamped / 3600);
  const minutes = Math.floor((clamped % 3600) / 60);
  return `${hours}:${pad(minutes)}`;
}

/**
 * Formats a speed in m/s as km/h with one decimal, e.g. 1.25 -> "4.5 km/h".
 * @param {number} metersPerSecond
 * @returns {string}
 */
export function formatSpeedKmh(metersPerSecond) {
  return `${(metersPerSecond * 3.6).toFixed(1)} km/h`;
}

/**
 * Formats a speed in m/s as a running pace, e.g. 2.5 -> "6:40 /km". A zero or
 * negative speed has no finite pace and formats as "–:–– /km".
 * @param {number} metersPerSecond
 * @returns {string}
 */
export function formatPace(metersPerSecond) {
  if (!(metersPerSecond > 0)) {
    return '–:–– /km';
  }
  return `${formatPaceClock(1000 / metersPerSecond)} /km`;
}

/**
 * Formats a pace given in seconds per km as "m:ss", without a unit (the
 * profile chart's ticks), e.g. 400 -> "6:40". Rounds to the whole second.
 * @param {number} secondsPerKm
 * @returns {string}
 */
export function formatPaceClock(secondsPerKm) {
  const rounded = Math.round(secondsPerKm);
  return `${Math.floor(rounded / 60)}:${pad(rounded % 60)}`;
}

/**
 * Turns a caught error into a `{ message, kind }` pair for `setStatus`.
 * `kind: 'input'` (from `pyodideBridge.js`'s `classifyPyError` tagging an
 * error `isInputError`) means the problem traces back to something the user
 * entered — its message is already written for an end user, so it's shown
 * as-is rather than as a generic failure. Everything else is `kind: 'error'`:
 * an unexpected/internal failure, kept as the raw `Error: <message>` (a full
 * Python traceback, for a PythonError) since that detail is what makes it
 * debuggable.
 * @param {any} error
 * @returns {{ message: string, kind: 'input'|'error' }}
 */
export function describeError(error) {
  if (error && error.isInputError) {
    return { message: error.message, kind: 'input' };
  }
  return { message: `Error: ${error instanceof Error ? error.message : String(error)}`, kind: 'error' };
}

export const DEFAULT_FIT_FILE_NAME = 'activity.fit';

// Base names that carry no information about the route: what an export button
// or a "save as" dialog picks when nobody named the file. Naming the FIT after
// one of these would just rename "export.fit" for every route, so these fall
// back to DEFAULT_FIT_FILE_NAME instead. Compared after normalization (see
// isGenericGpxName), so spacing/underscores/case/copy suffixes don't matter.
const GENERIC_GPX_NAMES = new Set([
  'activities',
  'activity',
  'course',
  'courses',
  'download',
  'export',
  'exported',
  'gpx',
  'gpx-export',
  'gpx-track',
  'my-activity',
  'my-route',
  'my-track',
  'new-route',
  'route',
  'routes',
  'track',
  'trackpoints',
  'tracks',
  'untitled',
  'waypoints',
  'workout',
]);

// Characters Windows rejects in a file name, plus control characters. Stripped
// rather than substituted: the GPX name is already a valid file name on the
// user's own machine, so this only guards against a hand-crafted `File` name.
// eslint-disable-next-line no-control-regex
const UNSAFE_FILE_NAME_CHARS = /[<>:"/\\|?*\x00-\x1f]/g;

const MAX_FIT_BASE_NAME_LENGTH = 100;

/**
 * Strips a trailing `.gpx` extension (case-insensitive) from a file name.
 * @param {string} fileName
 * @returns {string}
 */
function stripGpxExtension(fileName) {
  return fileName.replace(/\.gpx$/i, '');
}

/**
 * Decides whether a GPX base name is one of the generic export defaults.
 * Normalizes first so "Export (1)", "my_track", and "activity 12345678"
 * (Strava's numbered export) all collapse onto a GENERIC_GPX_NAMES entry:
 * lowercase, spaces/underscores to hyphens, and a trailing copy/id number
 * dropped. The normalization is only used for this check — the name the user
 * actually sees keeps its original spelling.
 * @param {string} baseName
 * @returns {boolean}
 */
function isGenericGpxName(baseName) {
  const normalized = baseName
    .toLowerCase()
    .replace(/[\s_]+/g, '-')
    .replace(/\((\d+)\)$/, '')
    .replace(/-*\d+$/, '')
    .replace(/^-+|-+$/g, '');
  return normalized === '' || GENERIC_GPX_NAMES.has(normalized);
}

/**
 * Picks the download name for the generated FIT file: the uploaded GPX's own
 * name with a `.fit` extension, e.g. "Mont Blanc loop.gpx" -> "Mont Blanc
 * loop.fit". Falls back to DEFAULT_FIT_FILE_NAME when there is no GPX name or
 * when its name is a generic export default (see GENERIC_GPX_NAMES) that would
 * say nothing about the route.
 * @param {string|null|undefined} gpxFileName
 * @returns {string}
 */
export function fitFileNameFromGpx(gpxFileName) {
  if (typeof gpxFileName !== 'string') {
    return DEFAULT_FIT_FILE_NAME;
  }
  const baseName = stripGpxExtension(gpxFileName.trim())
    .replace(UNSAFE_FILE_NAME_CHARS, '')
    .replace(/\s+/g, ' ')
    // Windows also silently drops trailing dots and spaces from a file name.
    .replace(/^[\s.]+|[\s.]+$/g, '')
    .slice(0, MAX_FIT_BASE_NAME_LENGTH)
    .trimEnd();
  if (baseName === '' || isGenericGpxName(baseName)) {
    return DEFAULT_FIT_FILE_NAME;
  }
  return `${baseName}.fit`;
}

const FILE_SIZE_UNITS = ['bytes', 'KB', 'MB', 'GB'];

/**
 * Formats a byte count in the largest unit that keeps it >= 1, e.g.
 * formatFileSize(2400) -> "2.3 KB". Whole byte counts skip the decimal.
 * @param {number} bytes
 * @returns {string}
 */
export function formatFileSize(bytes) {
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < FILE_SIZE_UNITS.length - 1) {
    value /= 1024;
    unitIndex++;
  }
  const formatted = unitIndex === 0 ? String(value) : value.toFixed(1);
  return `${formatted} ${FILE_SIZE_UNITS[unitIndex]}`;
}

/**
 * A success status with any warnings from the Python side appended (a gap
 * between GPX segments, an implausible segment pace, a failed surface
 * lookup). Warnings make it a `'warning'` status, which main.js shows in amber
 * and doesn't clear on a timer, since it explains the file just produced.
 *
 * @param {string} baseMessage
 * @param {string[]} [warnings]
 * @returns {{ message: string, kind: 'warning' | false }}
 */
export function statusWithWarnings(baseMessage, warnings) {
  if (!warnings || warnings.length === 0) {
    return { message: baseMessage, kind: false };
  }
  return { message: [baseMessage, ...warnings].join(' '), kind: 'warning' };
}

/**
 * The sentence appended to a successful conversion's status when some
 * anchors or stops were left out for falling outside the activity's time
 * (see activityWindow.js). Empty when nothing was left out; otherwise starts
 * with a space, so it can be appended directly.
 *
 * @param {number} anchorCount
 * @param {number} stopCount
 * @returns {string}
 */
export function leftOutNote(anchorCount, stopCount) {
  const parts = [];
  if (anchorCount > 0) {
    parts.push(`${anchorCount} ${anchorCount === 1 ? 'anchor' : 'anchors'}`);
  }
  if (stopCount > 0) {
    parts.push(`${stopCount} ${stopCount === 1 ? 'stop' : 'stops'}`);
  }
  if (parts.length === 0) {
    return '';
  }
  const verb = anchorCount + stopCount === 1 ? 'was' : 'were';
  return ` ${parts.join(' and ')} outside the activity's time ${verb} left out.`;
}
