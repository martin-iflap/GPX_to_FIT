// Whether a time placed on the route can be used there: strictly inside the
// activity's start–end (activityWindow.js), and in time order with the
// anchors and stops convert() would use (anchorOrder.js). The anchor, stop
// and photo popovers all check through here, so each refuses a bad time when
// it's entered instead of the conversion failing later.

import { isStrictlyInside } from '../route/activityWindow.js';
import { describeTimeConflict, findOrderConflict, timedPoints } from '../route/anchorOrder.js';

// Shown under a time that the start/end anchors make unusable.
export const OUTSIDE_WINDOW_HINT = 'This time isn\'t between the activity\'s start and end.';

/**
 * @param {object} deps
 * @param {ReturnType<import('../state/appState.js').createAppState>} deps.appState
 * @param {{ getActiveAnchors: () => object[] }} deps.anchors
 * @param {{ getActiveStops: () => object[] }} deps.stops
 */
export function createTimeChecks({ appState, anchors, stops }) {
  function isInsideWindow(date) {
    return isStrictlyInside(date, appState.activityWindow());
  }

  // Only the anchors and stops convert() would use count: one outside the
  // window is left out of the conversion, so it can't be in the way.
  /**
   * @param {number} distanceFromStart
   * @param {Date} time
   * @param {{ until?: Date, excludeAnchorId?: number }} [opts]
   */
  function orderConflict(distanceFromStart, time, { until, excludeAnchorId } = {}) {
    const points = timedPoints(anchors.getActiveAnchors(), stops.getActiveStops(), { excludeAnchorId });
    return findOrderConflict(points, { distanceFromStart, time, until });
  }

  return {
    isInsideWindow,

    /**
     * What's wrong with a time typed into the anchor popover (`time`) or a
     * stop's arrival and departure (`time` to `until`) at this point, or null
     * if it can be used. Written for the popover's warning line.
     *
     * @param {number} distanceFromStart
     * @param {Date} time
     * @param {Date} [until]
     * @returns {string|null}
     */
    typedTimeProblem(distanceFromStart, time, until) {
      if (!isInsideWindow(time) || (until && !isInsideWindow(until))) {
        return OUTSIDE_WINDOW_HINT;
      }
      const conflict = orderConflict(distanceFromStart, time, { until });
      return conflict ? describeTimeConflict(conflict, { span: Boolean(until) }) : null;
    },

    /**
     * Why a photo taken at `time` can't go to `distanceFromStart`, or null if
     * it can. A photo outside the activity's time isn't used until the window
     * covers it, so that one fits anywhere.
     *
     * @param {number} distanceFromStart
     * @param {Date} time
     * @param {number} [excludeAnchorId] - the photo's own anchor, when moving it
     * @returns {import('../route/anchorOrder.js').OrderConflict|null}
     */
    photoOrderConflict(distanceFromStart, time, excludeAnchorId) {
      if (!isInsideWindow(time)) {
        return null;
      }
      return orderConflict(distanceFromStart, time, { excludeAnchorId });
    },
  };
}
