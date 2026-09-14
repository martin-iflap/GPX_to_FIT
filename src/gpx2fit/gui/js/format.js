// Shared formatting helpers for dates, durations, and distances. Kept in one
// place because the sidebar (anchors.js), the time toggle (timeInput.js),
// and the anchor-popover flow (anchorPopovers.js) all need to render the
// exact same "meters -> km" and "Date -> clock/duration" strings, and used
// to each carry their own slightly-drifting copy.

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
 * Formats a Date as a short local date + time, e.g. "Sep 5, 02:30 PM".
 * @param {Date} date
 * @returns {string}
 */
export function formatDateTime(date) {
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
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
 * Turns a caught error into a `{ message, kind }` pair for `setStatus`.
 * `kind: 'input'` (from `pyodideBridge.js`'s `classifyPyError` tagging an
 * error `isInputError`) means the problem traces back to something the user
 * entered — its message is already written for an end user, so it's shown
 * as-is rather than as a generic failure. Everything else is `kind: 'error'`:
 * an unexpected/internal failure, kept as the raw `Error: <message>` (a full
 * Python traceback, for a PythonError) since that detail is what makes it
 * debuggable.
 * @param {unknown} error
 * @returns {{ message: string, kind: 'input'|'error' }}
 */
export function describeError(error) {
  if (error && error.isInputError) {
    return { message: error.message, kind: 'input' };
  }
  return { message: `Error: ${error instanceof Error ? error.message : String(error)}`, kind: 'error' };
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
