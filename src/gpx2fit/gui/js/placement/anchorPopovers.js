// Everything a tap on the route, or a dragged photo pin, opens: the
// anchor/stop choice and their time popovers, the "Which pass?" picker on an
// overlapping route, and the photo cards ("Place photo here", "Move photo?").
// Each one opens through map.js's openAnchorPopup, which is a Leaflet popup on
// a desktop and #routeDialog on a phone. The pure rules behind them live
// elsewhere: time order in anchorOrder.js, the pending photo and the
// likely-pass suggestion in photoPlacement.js, and the activity window in
// activityWindow.js.
//
// Pure helpers are imported; everything with state or side effects (the app
// state, the anchor and stop lists, the map, the Python bridge, the status
// line) is passed in to createAnchorPlacer by main.js.

import { createTimeToggle } from '../inputs/timeInput.js';
import { createSegmented } from '../inputs/segmented.js';
import { dayOffsetBetween } from '../util/dates.js';
import { effectiveAnchorTime, isStrictlyInside } from '../route/activityWindow.js';
import { describeError, formatClock, formatClockSeconds, formatDistanceKm } from '../util/format.js';
import { createPhotoPlacement, suggestCandidate } from './photoPlacement.js';
import { describeOrderConflict, describeOrderConflictBriefly, findOrderConflict, timedPoints } from '../route/anchorOrder.js';

// Shown under a time that the start/end anchors make unusable. The popover
// catches it before the anchor or stop is added, so a later conversion never
// has to reject it.
const OUTSIDE_WINDOW_HINT = 'This time isn\'t between the activity\'s start and end.';
// A photo's time can't be corrected, so a photo outside the window is still
// placed, like a GPS-matched one, and anchors.js dims it until the window
// covers it.
const PHOTO_OUTSIDE_WINDOW_HINT = 'Taken outside the activity\'s time: it\'s added, '
  + 'but not used until the start time and duration cover it.';
// A dragged photo pin dropped closer than this (along the route) to where it
// was isn't moved: route points are 2 m apart, so a slight nudge would
// otherwise ask to "move" it between two distances that read the same at
// the card's 10 m resolution.
const MIN_PHOTO_MOVE_M = 10;

/**
 * A result's offset from the start, for a time entered as "Duration since
 * start": stored so the anchor or stop follows later start changes. Absent
 * for a time of day, which is a fixed clock time.
 */
function offsetSecondsOf(result) {
  return result.mode === 'duration' ? result.durationSeconds : undefined;
}

/**
 * Builds the route-click handler that drives anchor placement, including
 * "Place on map" for a photo that kept its time but lost its location: while
 * one is pending, a route tap opens a "Place photo here" card instead of the
 * anchor/stop choice. Also handles a photo pin dragged along the route,
 * which only moves once the user confirms, and the "Which pass?" choice for a
 * photo on a spot the route passes more than once.
 *
 * However a photo gets its point (placed, resolved or dragged), a point where
 * its time would be out of order with another anchor or stop is refused
 * there, with the reason (anchorOrder.js), instead of failing at Convert.
 *
 * @param {object} deps
 * @param {ReturnType<import('../state/appState.js').createAppState>} deps.appState -
 *   the route, the start time and the activity window
 * @param {typeof import('../route/anchors.js')} deps.anchors - the anchor list
 * @param {typeof import('../route/stops.js')} deps.stops - the stop list
 * @param {typeof import('../route/map.js')} deps.map
 * @param {typeof import('../bridge/pyodideBridge.js').resolveAnchorCandidates} deps.resolveAnchorCandidates
 * @param {() => void} deps.revealMap - mobileLayout's: brings the map into view on a phone
 * @param {(message: string, kind?: import('../shell/status.js').StatusKind) => void} deps.setStatus
 * @param {HTMLElement} [deps.placementBannerEl] - the banner over the map
 *   shown while a photo waits for its tap (`#photoPlacementBanner`)
 * @returns {{
 *   handleRouteClick: (lat: number, lon: number) => Promise<void>,
 *   handleAnchorDrag: (anchor: object, lat: number, lon: number, revert: () => void) => Promise<void>,
 *   placePhoto: (photo: {timestamp: Date, thumbnailUrl?: string|null, onPlaced: (position: {lat: number, lon: number, distanceFromStart: number}) => void, onCancel?: () => void}) => void,
 *   cancelPhotoPlacement: () => void,
 *   choosePhotoPass: (photo: {timestamp: Date, passes: {lat: number, lon: number, distance_from_start: number}[], onPicked: (pass: {lat: number, lon: number, distance_from_start: number}) => void}) => void,
 * }}
 */
