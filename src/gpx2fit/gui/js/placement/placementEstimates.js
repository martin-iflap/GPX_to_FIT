// Pure helpers behind the placement popovers: a rough arrival-time estimate
// for a point on the route, the time controls' pre-fill from it, and the
// route-end rule. No DOM and no map here, so this is the part that gets unit
// tests; anchorPopovers.js and stopPopover.js show the results.

import { dayOffsetBetween } from '../util/dates.js';

/**
 * When a point would be reached at one uniform pace over the whole activity.
 * A ballpark only: it helps tell two passes of an out-and-back apart and seeds
 * a time field the user corrects. It has nothing to do with the gradient-based
 * pacing, which only runs at Convert, once every anchor is known.
 *
 * @param {number} distanceFromStart
 * @param {{ start: Date|null, durationSeconds: number|null, totalDistance: number|null|undefined }} activity
 * @returns {Date|null} null while the start, the duration or the route is missing
 */
export function estimateArrivalDate(distanceFromStart, { start, durationSeconds, totalDistance }) {
  if (!totalDistance || durationSeconds === null || !start) {
    return null;
  }
  const fraction = distanceFromStart / totalDistance;
  return new Date(start.getTime() + fraction * durationSeconds * 1000);
}

/**
 * The route's first and last point already carry the start and end time, so
 * another anchor, stop or photo there would contradict them.
 *
 * @param {number} distanceFromStart
 * @param {number|undefined} totalDistance - undefined before a route is loaded
 * @returns {boolean}
 */
export function isRouteEnd(distanceFromStart, totalDistance) {
  return distanceFromStart === 0 || distanceFromStart === totalDistance;
}

/**
 * The `createTimeToggle` pre-fill for a time picked near `estimate`: its time
 * of day (which also opens the toggle on that tab), its offset from the start,
 * and which day of a multi-day activity it falls on. Empty without an
 * estimate; without a start only the time of day is known.
 *
 * @param {Date|null} estimate
 * @param {Date|null} start
 * @returns {{ initialTimeOfDay?: {hours: number, minutes: number}, initialDurationSeconds?: number, initialDayOffset?: number }}
 */
export function toggleDefaultsFromEstimate(estimate, start) {
  if (!estimate) {
    return {};
  }
  const initialTimeOfDay = { hours: estimate.getHours(), minutes: estimate.getMinutes() };
  if (!start) {
    return { initialTimeOfDay };
  }
  return {
    initialTimeOfDay,
    initialDurationSeconds: (estimate.getTime() - start.getTime()) / 1000,
    initialDayOffset: dayOffsetBetween(start, estimate),
  };
}

/**
 * A result's offset from the start, for a time entered as "Duration since
 * start": stored so the anchor or stop follows later start changes. Absent
 * for a time of day, which is a fixed clock time.
 *
 * @param {import('../inputs/timeInput.js').TimeResult} result - a `createTimeToggle` result
 * @returns {number|undefined}
 */
export function offsetSecondsOf(result) {
  return result.isValid && result.mode === 'duration' ? result.durationSeconds : undefined;
}
