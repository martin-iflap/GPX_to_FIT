// The app's shared state: the parsed route, the chosen sport, and the start
// time with the start-time control's result. main.js creates one and writes
// to it; the modules that need to read it (the anchor placer, the photo drop)
// get it once, instead of a getter per value. Not a framework: plain fields,
// one setter each, and one listener list for the one change other modules
// react to, the activity window.

import { sameWindow } from '../route/activityWindow.js';

/**
 * @typedef {{ points: {lat: number, lon: number}[], totalDistance: number, fileName: string }} Route
 * @typedef {import('../inputs/timeInput.js').TimeResult} TimeResult
 * @typedef {import('../route/activityWindow.js').ActivityWindow} ActivityWindow
 * @typedef {'running'|'hiking'} Sport - the GUI's values, which bridge_api reads as SportType
 */

/**
 * @param {object} [initial]
 * @param {Sport} [initial.sport] - the sport tab selected in the HTML
 */
export function createAppState({ sport = 'hiking' } = {}) {
  /** @type {Route|null} */
  let route = null;
  /** @type {Sport} */
  let currentSport = sport;
  /** @type {Date|null} */
  let startTime = null;
  /** @type {TimeResult} */
  let timeResult = { isValid: false };
  /** @type {ActivityWindow|null} */
  let lastWindow = null;
  /** @type {((activityWindow: ActivityWindow|null) => void)[]} */
  const windowListeners = [];

  function activityWindow() {
    return timeResult.isValid && startTime ? { start: startTime, end: timeResult.resolvedDate } : null;
  }

  return {
    /** The most recently parsed GPX, or null before one has parsed. */
    get route() {
      return route;
    },
    /**
     * Set only once a parse succeeds, so a failed upload leaves the loaded
     * route (and the name the FIT download is given) as it was.
     * @param {Route} nextRoute
     */
    setRoute(nextRoute) {
      route = nextRoute;
    },

    /** @returns {Sport} */
    get sport() {
      return currentSport;
    },
    /** @param {Sport} nextSport */
    setSport(nextSport) {
      currentSport = nextSport;
    },

    /** The start time, as of the start-time control's last result; null while unset. */
    get startTime() {
      return startTime;
    },
    /** @returns {TimeResult} the start-time control's last result */
    get timeResult() {
      return timeResult;
    },
    /**
     * Records the start time together with the control's result for it, so
     * the two can never disagree: in duration mode the end is start +
     * duration, and a start stored before its result would pair with the old
     * end. Notifies the window listeners if the window changed.
     *
     * @param {Date|null} start
     * @param {TimeResult} result
     */
    setTime(start, result) {
      startTime = start;
      timeResult = result;
      const next = activityWindow();
      // The control reports every change, including ones that leave the
      // window as it was (a sport switch, an added stop), and a listener
      // re-renders a whole list.
      if (sameWindow(lastWindow, next)) {
        return;
      }
      lastWindow = next;
      windowListeners.forEach((fn) => fn(next));
    },

    /**
     * The activity's start–end, or null while the start or the duration isn't
     * valid. Anchors and stops are only used while strictly inside it.
     * @returns {ActivityWindow|null}
     */
    activityWindow,

    /** @returns {number|null} the activity's whole duration in seconds, or null while it isn't valid */
    durationSeconds() {
      return timeResult.isValid ? timeResult.durationSeconds : null;
    },

    /**
     * Registers a listener for a changed activity window. It isn't called
     * for the current window: the lists start out empty, so there is nothing
     * to re-check yet.
     * @param {(activityWindow: ActivityWindow|null) => void} fn
     */
    onWindowChange(fn) {
      windowListeners.push(fn);
    },
  };
}
