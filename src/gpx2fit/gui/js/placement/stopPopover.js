// The "Add stop" popover: a real pause at one point, entered either as a
// duration only or as an arrival and a departure. A start/end stop is checked
// where it's entered (timeChecks.js): both times inside the activity, the
// departure after the arrival, and the whole stay in time order with the
// other anchors and stops. A duration-only stop has no clock time, so there is
// nothing to check.

import { createTimeToggle } from '../inputs/timeInput.js';
import { createSegmented } from '../inputs/segmented.js';
import { formatDistanceKm } from '../util/format.js';
import { offsetSecondsOf, toggleDefaultsFromEstimate } from './placementEstimates.js';
import { confirmButton, estimateHint, popoverHeading, popoverHint } from './popoverParts.js';
import { OUTSIDE_WINDOW_HINT } from './timeChecks.js';

/** @typedef {import('../inputs/timeInput.js').TimeResult} TimeResult */

const DEPARTURE_BEFORE_ARRIVAL_HINT = 'The departure has to be after the arrival.';

/**
 * @param {import('./candidatePicker.js').Candidate} candidate
 * @param {object} deps
 * @param {ReturnType<import('./popoverHost.js').createPopoverHost>} deps.popovers
 * @param {ReturnType<import('../state/appState.js').createAppState>} deps.appState
 * @param {Pick<ReturnType<typeof import('../route/stops.js').createStopList>, 'addStop'>} deps.stops
 * @param {ReturnType<import('./timeChecks.js').createTimeChecks>} deps.checks
 * @param {(distanceFromStart: number) => Date|null} deps.estimateArrival
 */
