// The map-click -> anchor-creation flow: resolve which track point(s) the
// user meant, disambiguate if the route passes near that spot more than
// once (out-and-back routes), let the user pick a time for the anchor
// (duration-since-start or time-of-day), and hand the finished anchor off
// to anchors.js. Split out of main.js because this is the part of the app
// most likely to grow next — see CLAUDE.md's note on out-and-back
// disambiguation being a known, deliberately deferred gap.

import { resolveAnchorCandidates } from './pyodideBridge.js';
import * as mapModule from './map.js';
import * as anchorsModule from './anchors.js';
import * as stopsModule from './stops.js';
import { createTimeToggle } from './timeInput.js';
import { formatClock, formatDistanceKm } from './format.js';

/**
 * Builds the route-click handler that drives anchor placement.
 *
 * @param {object} deps
 * @param {() => Date|null} deps.getStartTime - current route start time, or null if unset/invalid
 * @param {() => {isValid: boolean, durationSeconds?: number}} deps.getStartTimeResult -
 *   current value of the main start-time toggle
 * @param {() => number|null} deps.getTotalDistance - total route distance in
 *   meters, or null before a route is loaded
 * @param {(message: string, isError?: boolean) => void} deps.setStatus
 * @returns {{ handleRouteClick: (lat: number, lon: number) => Promise<void> }}
 */
