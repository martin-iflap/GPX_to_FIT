// The "Which pass?" picker, for a tap or a photo on a spot the route visits
// more than once (an out-and-back): one option per pass, with its distance
// and a uniform-pace time estimate to tell them apart. For a photo, the pass
// nearest its time is marked as likely, and a pass out of time order is
// listed but disabled, with the reason.

import { describeOrderConflictBriefly } from '../route/anchorOrder.js';
import { formatClock, formatClockSeconds, formatDistanceKm } from '../util/format.js';
import { suggestCandidate } from './photoPlacement.js';
import { optionButton, popoverHeading, popoverHint } from './popoverParts.js';

/**
 * @typedef {{ lat: number, lon: number, distance_from_start: number }} Candidate
 */

/**
 * Opens the picker at the first candidate. `onPick` is what a chosen pass
 * opens: the anchor/stop choice, a photo's place card, or a dragged pin's
 * move confirmation.
 *
 * @param {object} opts
 * @param {ReturnType<import('./popoverHost.js').createPopoverHost>} opts.popovers
 * @param {Candidate[]} opts.candidates
 * @param {(distanceFromStart: number) => Date|null} opts.estimateArrival
 * @param {(candidate: Candidate) => void} opts.onPick
 * @param {Date} [opts.photoTime] - for a photo: shown in the hint, and the
 *   pass whose estimate is nearest it is marked as likely
 * @param {(import('../route/anchorOrder.js').OrderConflict|null)[]} [opts.conflicts] -
 *   per candidate, why a photo can't go there (or null if it can)
 * @param {() => void} [opts.onDismiss] - the picker closed without a pick
 */
export function openCandidatePicker({ popovers, candidates, estimateArrival, onPick, photoTime, conflicts = [], onDismiss }) {
  const first = candidates[0];
  // Only a pass that can be picked is suggested.
  const open = candidates.filter((_, index) => !conflicts[index]);
  const openIndex = photoTime ? suggestCandidate(open, photoTime, estimateArrival) : null;
  const suggestedIndex = openIndex === null ? null : candidates.indexOf(open[openIndex]);
  let picked = false;

  function estimateLabel(distanceFromStart) {
    const estimated = estimateArrival(distanceFromStart);
    return estimated ? `~${formatClock(estimated)} (uniform-pace estimate)` : '';
  }

  popovers.open(first.lat, first.lon, (container, close) => {
    container.append(
      popoverHeading(photoTime ? 'Which pass was the photo taken on?' : 'Multiple points found here'),
      popoverHint(photoTime
        ? `This spot is visited more than once (e.g. an out-and-back). The photo was taken at ${formatClockSeconds(photoTime)}.`
        : 'This spot is visited more than once (e.g. an out-and-back). Which one did you mean?'),
    );

    const list = document.createElement('div');
    list.className = 'candidate-list';

    candidates.forEach((candidate, index) => {
      const optionBtn = optionButton(
        formatDistanceKm(candidate.distance_from_start),
        estimateLabel(candidate.distance_from_start),
      );
      if (conflicts[index]) {
        optionBtn.disabled = true;
        const reason = document.createElement('span');
        reason.className = 'candidate-blocked';
        reason.textContent = describeOrderConflictBriefly(conflicts[index]);
        optionBtn.append(reason);
      }
      if (index === suggestedIndex) {
        optionBtn.classList.add('is-suggested');
        const badge = document.createElement('span');
        badge.className = 'candidate-badge';
        badge.textContent = 'Likely, by the photo\'s time';
        optionBtn.append(badge);
        // The content is in the page once popovers.open returns.
        window.setTimeout(() => optionBtn.focus({ preventScroll: true }));
      }
      optionBtn.addEventListener('click', () => {
        picked = true;
        close();
        onPick(candidate);
      });
      list.append(optionBtn);
    });

    container.append(list);

    if (open.length === 0) {
      container.append(popoverHint(
        `The photo's time (${formatClockSeconds(photoTime)}) doesn't fit any of these passes.`,
        { warning: true },
      ));
    }
  }, {
    onClose: () => {
      if (!picked) {
        onDismiss?.();
      }
    },
  });
}