export function createAnchorPlacer({
  appState,
  anchors,
  stops,
  map,
  resolveAnchorCandidates,
  revealMap,
  setStatus,
  placementBannerEl,
}) {
  const photoPlacement = createPhotoPlacement({ onChange: showPlacementState });

  // The map and the banner follow the pending photo: a crosshair and a
  // pulsing route, and a banner saying which photo and how to back out.
  // The sidebar status alone sits off-screen at the end of a phone's sheet.
  function showPlacementState(photo) {
    map.setPlacementMode(Boolean(photo));
    if (!placementBannerEl) {
      return;
    }
    placementBannerEl.hidden = !photo;
    if (!photo) {
      return;
    }
    placementBannerEl.querySelector('.placement-banner-text').textContent = (
      `Tap the route where the photo from ${formatClockSeconds(photo.timestamp)} was taken`
    );
    const thumb = placementBannerEl.querySelector('.placement-banner-thumb');
    thumb.hidden = !photo.thumbnailUrl;
    if (photo.thumbnailUrl) {
      thumb.src = photo.thumbnailUrl;
    } else {
      thumb.removeAttribute('src');
    }
  }
  placementBannerEl?.querySelector('.placement-banner-cancel')
    .addEventListener('click', () => cancelPhotoPlacement());

  // The whole activity's total duration, so a time-of-day toggle can tell
  // whether it needs a day selector — not the estimate helpers below, which
  // are about a single anchor's ballpark position, not the route's overall span.
  const getTotalDurationSeconds = () => appState.durationSeconds();

  // Rough, uniform-pace ballpark only — good enough to help the user tell
  // two candidate points apart in the disambiguation picker, and to seed
  // the time-of-day field below with a starting point they can correct.
  // Has nothing to do with the real gradient-based pacing model, which only
  // runs at Convert time once every anchor is known.
  function estimateArrivalDate(distanceFromStart) {
    const totalDistance = appState.route?.totalDistance;
    const durationSeconds = appState.durationSeconds();
    const start = appState.startTime;
    if (!totalDistance || durationSeconds === null || !start) {
      return null;
    }
    const fraction = distanceFromStart / totalDistance;
    return new Date(start.getTime() + fraction * durationSeconds * 1000);
  }

  function isUsableTime(result) {
    return result.isValid && isStrictlyInside(result.resolvedDate, appState.activityWindow());
  }

  // The route's first and last point already carry the start and end time;
  // another anchor or stop there would contradict them.
  function isRouteEnd(distanceFromStart) {
    return distanceFromStart === 0 || distanceFromStart === appState.route?.totalDistance;
  }

  function createWarningHint() {
    const hint = document.createElement('p');
    hint.className = 'popover-hint is-warning';
    hint.textContent = OUTSIDE_WINDOW_HINT;
    hint.hidden = true;
    return hint;
  }

  function estimateArrivalLabel(distanceFromStart) {
    const estimated = estimateArrivalDate(distanceFromStart);
    return estimated ? `~${formatClock(estimated)} (uniform-pace estimate)` : '';
  }

  // Prefilled times are a straight-line average-pace guess, not a fact —
  // this makes that visible wherever a popover prefills from one, so it
  // doesn't get mistaken for a precise computed arrival time.
  function appendEstimateHint(container, estimate) {
    if (!estimate) {
      return;
    }
    const hint = document.createElement('p');
    hint.className = 'popover-hint';
    hint.textContent = 'Pre-filled time is an estimate — check and adjust.';
    container.append(hint);
  }

  function openAnchorTimePopover(candidate) {
    map.openAnchorPopup(candidate.lat, candidate.lon, (container, close) => {
      const start = appState.startTime;
      const estimate = estimateArrivalDate(candidate.distance_from_start);

      const heading = document.createElement('p');
      heading.className = 'popover-heading';
      heading.textContent = formatDistanceKm(candidate.distance_from_start);
      container.append(heading);

      appendEstimateHint(container, estimate);

      const toggleContainer = document.createElement('div');
      container.append(toggleContainer);

      const outsideHint = createWarningHint();
      container.append(outsideHint);

      const confirmBtn = document.createElement('button');
      confirmBtn.type = 'button';
      confirmBtn.className = 'primary-button popover-confirm';
      confirmBtn.textContent = 'Add anchor';
      confirmBtn.disabled = true;
      container.append(confirmBtn);

      let lastResult = { isValid: false };
      createTimeToggle({
        container: toggleContainer,
        variant: 'durationOrTimeOfDay',
        getReferenceTime: () => appState.startTime,
        getTotalDurationSeconds,
        initialTimeOfDay: estimate ? { hours: estimate.getHours(), minutes: estimate.getMinutes() } : undefined,
        initialDurationSeconds: estimate && start ? (estimate.getTime() - start.getTime()) / 1000 : undefined,
        initialDayOffset: estimate && start ? dayOffsetBetween(start, estimate) : undefined,
        onChange: (result) => {
          lastResult = result;
          confirmBtn.disabled = !isUsableTime(result);
          outsideHint.hidden = !result.isValid || isUsableTime(result);
        },
      });

      confirmBtn.addEventListener('click', () => {
        if (!isUsableTime(lastResult)) {
          return;
        }
        anchors.addAnchor({
          lat: candidate.lat,
          lon: candidate.lon,
          distanceFromStart: candidate.distance_from_start,
          timestamp: lastResult.resolvedDate,
          offsetSeconds: offsetSecondsOf(lastResult),
        });
        close();
      });
    });
  }

  /**
   * Asks which pass over an overlapping spot was meant. `onPick` is what a
   * chosen pass opens: the anchor/stop choice, a photo's place card, or a
   * dragged pin's move confirmation.
   *
   * @param {object[]} candidates
   * @param {(candidate: object) => void} onPick
   * @param {object} [opts]
   * @param {Date} [opts.photoTime] - for a photo: shown in the hint, and the
   *   pass whose estimate is nearest it is marked as likely
   * @param {(import('../route/anchorOrder.js').OrderConflict|null)[]} [opts.conflicts] -
   *   per candidate, why a photo can't go there (or null if it can): such a
   *   pass is listed but disabled, with the reason
   * @param {() => void} [opts.onDismiss] - the picker closed without a pick
   */
  function openCandidatePicker(candidates, onPick, { photoTime, conflicts = [], onDismiss } = {}) {
    const first = candidates[0];
    // Only a pass that can be picked is suggested.
    const open = candidates.filter((_, index) => !conflicts[index]);
    const openIndex = photoTime ? suggestCandidate(open, photoTime, estimateArrivalDate) : null;
    const suggestedIndex = openIndex === null ? null : candidates.indexOf(open[openIndex]);
    let picked = false;
    map.openAnchorPopup(first.lat, first.lon, (container, close) => {
      const heading = document.createElement('p');
      heading.className = 'popover-heading';
      heading.textContent = photoTime ? 'Which pass was the photo taken on?' : 'Multiple points found here';
      container.append(heading);

      const hint = document.createElement('p');
      hint.className = 'popover-hint';
      hint.textContent = photoTime
        ? `This spot is visited more than once (e.g. an out-and-back). The photo was taken at ${formatClockSeconds(photoTime)}.`
        : 'This spot is visited more than once (e.g. an out-and-back). Which one did you mean?';
      container.append(hint);

      const list = document.createElement('div');
      list.className = 'candidate-list';

      candidates.forEach((candidate, index) => {
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
          // The content is in the page once openAnchorPopup returns.
          setTimeout(() => optionBtn.focus({ preventScroll: true }));
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
        const none = document.createElement('p');
        none.className = 'popover-hint is-warning';
        none.textContent = `The photo's time (${formatClockSeconds(photoTime)}) doesn't fit any of these passes.`;
        container.append(none);
      }
    }, {
      onClose: () => {
        if (!picked) {
          onDismiss?.();
        }
      },
    });
  }

  // A popup that only explains, at the point it's about.
  function openNotice(candidate, headingText, hintText) {
    map.openAnchorPopup(candidate.lat, candidate.lon, (container) => {
      const heading = document.createElement('p');
      heading.className = 'popover-heading';
      heading.textContent = headingText;
      const hint = document.createElement('p');
      hint.className = 'popover-hint';
      hint.textContent = hintText;
      container.append(heading, hint);
    });
  }

  /**
   * Opens the "you picked the route's start/finish" explanation instead of a
   * popover with inputs. `action` finishes "To …, pick a point": what the
   * user was trying to do there.
   */
  function openRouteEndHint(candidate, action) {
    openNotice(
      candidate,
      formatDistanceKm(candidate.distance_from_start),
      candidate.distance_from_start === 0
        ? `This is the route's start, and its time is the start time you entered. To ${action}, pick a point a little further along.`
        : `This is the route's finish, and its time is the activity's end. To ${action}, pick a point a little before it.`,
    );
  }

  /**
   * Why a photo taken at `time` can't go to `distanceFromStart`, or null if
   * it can: it would be out of time order with an anchor or stop there
   * (anchorOrder.js). Only the anchors and stops convert() would use count,
   * and only for a photo it would use: convert() leaves out a photo outside
   * the activity's time until the window covers it, so that one fits anywhere.
   *
   * @param {number} distanceFromStart
   * @param {Date} time
   * @param {number} [excludeAnchorId] - the photo's own anchor, when moving it
   */
  function photoOrderConflict(distanceFromStart, time, excludeAnchorId) {
    if (!isStrictlyInside(time, appState.activityWindow())) {
      return null;
    }
    const points = timedPoints(anchors.getActiveAnchors(), stops.getActiveStops(), { excludeAnchorId });
    return findOrderConflict(points, { distanceFromStart, time });
  }

  /**
   * Picks a photo's point, whichever way it got one (placed, resolved or
   * dragged). One candidate goes straight to `onPick`, or to a notice saying
   * why it can't be used. Several open the "Which pass?" picker, with the
   * passes that are out of order disabled. `onDismiss` runs when nothing is
   * picked.
   */
  function choosePhotoCandidate(candidates, time, onPick, { excludeAnchorId, onDismiss } = {}) {
    const conflicts = candidates.map((c) => photoOrderConflict(c.distance_from_start, time, excludeAnchorId));
    if (candidates.length > 1) {
      openCandidatePicker(candidates, onPick, { photoTime: time, conflicts, onDismiss });
      return;
    }
    const [candidate] = candidates;
    const [conflict] = conflicts;
    if (conflict) {
      onDismiss?.();
      openNotice(candidate, 'Not in time order here', describeOrderConflict(conflict, time));
      return;
    }
    onPick(candidate);
  }

  // The photo's thumbnail (or a camera glyph without one), its fixed time
  // and where on the route this is: the header of both photo cards.
  function appendPhotoSummary(container, { thumbnailUrl, timestamp }, distanceText) {
    const summary = document.createElement('div');
    summary.className = 'photo-card';
    let thumb;
    if (thumbnailUrl) {
      thumb = document.createElement('img');
      thumb.src = thumbnailUrl;
      thumb.alt = '';
    } else {
      thumb = document.createElement('span');
      thumb.textContent = '📷';
      thumb.setAttribute('aria-hidden', 'true');
    }
    thumb.className = 'photo-card-thumb';

    const text = document.createElement('div');
    const heading = document.createElement('p');
    heading.className = 'popover-heading';
    heading.textContent = `Photo taken ${formatClockSeconds(timestamp)}`;
    const where = document.createElement('p');
    where.className = 'popover-hint';
    where.textContent = distanceText;
    text.append(heading, where);

    summary.append(thumb, text);
    container.append(summary);
  }

  function appendOutsideWindowHint(container, timestamp) {
    if (isStrictlyInside(timestamp, appState.activityWindow())) {
      return;
    }
    const hint = document.createElement('p');
    hint.className = 'popover-hint is-warning';
    hint.textContent = PHOTO_OUTSIDE_WINDOW_HINT;
    container.append(hint);
  }

  // A card's two buttons: a quiet way out, and the one thing it's for.
  function appendCardActions(container, cancelText, confirmText) {
    const actions = document.createElement('div');
    actions.className = 'popover-actions';
    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'popover-secondary';
    cancelBtn.textContent = cancelText;
    const confirmBtn = document.createElement('button');
    confirmBtn.type = 'button';
    confirmBtn.className = 'primary-button popover-confirm';
    confirmBtn.textContent = confirmText;
    actions.append(cancelBtn, confirmBtn);
    container.append(actions);
    return { cancelBtn, confirmBtn };
  }

  /**
   * The card a tap opens while a photo waits to be placed. The photo's time
   * is shown but can't be changed. "Not here" just closes the card: the
   * photo stays armed for another tap.
   */
  function openPhotoPlaceCard(candidate, photo) {
    if (isRouteEnd(candidate.distance_from_start)) {
      openRouteEndHint(candidate, 'place this photo');
      return;
    }
    map.openAnchorPopup(candidate.lat, candidate.lon, (container, close) => {
      appendPhotoSummary(container, photo, `At ${formatDistanceKm(candidate.distance_from_start)} along the route`);
      appendOutsideWindowHint(container, photo.timestamp);
      const { cancelBtn, confirmBtn } = appendCardActions(container, 'Not here', 'Place photo here');
      cancelBtn.addEventListener('click', close);
      confirmBtn.addEventListener('click', () => {
        // Canceled (or replaced by another photo) while this card was open:
        // its row no longer expects an anchor.
        if (photoPlacement.pending() !== photo) {
          close();
          return;
        }
        photoPlacement.consume();
        photo.onPlaced({
          lat: candidate.lat,
          lon: candidate.lon,
          distanceFromStart: candidate.distance_from_start,
        });
        setStatus('Photo placed. To move it, drag its pin along the route.');
        close();
      });
    });
  }

  /**
   * Asks before a dragged photo pin really moves: the pin waits at the drop
   * point, and anything but "Move" (the other button, the ×, a click
   * elsewhere, Esc) puts it back.
   */
  function openMoveConfirm(anchor, candidate, revert) {
    let moved = false;
    const taken = effectiveAnchorTime(anchor, appState.startTime);
    map.openAnchorPopup(candidate.lat, candidate.lon, (container, close) => {
      appendPhotoSummary(
        container,
        { thumbnailUrl: anchor.thumbnailUrl ?? null, timestamp: taken },
        `Move from ${formatDistanceKm(anchor.distanceFromStart)} to ${formatDistanceKm(candidate.distance_from_start)}? Its time stays the same.`,
      );
      appendOutsideWindowHint(container, taken);
      const { cancelBtn, confirmBtn } = appendCardActions(container, 'Keep it where it was', 'Move');
      cancelBtn.addEventListener('click', close);
      confirmBtn.addEventListener('click', () => {
        moved = true;
        anchors.moveAnchor(anchor.id, {
          lat: candidate.lat,
          lon: candidate.lon,
          distanceFromStart: candidate.distance_from_start,
        });
        setStatus(`Photo moved to ${formatDistanceKm(candidate.distance_from_start)}.`);
        close();
      });
    }, {
      onClose: () => {
        if (!moved) {
          revert();
        }
      },
    });
  }

  function openKindChoicePopover(candidate) {
    if (isRouteEnd(candidate.distance_from_start)) {
      openRouteEndHint(candidate, 'add an anchor or stop');
      return;
    }
    map.openAnchorPopup(candidate.lat, candidate.lon, (container, close) => {
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
    map.openAnchorPopup(candidate.lat, candidate.lon, (container, close, updateLayout) => {
      const estimate = estimateArrivalDate(candidate.distance_from_start);
      const start = appState.startTime;

      const heading = document.createElement('p');
      heading.className = 'popover-heading';
      heading.textContent = `Stop at ${formatDistanceKm(candidate.distance_from_start)}`;
      container.append(heading);

      const modes = createSegmented({
        options: [
          { value: 'duration', label: 'Duration only' },
          { value: 'startEnd', label: 'Start & end time' },
        ],
        value: 'duration',
        onSelect: setMode,
        className: 'time-toggle-modes',
      });
      container.append(modes.el);

      const durationContainer = document.createElement('div');
      const startEndContainer = document.createElement('div');
      startEndContainer.className = 'hidden';

      // Arrival and departure sit in side-by-side columns rather than
      // stacked — stacking made this popover taller than the map pane.
      // The estimate hint applies to both columns (arrival is prefilled from
      // it; departure's default duration is measured from it), so it sits
      // above them, spanning the full popover width, rather than inside
      // either column.
      appendEstimateHint(startEndContainer, estimate);

      const columnsEl = document.createElement('div');
      columnsEl.className = 'stop-time-columns';

      const arrivalColumn = document.createElement('div');
      const arrivalLabel = document.createElement('p');
      arrivalLabel.className = 'popover-hint';
      arrivalLabel.textContent = 'Arrival';
      arrivalColumn.append(arrivalLabel);
      const arrivalContainer = document.createElement('div');
      arrivalColumn.append(arrivalContainer);

      const departureColumn = document.createElement('div');
      const departureLabel = document.createElement('p');
      departureLabel.className = 'popover-hint';
      departureLabel.textContent = 'Departure';
      departureColumn.append(departureLabel);
      const departureContainer = document.createElement('div');
      departureColumn.append(departureContainer);

      columnsEl.append(arrivalColumn, departureColumn);
      startEndContainer.append(columnsEl);

      const outsideHint = createWarningHint();
      startEndContainer.append(outsideHint);

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

      function isStartEndUsable() {
        return isUsableTime(arrivalResult)
          && isUsableTime(departureResult)
          && departureResult.resolvedDate > arrivalResult.resolvedDate;
      }

      function updateConfirmAvailability() {
        if (mode === 'duration') {
          confirmBtn.disabled = !durationResult.isValid;
          outsideHint.hidden = true;
        } else {
          confirmBtn.disabled = !isStartEndUsable();
          const eitherOutside = [arrivalResult, departureResult].some((r) => r.isValid && !isUsableTime(r));
          outsideHint.hidden = !eitherOutside;
        }
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
        getTotalDurationSeconds,
        initialTimeOfDay: estimate ? { hours: estimate.getHours(), minutes: estimate.getMinutes() } : undefined,
        initialDurationSeconds: estimate && start ? (estimate.getTime() - start.getTime()) / 1000 : undefined,
        initialDayOffset: estimate && start ? dayOffsetBetween(start, estimate) : undefined,
        onChange: (result) => {
          arrivalResult = result;
          updateConfirmAvailability();
        },
      });

      createTimeToggle({
        container: departureContainer,
        variant: 'durationOrTimeOfDay',
        getReferenceTime: () => appState.startTime,
        getTotalDurationSeconds,
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
        // width change is CSS-animated (see .is-wide in styles.css), while
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
            (event) => {
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
        if (mode === 'duration') {
          if (!durationResult.isValid) {
            return;
          }
          stops.addStop({
            lat: candidate.lat,
            lon: candidate.lon,
            distanceFromStart: candidate.distance_from_start,
            mode: 'duration',
            durationSeconds: durationResult.durationSeconds,
          });
        } else {
          if (!isStartEndUsable()) {
            return;
          }
          stops.addStop({
            lat: candidate.lat,
            lon: candidate.lon,
            distanceFromStart: candidate.distance_from_start,
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

  /**
   * Resolves a route click to one or more candidate track points, then lets
   * the user choose whether to add an anchor or a stop there before picking
   * its time(s). Pass this straight to `map.renderRoute` as
   * `onRouteClick`.
   */
  async function handleRouteClick(lat, lon) {
    // Read before the await, so a tap made for this photo places this photo
    // even if the row is canceled meanwhile (the card re-checks).
    const photo = photoPlacement.pending();
    // A photo brings its own time, and one outside the window is kept
    // (dimmed), so only a typed-in anchor or stop needs a valid start–end.
    if (!photo && !appState.timeResult.isValid) {
      setStatus('Set a valid duration or end time before adding anchors.', 'input');
      return;
    }

    try {
      const candidates = await resolveAnchorCandidates(lat, lon);
      if (photo) {
        choosePhotoCandidate(candidates, photo.timestamp, (candidate) => openPhotoPlaceCard(candidate, photo));
      } else if (candidates.length > 1) {
        openCandidatePicker(candidates, openKindChoicePopover);
      } else {
        openKindChoicePopover(candidates[0]);
      }
    } catch (error) {
      console.error(error);
      const { message, kind } = describeError(error);
      setStatus(message, kind);
    }
  }

  /**
   * A photo pin was dragged along the route and dropped at `lat`/`lon`
   * (already snapped to the line). Resolves the drop to a route point, asks
   * which pass if the spot is visited more than once, then asks to confirm.
   * A drop under `MIN_PHOTO_MOVE_M` from where the photo was only says so.
   * A point out of time order with the other anchors and stops is refused,
   * with the reason. Every way of not confirming calls `revert()`, which puts
   * the pin back.
   * Pass this to `anchors.setAnchorDragEndListener`.
   */
  async function handleAnchorDrag(anchor, lat, lon, revert) {
    let candidates;
    try {
      candidates = await resolveAnchorCandidates(lat, lon);
    } catch (error) {
      revert();
      console.error(error);
      const { message, kind } = describeError(error);
      setStatus(message, kind);
      return;
    }

    function confirmAt(candidate) {
      if (Math.abs(candidate.distance_from_start - anchor.distanceFromStart) < MIN_PHOTO_MOVE_M) {
        revert();
        setStatus(`The photo was dropped less than ${MIN_PHOTO_MOVE_M} m from where it was, so it wasn't moved. Drag it a little further along the route.`);
        return;
      }
      if (isRouteEnd(candidate.distance_from_start)) {
        revert();
        openRouteEndHint(candidate, 'move this photo');
        return;
      }
      openMoveConfirm(anchor, candidate, revert);
    }

    choosePhotoCandidate(candidates, effectiveAnchorTime(anchor, appState.startTime), confirmAt, {
      excludeAnchorId: anchor.id,
      onDismiss: revert,
    });
  }

  /**
   * Arms "Place on map" for a photo with a time but no location: the next
   * route tap opens a card to place it there, at the photo's own time. Only
   * one photo waits at a time; arming another cancels the first (its
   * `onCancel` runs). The banner over the map says what to do.
   */
  function placePhoto(photo) {
    photoPlacement.arm(photo);
    revealMap();
  }

  function cancelPhotoPlacement() {
    photoPlacement.cancel();
  }

  /**
   * Asks which pass a photo on a spot the route passes more than once was
   * taken on: its GPS can't tell, since the passes are metres apart. The pass
   * nearest the photo's time is marked as likely, and a pass out of time
   * order with the other anchors and stops is disabled.
   */
  function choosePhotoPass(photo) {
    revealMap();
    choosePhotoCandidate(photo.passes, photo.timestamp, (pass) => {
      photo.onPicked(pass);
      setStatus(`Photo added at ${formatDistanceKm(pass.distance_from_start)}. To move it, drag its pin along the route.`);
    });
  }

  // Esc cancels a pending placement, unless it's closing a dialog (the
  // phone's anchor popover is one) or a Leaflet popup: then it does only that.
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !photoPlacement.pending() || document.querySelector('dialog[open], .leaflet-popup')) {
      return;
    }
    cancelPhotoPlacement();
  });

  return { handleRouteClick, handleAnchorDrag, placePhoto, cancelPhotoPlacement, choosePhotoPass };
}
