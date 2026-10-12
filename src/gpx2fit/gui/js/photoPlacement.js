// "Place on map" for a photo that kept its capture time but lost its GPS
// position (Android's Photo Picker, email and Bluetooth all strip location).
// The user taps the route where the photo was taken, and the anchor gets the
// photo's own time, which is never edited. This module is the pure part:
// which photo is waiting to be placed, and which pass of an overlapping
// route its time points at. The map click and the cards themselves stay in
// anchorPopovers.js, which isn't unit-tested, so everything decidable
// without Leaflet lives here.

/**
 * Holds at most one photo waiting for a route tap. Arming another photo
 * cancels the one before it (its `onCancel` runs), so two rows can't both
 * think they're about to be placed. `onChange` hears every change of the
 * pending photo (armed, canceled, consumed), which is what the map's
 * "placing" banner and cursor follow.
 *
 * @param {{ onChange?: (pending: object|null) => void }} [opts]
 * @returns {{
 *   arm: (photo: {timestamp: Date, onPlaced: (position: object) => void, onCancel?: () => void}) => void,
 *   cancel: () => void,
 *   pending: () => object|null,
 *   consume: () => object|null,
 * }}
 */
export function createPhotoPlacement({ onChange = () => {} } = {}) {
  let current = null;

  function cancel() {
    const cancelled = current;
    if (!cancelled) {
      return;
    }
    current = null;
    cancelled.onCancel?.();
    onChange(null);
  }

  return {
    arm(photo) {
      if (current === photo) {
        return;
      }
      cancel();
      current = photo;
      onChange(photo);
    },
    cancel,
    pending: () => current,
    // Hands the photo to the route click that places it, once: after this a
    // cancel (the popover closing) no longer reaches the photo's row.
    consume() {
      const consumed = current;
      if (consumed) {
        current = null;
        onChange(null);
      }
      return consumed;
    },
  };
}

/**
 * Which of several passes over one spot (an out-and-back) a photo most
 * likely belongs to: the one whose uniform-pace arrival estimate is nearest
 * the photo's time. Only a suggestion for the picker, never applied on its
 * own, since the estimate ignores the terrain. On a tie the earlier pass
 * wins.
 *
 * @param {{distance_from_start: number}[]} candidates
 * @param {Date} photoTime
 * @param {(distanceFromStart: number) => Date|null} estimateArrival - null
 *   while the start or duration isn't valid
 * @returns {number|null} the index into `candidates`, or null when there is
 *   nothing to choose between or no estimate to choose by
 */
export function suggestCandidate(candidates, photoTime, estimateArrival) {
  if (candidates.length < 2) {
    return null;
  }
  let bestIndex = null;
  let bestGap = Infinity;
  for (const [index, candidate] of candidates.entries()) {
    const estimate = estimateArrival(candidate.distance_from_start);
    if (!estimate) {
      return null;
    }
    const gap = Math.abs(estimate.getTime() - photoTime.getTime());
    if (gap < bestGap) {
      bestGap = gap;
      bestIndex = index;
    }
  }
  return bestIndex;
}