export function createAnchorPlacer({ getStartTime, getStartTimeResult, getTotalDistance, setStatus }) {
  // Rough, uniform-pace ballpark only — good enough to help the user tell
  // two candidate points apart in the disambiguation picker, and to seed
  // the time-of-day field below with a starting point they can correct.
  // Has nothing to do with the real gradient-based pacing model, which only
  // runs at Convert time once every anchor is known.
  function estimateArrivalDate(distanceFromStart) {
    const totalDistance = getTotalDistance();
    const startTimeResult = getStartTimeResult();
    const start = getStartTime();
    if (!totalDistance || !startTimeResult.isValid || !start) {
      return null;
    }
    const fraction = distanceFromStart / totalDistance;
    return new Date(start.getTime() + fraction * startTimeResult.durationSeconds * 1000);
  }

  function estimateArrivalLabel(distanceFromStart) {
    const estimated = estimateArrivalDate(distanceFromStart);
    return estimated ? `~${formatClock(estimated)} (uniform-pace estimate)` : '';
  }

  function openAnchorTimePopover(candidate) {
    mapModule.openAnchorPopup(candidate.lat, candidate.lon, (container, close) => {
      const heading = document.createElement('p');
      heading.className = 'popover-heading';
      heading.textContent = formatDistanceKm(candidate.distance_from_start);
      container.append(heading);

      const toggleContainer = document.createElement('div');
      container.append(toggleContainer);

      const confirmBtn = document.createElement('button');
      confirmBtn.type = 'button';
      confirmBtn.className = 'primary-button popover-confirm';
      confirmBtn.textContent = 'Add anchor';
      confirmBtn.disabled = true;
      container.append(confirmBtn);

      const estimate = estimateArrivalDate(candidate.distance_from_start);
      const start = getStartTime();

      let lastResult = { isValid: false };
      createTimeToggle({
        container: toggleContainer,
        variant: 'durationOrTimeOfDay',
        getReferenceTime: getStartTime,
        initialTimeOfDay: estimate ? { hours: estimate.getHours(), minutes: estimate.getMinutes() } : undefined,
        initialDurationSeconds: estimate && start ? (estimate.getTime() - start.getTime()) / 1000 : undefined,
        onChange: (result) => {
          lastResult = result;
          confirmBtn.disabled = !result.isValid;
        },
      });

      confirmBtn.addEventListener('click', () => {
        if (!lastResult.isValid) {
          return;
        }
        anchorsModule.addAnchor({
          lat: candidate.lat,
          lon: candidate.lon,
          distanceFromStart: candidate.distance_from_start,
          timestamp: lastResult.resolvedDate,
        });
        close();
      });
    });
  }

  function openCandidatePicker(candidates) {
    const first = candidates[0];
    mapModule.openAnchorPopup(first.lat, first.lon, (container, close) => {
      const heading = document.createElement('p');
      heading.className = 'popover-heading';
      heading.textContent = 'Multiple points found here';
      container.append(heading);

      const hint = document.createElement('p');
      hint.className = 'popover-hint';
      hint.textContent = 'This spot is visited more than once (e.g. an out-and-back). Which one did you mean?';
      container.append(hint);

      const list = document.createElement('div');
      list.className = 'candidate-list';

      candidates.forEach((candidate) => {
        const optionBtn = document.createElement('button');
        optionBtn.type = 'button';
        optionBtn.className = 'candidate-option';

        const distanceEl = document.createElement('span');
        distanceEl.className = 'candidate-distance';
        distanceEl.textContent = formatDistanceKm(candidate.distance_from_start);

        const estimateEl = document.createElement('span');
        estimateEl.className = 'candidate-estimate';
        estimateEl.textContent = estimateArrivalLabel(candidate.distance_from_start);

        optionBtn.append(distanceEl, estimateEl);
        optionBtn.addEventListener('click', () => {
          close();
          openKindChoicePopover(candidate);
        });
        list.append(optionBtn);
      });

      container.append(list);
    });
  }

  function openKindChoicePopover(candidate) {
    mapModule.openAnchorPopup(candidate.lat, candidate.lon, (container, close) => {
      const heading = document.createElement('p');
      heading.className = 'popover-heading';
      heading.textContent = formatDistanceKm(candidate.distance_from_start);
      container.append(heading);

      const list = document.createElement('div');
      list.className = 'candidate-list';

      const anchorBtn = document.createElement('button');
      anchorBtn.type = 'button';
      anchorBtn.className = 'candidate-option';
      anchorBtn.innerHTML = '<span class="candidate-distance">Add anchor</span><span class="candidate-estimate">A single known timestamp</span>';
      anchorBtn.addEventListener('click', () => {
        close();
        openAnchorTimePopover(candidate);
      });

      const stopBtn = document.createElement('button');
      stopBtn.type = 'button';
      stopBtn.className = 'candidate-option';
      stopBtn.innerHTML = '<span class="candidate-distance">Add stop</span><span class="candidate-estimate">A real pause at this location</span>';
      stopBtn.addEventListener('click', () => {
        close();
        openStopPopover(candidate);
      });

      list.append(anchorBtn, stopBtn);
      container.append(list);
    });
  }

  function openStopPopover(candidate) {
    mapModule.openAnchorPopup(candidate.lat, candidate.lon, (container, close) => {
      const heading = document.createElement('p');
      heading.className = 'popover-heading';
      heading.textContent = `Stop at ${formatDistanceKm(candidate.distance_from_start)}`;
      container.append(heading);

      const modesEl = document.createElement('div');
      modesEl.className = 'time-toggle-modes segmented';
      modesEl.setAttribute('role', 'tablist');
      const durationModeBtn = document.createElement('button');
      durationModeBtn.type = 'button';
      durationModeBtn.className = 'segmented-option is-active';
      durationModeBtn.textContent = 'Duration only';
      const startEndModeBtn = document.createElement('button');
      startEndModeBtn.type = 'button';
      startEndModeBtn.className = 'segmented-option';
      startEndModeBtn.textContent = 'Start & end time';
      modesEl.append(durationModeBtn, startEndModeBtn);
      container.append(modesEl);

      const durationContainer = document.createElement('div');
      const startEndContainer = document.createElement('div');
      startEndContainer.className = 'hidden';

      const arrivalLabel = document.createElement('p');
      arrivalLabel.className = 'popover-hint';
      arrivalLabel.textContent = 'Arrival';
      const arrivalContainer = document.createElement('div');
      const departureLabel = document.createElement('p');
      departureLabel.className = 'popover-hint';
      departureLabel.textContent = 'Departure';
      const departureContainer = document.createElement('div');
      startEndContainer.append(arrivalLabel, arrivalContainer, departureLabel, departureContainer);

      container.append(durationContainer, startEndContainer);

      const confirmBtn = document.createElement('button');
      confirmBtn.type = 'button';
      confirmBtn.className = 'primary-button popover-confirm';
      confirmBtn.textContent = 'Add stop';
      confirmBtn.disabled = true;
      container.append(confirmBtn);

      let mode = 'duration';
      let durationResult = { isValid: false };
      let arrivalResult = { isValid: false };
      let departureResult = { isValid: false };

      function updateConfirmAvailability() {
        if (mode === 'duration') {
          confirmBtn.disabled = !durationResult.isValid;
        } else {
          confirmBtn.disabled =
            !arrivalResult.isValid || !departureResult.isValid || !(departureResult.resolvedDate > arrivalResult.resolvedDate);
        }
      }

      createTimeToggle({
        container: durationContainer,
        variant: 'durationOnly',
        getReferenceTime: getStartTime,
        onChange: (result) => {
          durationResult = result;
          updateConfirmAvailability();
        },
      });

      createTimeToggle({
        container: arrivalContainer,
        variant: 'durationOrTimeOfDay',
        getReferenceTime: getStartTime,
        onChange: (result) => {
          arrivalResult = result;
          updateConfirmAvailability();
        },
      });

      createTimeToggle({
        container: departureContainer,
        variant: 'durationOrTimeOfDay',
        getReferenceTime: getStartTime,
        onChange: (result) => {
          departureResult = result;
          updateConfirmAvailability();
        },
      });

      function setMode(nextMode) {
        mode = nextMode;
        durationModeBtn.classList.toggle('is-active', mode === 'duration');
        startEndModeBtn.classList.toggle('is-active', mode !== 'duration');
        durationContainer.classList.toggle('hidden', mode !== 'duration');
        startEndContainer.classList.toggle('hidden', mode === 'duration');
        updateConfirmAvailability();
      }
      durationModeBtn.addEventListener('click', () => setMode('duration'));
      startEndModeBtn.addEventListener('click', () => setMode('startEnd'));

      confirmBtn.addEventListener('click', () => {
        if (mode === 'duration') {
          if (!durationResult.isValid) {
            return;
          }
          stopsModule.addStop({
            lat: candidate.lat,
            lon: candidate.lon,
            distanceFromStart: candidate.distance_from_start,
            mode: 'duration',
            durationSeconds: durationResult.durationSeconds,
          });
        } else {
          if (!arrivalResult.isValid || !departureResult.isValid || !(departureResult.resolvedDate > arrivalResult.resolvedDate)) {
            return;
          }
          stopsModule.addStop({
            lat: candidate.lat,
            lon: candidate.lon,
            distanceFromStart: candidate.distance_from_start,
            mode: 'startEnd',
            arrival: arrivalResult.resolvedDate,
            departure: departureResult.resolvedDate,
          });
        }
        close();
      });
    });
  }

  /**
   * Resolves a route click to one or more candidate track points, then lets
   * the user choose whether to add an anchor or a stop there before picking
   * its time(s). Pass this straight to `mapModule.renderRoute` as
   * `onRouteClick`.
   */
  async function handleRouteClick(lat, lon) {
    if (!getStartTimeResult().isValid) {
      setStatus('Set a valid duration or end time before adding anchors.', true);
      return;
    }

    try {
      const candidates = await resolveAnchorCandidates(lat, lon);
      if (candidates.length > 1) {
        openCandidatePicker(candidates);
      } else {
        openKindChoicePopover(candidates[0]);
      }
    } catch (error) {
      console.error(error);
      setStatus(`Error: ${error instanceof Error ? error.message : String(error)}`, true);
    }
  }

  return { handleRouteClick };
}