export function openStopPopover(candidate, { popovers, appState, stops, checks, estimateArrival }) {
  const distanceFromStart = candidate.distance_from_start;
  popovers.open(candidate.lat, candidate.lon, (container, close, updateLayout) => {
    const estimate = estimateArrival(distanceFromStart);

    container.append(popoverHeading(`Stop at ${formatDistanceKm(distanceFromStart)}`));

    const modes = createSegmented({
      options: [
        { value: 'duration', label: 'Duration only' },
        { value: 'startEnd', label: 'Start & end time' },
      ],
      value: 'duration',
      onSelect: setMode,
      kind: 'tabs',
      className: 'time-toggle-modes',
    });
    container.append(modes.el);

    const durationContainer = document.createElement('div');
    const startEndContainer = document.createElement('div');
    startEndContainer.className = 'hidden';
    modes.linkPanel('duration', durationContainer);
    modes.linkPanel('startEnd', startEndContainer);

    // Arrival and departure sit in side-by-side columns rather than
    // stacked — stacking made this popover taller than the map pane.
    // The estimate hint applies to both columns (arrival is prefilled from
    // it; departure's default duration is measured from it), so it sits
    // above them, spanning the full popover width, rather than inside
    // either column.
    const hint = estimateHint(estimate);
    if (hint) {
      startEndContainer.append(hint);
    }

    const columnsEl = document.createElement('div');
    columnsEl.className = 'stop-time-columns';

    function timeColumn(label) {
      const column = document.createElement('div');
      const toggleContainer = document.createElement('div');
      column.append(popoverHint(label), toggleContainer);
      columnsEl.append(column);
      return toggleContainer;
    }
    const arrivalContainer = timeColumn('Arrival');
    const departureContainer = timeColumn('Departure');
    startEndContainer.append(columnsEl);

    const warning = popoverHint('', { warning: true, hidden: true });
    startEndContainer.append(warning);

    container.append(durationContainer, startEndContainer);

    const confirmBtn = confirmButton('Add stop');
    confirmBtn.disabled = true;
    container.append(confirmBtn);

    let mode = 'duration';
    /** @type {TimeResult} */
    let durationResult = { isValid: false };
    /** @type {TimeResult} */
    let arrivalResult = { isValid: false };
    /** @type {TimeResult} */
    let departureResult = { isValid: false };

    /**
     * Whether the arrival and departure can be used, and if not, what to say
     * (nothing while a side is still being typed).
     * @returns {{ usable: boolean, message: string|null }}
     */
    function checkStartEnd() {
      const sides = [arrivalResult, departureResult];
      if (sides.some((r) => r.isValid && !checks.isInsideWindow(r.resolvedDate))) {
        return { usable: false, message: OUTSIDE_WINDOW_HINT };
      }
      if (!sides.every((r) => r.isValid)) {
        return { usable: false, message: null };
      }
      if (departureResult.resolvedDate <= arrivalResult.resolvedDate) {
        return { usable: false, message: DEPARTURE_BEFORE_ARRIVAL_HINT };
      }
      const message = checks.typedTimeProblem(distanceFromStart, arrivalResult.resolvedDate, departureResult.resolvedDate);
      return { usable: message === null, message };
    }

    function isUsable() {
      return mode === 'duration' ? durationResult.isValid : checkStartEnd().usable;
    }

    function updateConfirmAvailability() {
      confirmBtn.disabled = !isUsable();
      const message = mode === 'duration' ? null : checkStartEnd().message;
      warning.textContent = message ?? '';
      warning.hidden = message === null;
    }

    createTimeToggle({
      container: durationContainer,
      variant: 'durationOnly',
      getReferenceTime: () => appState.startTime,
      onChange: (result) => {
        durationResult = result;
        updateConfirmAvailability();
      },
    });

    createTimeToggle({
      container: arrivalContainer,
      variant: 'durationOrTimeOfDay',
      getReferenceTime: () => appState.startTime,
      getTotalDurationSeconds: () => appState.durationSeconds(),
      ...toggleDefaultsFromEstimate(estimate, appState.startTime),
      onChange: (result) => {
        arrivalResult = result;
        updateConfirmAvailability();
      },
    });

    createTimeToggle({
      container: departureContainer,
      variant: 'durationOrTimeOfDay',
      getReferenceTime: () => appState.startTime,
      getTotalDurationSeconds: () => appState.durationSeconds(),
      // No reasonable dwell time to prefill, but it should still open on
      // the same tab as arrival rather than defaulting to "Duration since
      // start".
      initialMode: 'timeOfDay',
      onChange: (result) => {
        departureResult = result;
        updateConfirmAvailability();
      },
    });

    function setMode(nextMode) {
      mode = nextMode;
      durationContainer.classList.toggle('hidden', mode !== 'duration');
      startEndContainer.classList.toggle('hidden', mode === 'duration');

      // Only this popover, and only this mode, gets to be wider — the
      // width change is CSS-animated (see .is-wide in css/map.css), while
      // the height change (revealing the arrival/departure columns) lands
      // instantly. Leaflet doesn't know the content resized until told, so
      // rather than correcting once immediately and once at transitionend
      // (which reads as two separate map jumps), re-check every frame for
      // the width animation's duration — this pans the map in one
      // continuous motion that tracks the popup as it grows. transitionend
      // ends the loop as soon as the animation actually settles; the
      // deadline below is just a safety net in case it never fires (e.g.
      // a reduced-motion override skipping the transition).
      const popupContent = container.closest('.leaflet-popup-content');
      let settled = !popupContent;
      if (popupContent) {
        popupContent.classList.toggle('is-wide', mode === 'startEnd');
        popupContent.addEventListener(
          'transitionend',
          (/** @type {TransitionEvent} */ event) => {
            if (event.propertyName === 'width') {
              settled = true;
            }
          },
          { once: true },
        );
      }
      const deadline = performance.now() + 400;
      (function trackResize(now) {
        updateLayout();
        if (!settled && now < deadline) {
          requestAnimationFrame(trackResize);
        }
      })(performance.now());

      updateConfirmAvailability();
    }

    confirmBtn.addEventListener('click', () => {
      if (!isUsable()) {
        return;
      }
      const position = { lat: candidate.lat, lon: candidate.lon, distanceFromStart };
      if (mode === 'duration') {
        stops.addStop({ ...position, mode: 'duration', durationSeconds: durationResult.durationSeconds });
      } else {
        stops.addStop({
          ...position,
          mode: 'startEnd',
          arrival: arrivalResult.resolvedDate,
          departure: departureResult.resolvedDate,
          arrivalOffsetSeconds: offsetSecondsOf(arrivalResult),
          departureOffsetSeconds: offsetSecondsOf(departureResult),
        });
      }
      close();
    });
  });
}
