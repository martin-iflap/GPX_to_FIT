// Pure date logic deciding whether a placed anchor or stop still falls inside
// the activity's current start–end window. anchors.js and stops.js re-check
// every item against it whenever the start time or duration changes, so an
// anchor that no longer fits is shown as inactive and left out of convert()
// instead of blocking the whole conversion with combine()'s ordering error.
// No DOM and no map here, so this is the part that gets unit tests.

/** @typedef {{ start: Date, end: Date }} ActivityWindow */

/**
 * True when `date` lies strictly between the window's start and end. The
 * edges are excluded because the start and end are anchors themselves, at
 * the route's first and last point: any other anchor at exactly that time
 * would contradict its distance along the route. A `null` window (no valid
 * start/duration yet) is no constraint at all.
 *
 * @param {Date} date
 * @param {ActivityWindow|null} window
 * @returns {boolean}
 */
export function isStrictlyInside(date, window) {
  if (!window) {
    return true;
  }
  return window.start.getTime() < date.getTime() && date.getTime() < window.end.getTime();
}

/**
 * True when both windows have the same start and end times (or are both
 * null). The start-time control reports every change, including ones that
 * leave the window as it was (a sport change, a stop added), so this lets
 * those skip a re-render.
 *
 * @param {ActivityWindow|null} a
 * @param {ActivityWindow|null} b
 * @returns {boolean}
 */
export function sameWindow(a, b) {
  if (!a || !b) {
    return a === b;
  }
  return a.start.getTime() === b.start.getTime() && a.end.getTime() === b.end.getTime();
}

function resolveTime(timestamp, offsetSeconds, start) {
  if (offsetSeconds == null || !start) {
    return timestamp;
  }
  return new Date(start.getTime() + offsetSeconds * 1000);
}

/**
 * An anchor's time right now. One entered as "Duration since start" keeps
 * its `offsetSeconds` and moves with the start; one entered as a time of day,
 * or read from a photo, is a fixed clock time.
 *
 * @param {{ timestamp: Date, offsetSeconds?: number }} anchor
 * @param {Date|null} start - the current start time, or null while unset
 * @returns {Date}
 */
export function effectiveAnchorTime(anchor, start) {
  return resolveTime(anchor.timestamp, anchor.offsetSeconds, start);
}

/**
 * A start/end stop's arrival and departure right now, each resolved on its
 * own (the popover lets either side be entered relative to the start).
 *
 * @param {{ arrival: Date, departure: Date, arrivalOffsetSeconds?: number, departureOffsetSeconds?: number }} stop
 * @param {Date|null} start
 * @returns {{ arrival: Date, departure: Date }}
 */
export function effectiveStopTimes(stop, start) {
  return {
    arrival: resolveTime(stop.arrival, stop.arrivalOffsetSeconds, start),
    departure: resolveTime(stop.departure, stop.departureOffsetSeconds, start),
  };
}

/**
 * @param {{ timestamp: Date, offsetSeconds?: number }} anchor
 * @param {ActivityWindow|null} window
 * @returns {boolean}
 */
export function isAnchorActive(anchor, window) {
  return isStrictlyInside(effectiveAnchorTime(anchor, window?.start ?? null), window);
}

/**
 * A duration-only stop has no clock time, so it always counts. A start/end
 * stop counts only while both its arrival and departure are inside.
 *
 * @param {{ mode: 'duration'|'startEnd', arrival?: Date, departure?: Date }} stop
 * @param {ActivityWindow|null} window
 * @returns {boolean}
 */
export function isStopActive(stop, window) {
  if (stop.mode !== 'startEnd') {
    return true;
  }
  const { arrival, departure } = effectiveStopTimes(stop, window?.start ?? null);
  return isStrictlyInside(arrival, window) && isStrictlyInside(departure, window);
}
