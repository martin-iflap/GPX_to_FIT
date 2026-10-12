// Pure time-order rule for placing a photo on the route: every timed point
// (anchor, photo, start/end stop) has to come later the further along the
// route it is, or combine() rejects the whole conversion. Checking it where
// the photo is placed or dragged lets the GUI refuse that one point, and say
// why, instead. No DOM and no map here, so this is the part that gets unit
// tests; anchorPopovers.js shows the result.

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
    .map((anchor) => ({
      kind: anchor.source === 'photo' ? 'photo' : 'anchor',
      distanceFromStart: anchor.distanceFromStart,
      earliest: anchor.timestamp,
      latest: anchor.timestamp,
    }));
  const fromStops = stops
    .filter((stop) => stop.mode === 'startEnd')
    .map((stop) => ({
      kind: 'stop',
      distanceFromStart: stop.distanceFromStart,
      earliest: stop.arrival,
      latest: stop.departure,
    }));
  return [...fromAnchors, ...fromStops];
}

/**
 * Whether a point at `distanceFromStart`, taken at `time`, would be out of
 * time order with any of `points`, and if so, which one is in the way.
 * Times must strictly increase along the route, so the very same point is
 * refused, and so is the very same time anywhere.
 *
 * When several are in the way, the one named is the limit: going too far
 * forward, the first one passed (the point has to go before it); going too
 * far back, the last one (it has to go after it).
 *
 * @param {TimedPoint[]} points
 * @param {{ distanceFromStart: number, time: Date }} candidate
 * @returns {OrderConflict|null}
 */
export function findOrderConflict(points, { distanceFromStart, time }) {
  const ms = time.getTime();
  const samePoint = points.find((p) => p.distanceFromStart === distanceFromStart);
  if (samePoint) {
    return { point: samePoint, issue: 'samePoint' };
  }
  const sameTime = points.find((p) => p.earliest.getTime() === ms || p.latest.getTime() === ms);
  if (sameTime) {
    return { point: sameTime, issue: 'sameTime' };
  }
  // Behind it on the route, but not done before its time.
  const later = points
    .filter((p) => p.distanceFromStart < distanceFromStart && p.latest.getTime() > ms)
    .sort((a, b) => a.distanceFromStart - b.distanceFromStart);
  if (later.length > 0) {
    return { point: later[0], issue: 'mustGoBefore' };
  }
  // Ahead of it on the route, but already reached before its time.
  const earlier = points
    .filter((p) => p.distanceFromStart > distanceFromStart && p.earliest.getTime() < ms)
    .sort((a, b) => b.distanceFromStart - a.distanceFromStart);
  if (earlier.length > 0) {
    return { point: earlier[0], issue: 'mustGoAfter' };
  }
  return null;
}

function pointName({ kind, distanceFromStart }) {
  return `${kind} at ${formatDistanceKm(distanceFromStart)}`;
}

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
