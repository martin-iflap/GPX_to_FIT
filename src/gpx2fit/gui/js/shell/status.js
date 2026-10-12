// The sidebar's status line: one message at a time, styled by kind. Every
// module that reports progress or a problem gets the `setStatus` this
// returns.

// How long an error or input problem stays before the line goes back to "Ready".
export const STATUS_ERROR_TIMEOUT_MS = 10000;

/**
 * @typedef {'error'|'input'|'warning'|false} StatusKind
 * `'error'` for an unexpected/internal failure (styled red; see describeError
 * in format.js), `'input'` for a problem traceable to something the user
 * entered or hasn't set up yet (styled amber, so it visibly reads as "fix
 * this" rather than "this app is broken"), `'warning'` for a step that
 * succeeded with a caveat (also amber; see statusWithWarnings in format.js),
 * or false for a normal status.
 */

/**
 * @param {HTMLElement} statusEl - the status line (`#status`)
 * @returns {(message: string, kind?: StatusKind) => void} setStatus
 */
export function createStatus(statusEl) {
  let timeoutId = null;

  /**
   * Errors are transient nudges, not permanent state, so they clear
   * themselves after a few seconds instead of sitting there until the next
   * unrelated status update happens to overwrite them. A warning stays: it
   * explains the route or file the user is looking at, and is gone with the
   * next status.
   *
   * @param {string} message
   * @param {StatusKind} [kind]
   */
  function setStatus(message, kind = false) {
    statusEl.textContent = message;
    statusEl.classList.toggle('status-error', kind === 'error');
    statusEl.classList.toggle('status-input', kind === 'input');
    statusEl.classList.toggle('status-warning', kind === 'warning');

    if (timeoutId) {
      clearTimeout(timeoutId);
      timeoutId = null;
    }
    if (kind === 'error' || kind === 'input') {
      timeoutId = setTimeout(() => {
        statusEl.textContent = 'Ready.';
        statusEl.classList.remove('status-error', 'status-input');
        timeoutId = null;
      }, STATUS_ERROR_TIMEOUT_MS);
    }
  }

  return setStatus;
}
