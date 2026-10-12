// Calendar-day arithmetic in local time, shared by the time inputs and the
// anchor and stop popovers. Days are counted between local midnights and
// rounded, so a 23- or 25-hour day around a DST change still counts as one.

import { pad } from './format.js';

const DAY_MS = 86_400_000;

function localMidnight(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

/**
 * How many calendar days after `reference`'s day `target`'s day falls on
 * (0 if same day, negative if earlier), e.g. to pre-select the right day in
 * an anchor or stop popover's day selector for a multi-day activity.
 * @param {Date} reference
 * @param {Date} target
 * @returns {number}
 */
export function dayOffsetBetween(reference, target) {
  return Math.round((localMidnight(target).getTime() - localMidnight(reference).getTime()) / DAY_MS);
}

/**
 * How many distinct calendar days `referenceStart` through
 * `referenceStart + totalDurationSeconds` spans, e.g. a start at 22:00 plus
 * a 4-hour duration spans 2 calendar days despite being under 24h. Returns
 * 1 (i.e. "single-day, no day selector needed") if the duration is unknown.
 * @param {Date} referenceStart
 * @param {number|null|undefined} totalDurationSeconds
 * @returns {number}
 */
export function computeDayCount(referenceStart, totalDurationSeconds) {
  if (!Number.isFinite(totalDurationSeconds) || totalDurationSeconds <= 0) {
    return 1;
  }
  const end = new Date(referenceStart.getTime() + totalDurationSeconds * 1000);
  return dayOffsetBetween(referenceStart, end) + 1;
}

/**
 * A Date's local calendar day as an `<input type="date">` value, "YYYY-MM-DD".
 * @param {Date} date
 * @returns {string}
 */
export function toDateInputValue(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}
