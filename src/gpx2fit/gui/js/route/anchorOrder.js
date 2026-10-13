// Pure time-order rule for anything timed on the route: every timed point
// (anchor, photo, start/end stop) has to come later the further along the
// route it is, or combine() rejects the whole conversion. Checking it where
// a photo is placed or dragged, or where an anchor's or stop's time is typed,
// lets the GUI refuse that one point, and say why, instead. A photo's time is
// fixed, so its messages say where to put it; a typed time's say which way
// to change it. No DOM and no map here, so this is the part that gets unit
// tests; anchorPopovers.js and stopPopover.js show the result.

import { formatClockSeconds, formatDistanceKm } from '../util/format.js';

/**
 * A point on the route with a known time. A stop spans its arrival to its
 * departure; anything else has one time (`earliest` equals `latest`).
 * @typedef {{ kind: 'photo'|'anchor'|'stop', distanceFromStart: number, earliest: Date, latest: Date }} TimedPoint
 */

/**
 * @typedef {{ point: TimedPoint, issue: 'samePoint'|'sameTime'|'mustGoBefore'|'mustGoAfter' }} OrderConflict
 * `issue` is what's wrong with the new point relative to `point`: it's on
 * the very same point, it has the very same time (so neither side of it
 * would do), or it has to go before or after it.
 */

/**
 * The timed points to check against, from anchors.js's `getActiveAnchors()`
 * and stops.js's `getActiveStops()` (their times already resolved against the
 * current start). A duration-only stop has no clock time, so it can't be out
 * of order.
 *
 * @param {{ id: number, distanceFromStart: number, timestamp: Date, source: string }[]} anchors
 * @param {{ distanceFromStart: number, mode: string, arrival?: Date, departure?: Date }[]} stops
 * @param {{ excludeAnchorId?: number }} [opts] - the anchor being moved, which
 *   can't be in its own way
 * @returns {TimedPoint[]}
 */
export function timedPoints(anchors, stops, { excludeAnchorId } = {}) {
  const fromAnchors = anchors
    .filter((anchor) => anchor.id !== excludeAnchorId)
    .map((anchor) => /** @type {TimedPoint} */ ({
      kind: anchor.source === 'photo' ? 'photo' : 'anchor',
      distanceFromStart: anchor.distanceFromStart,
      earliest: anchor.timestamp,
      latest: anchor.timestamp,
    }));
  const fromStops = stops
    .filter((stop) => stop.mode === 'startEnd')
    .map((stop) => /** @type {TimedPoint} */ ({
      kind: 'stop',
      distanceFromStart: stop.distanceFromStart,
      earliest: stop.arrival,
      latest: stop.departure,
    }));
  return [...fromAnchors, ...fromStops];
}

/**
 * Whether a point at `distanceFromStart`, at `time`, would be out of time
 * order with any of `points`, and if so, which one is in the way. A new stop
 * spans `time` (its arrival) to `until` (its departure). Times must strictly
 * increase along the route, so the very same point is refused, and so is the
 * very same time anywhere.
 *
 * When several are in the way, the one named is the limit: going too far
 * forward, the first one passed (the point has to go before it); going too
 * far back, the last one (it has to go after it).
 *
 * @param {TimedPoint[]} points
 * @param {{ distanceFromStart: number, time: Date, until?: Date }} candidate
 * @returns {OrderConflict|null}
 */
export function findOrderConflict(points, { distanceFromStart, time, until = time }) {
  const arrivalMs = time.getTime();
  const departureMs = until.getTime();
  const samePoint = points.find((p) => p.distanceFromStart === distanceFromStart);
  if (samePoint) {
    return { point: samePoint, issue: 'samePoint' };
  }
  const sameTime = points.find((p) => [p.earliest.getTime(), p.latest.getTime()].some(
    (ms) => ms === arrivalMs || ms === departureMs,
  ));
  if (sameTime) {
    return { point: sameTime, issue: 'sameTime' };
  }
  // Behind it on the route, but not done before it arrives.
  const later = points
    .filter((p) => p.distanceFromStart < distanceFromStart && p.latest.getTime() > arrivalMs)
    .sort((a, b) => a.distanceFromStart - b.distanceFromStart);
  if (later.length > 0) {
    return { point: later[0], issue: 'mustGoBefore' };
  }
  // Ahead of it on the route, but already reached before it leaves.
  const earlier = points
    .filter((p) => p.distanceFromStart > distanceFromStart && p.earliest.getTime() < departureMs)
    .sort((a, b) => b.distanceFromStart - a.distanceFromStart);
  if (earlier.length > 0) {
    return { point: earlier[0], issue: 'mustGoAfter' };
  }
  return null;
}

/** @param {TimedPoint} point */
function pointName({ kind, distanceFromStart }) {
  return `${kind} at ${formatDistanceKm(distanceFromStart)}`;
}

/** @param {TimedPoint} point */
function pointTime({ earliest, latest }) {
  return earliest.getTime() === latest.getTime()
    ? formatClockSeconds(earliest)
    : `${formatClockSeconds(earliest)}–${formatClockSeconds(latest)}`;
}

/**
 * The full explanation, for the card shown when a photo can't go where it
 * was dropped or tapped.
 * @param {OrderConflict} conflict
 * @param {Date} time - the photo's capture time
 * @returns {string}
 */
export function describeOrderConflict({ point, issue }, time) {
  const name = pointName(point);
  switch (issue) {
    case 'samePoint':
      return `The ${name} is already on this point. Place this one a little before or after it.`;
    case 'sameTime':
      return `This photo was taken at ${formatClockSeconds(time)}, the same time as the ${name}. `
        + 'Two points on the route can\'t share a time.';
    default:
      return `This photo was taken at ${formatClockSeconds(time)}, but the ${name} is at ${pointTime(point)}. `
        + 'Points on the route have to follow each other in time, so place this one '
        + `${issue === 'mustGoBefore' ? 'before' : 'after'} ${formatDistanceKm(point.distanceFromStart)}.`;
  }
}

/**
 * The explanation for a time typed into the anchor or stop popover. That
 * point stays where it was tapped, so this says which way to move the *time*:
 * something behind it on the route and later (`mustGoBefore`) means later,
 * something ahead and earlier (`mustGoAfter`) means earlier.
 *
 * @param {OrderConflict} conflict
 * @param {{ span?: boolean }} [opts] - `span` for a stop: the arrival is what
 *   has to be later, and the departure what has to be earlier
 * @returns {string}
 */
export function describeTimeConflict({ point, issue }, { span = false } = {}) {
  const name = pointName(point);
  switch (issue) {
    case 'samePoint':
      return `The ${name} is already on this point. Pick a point a little before or after it.`;
    case 'sameTime':
      return `The ${name} is at ${pointTime(point)} too. Two points on the route can't share a time.`;
    case 'mustGoBefore':
      return `The ${name} is at ${pointTime(point)}, so ${span ? 'the arrival' : 'this time'} has to be later.`;
    default:
      return `The ${name} is at ${pointTime(point)}, so ${span ? 'the departure' : 'this time'} has to be earlier.`;
  }
}

/**
 * One line, for a pass in the "Which pass?" picker that can't be picked.
 * @param {OrderConflict} conflict
 * @returns {string}
 */
export function describeOrderConflictBriefly({ point, issue }) {
  const name = pointName(point);
  switch (issue) {
    case 'samePoint':
      return `The ${name} is already here`;
    case 'sameTime':
      return `Same time as the ${name}`;
    default:
      return `Out of time order with the ${name}`;
  }
}
