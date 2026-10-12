// The few settings the app remembers across visits (theme, surface lookup,
// pace smoothness, device), listed in about.html#privacy. localStorage can
// throw or come back empty (a private window, blocked or cleared site data),
// and a remembered setting is only ever a convenience, so both helpers swallow
// that: a read falls back, and a failed write just isn't remembered this time.
// about.html's inline theme script can't import a module and keeps its own
// try/catch.

/**
 * Reads a stored setting, or `null` when there is none or storage is blocked.
 * Callers validate the value themselves: anything stored may be stale or
 * hand-edited.
 * @param {string} key
 * @returns {string|null}
 */
export function readSetting(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

/**
 * Stores a setting. Returns whether it was stored; a failure is not an error,
 * since the setting still applies to this visit.
 * @param {string} key
 * @param {string} value
 * @returns {boolean}
 */
export function writeSetting(key, value) {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}
