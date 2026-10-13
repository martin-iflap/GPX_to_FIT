// Everything a tap on the route, or a dragged photo pin, opens: the
// anchor/stop choice and the anchor's time popover, and the photo cards
// ("Place photo here", "Move photo?"). Each one opens through the popover
// host (popoverHost.js), which is a Leaflet popup on a desktop and
// #routeDialog on a phone. The rest lives in its own module: the stop popover in
// stopPopover.js, the "Which pass?" picker in candidatePicker.js, the time
// checks in timeChecks.js, the estimates in placementEstimates.js, the
// shared elements in popoverParts.js, and the pending photo in
// photoPlacement.js.
//
// Pure helpers are imported; everything with state or side effects (the app
// state, the anchor and stop lists, the map, the Python bridge, the status
// line) is passed in to createAnchorPlacer by main.js.

import { createTimeToggle } from '../inputs/timeInput.js';
import { effectiveAnchorTime } from '../route/activityWindow.js';
import { describeOrderConflict } from '../route/anchorOrder.js';
import { describeError, formatClockSeconds, formatDistanceKm } from '../util/format.js';
import { openCandidatePicker } from './candidatePicker.js';
import { createPhotoPlacement } from './photoPlacement.js';
import { estimateArrivalDate, isRouteEnd, offsetSecondsOf, toggleDefaultsFromEstimate } from './placementEstimates.js';
import {
  cardActions, confirmButton, estimateHint, optionButton, photoSummary, popoverHeading, popoverHint,
} from './popoverParts.js';
import { openStopPopover } from './stopPopover.js';
import { createTimeChecks } from './timeChecks.js';

/** @typedef {import('../inputs/timeInput.js').TimeResult} TimeResult */

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
 * Builds the route-click handler that drives anchor placement, including
 * "Place on map" for a photo that kept its time but lost its location: while
 * one is pending, a route tap opens a "Place photo here" card instead of the
 * anchor/stop choice. Also handles a photo pin dragged along the route,
 * which only moves once the user confirms, and the "Which pass?" choice for a
 * photo on a spot the route passes more than once.
 *
 * Every time placed on the route is checked where it's entered
 * (timeChecks.js): a typed anchor or stop time outside the activity or out of
 * time order with another anchor or stop can't be confirmed, and says why;
 * a photo's point out of order is refused, with the reason. So none of them
 * fails at Convert instead.
 *
 * @param {object} deps
 * @param {ReturnType<import('../state/appState.js').createAppState>} deps.appState -
 *   the route, the start time and the activity window
 * @param {ReturnType<typeof import('../route/anchors.js').createAnchorList>} deps.anchors - the anchor list
 * @param {ReturnType<typeof import('../route/stops.js').createStopList>} deps.stops - the stop list
 * @param {{ setPlacementMode: (on: boolean) => void }} deps.map - map.js's
 * @param {ReturnType<import('./popoverHost.js').createPopoverHost>} deps.popovers
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
  popovers,
  resolveAnchorCandidates,
  revealMap,
  setStatus,
  placementBannerEl,
}) {
  const photoPlacement = createPhotoPlacement({ onChange: showPlacementState });
  const checks = createTimeChecks({ appState, anchors, stops });

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
    const thumb = /** @type {HTMLImageElement} */ (placementBannerEl.querySelector('.placement-banner-thumb'));
    thumb.hidden = !photo.thumbnailUrl;
    if (photo.thumbnailUrl) {
      thumb.src = photo.thumbnailUrl;
    } else {
      thumb.removeAttribute('src');
    }
  }
  placementBannerEl?.querySelector('.placement-banner-cancel')
    .addEventListener('click', () => cancelPhotoPlacement());

  // A uniform-pace ballpark (see placementEstimates.js), against the current
  // route, start and duration.
  function estimateArrival(distanceFromStart) {
    return estimateArrivalDate(distanceFromStart, {
      start: appState.startTime,
      durationSeconds: appState.durationSeconds(),
      totalDistance: appState.route?.totalDistance,
    });
  }

  function isAtRouteEnd(candidate) {
    return isRouteEnd(candidate.distance_from_start, appState.route?.totalDistance);
  }

  function pickCandidate(candidates, onPick, opts = {}) {
    openCandidatePicker({ popovers, candidates, estimateArrival, onPick, ...opts });
  }

  function openAnchorTimePopover(candidate) {
    const distanceFromStart = candidate.distance_from_start;
    popovers.open(candidate.lat, candidate.lon, (container, close) => {
      const estimate = estimateArrival(distanceFromStart);

      container.append(popoverHeading(formatDistanceKm(distanceFromStart)));
      const hint = estimateHint(estimate);
      if (hint) {
        container.append(hint);
      }

      const toggleContainer = document.createElement('div');
      const warning = popoverHint('', { warning: true, hidden: true });
      const confirmBtn = confirmButton('Add anchor');
      confirmBtn.disabled = true;
      container.append(toggleContainer, warning, confirmBtn);

      /** @type {TimeResult} */
      let lastResult = { isValid: false };
      let usable = false;
      createTimeToggle({
        container: toggleContainer,
        variant: 'durationOrTimeOfDay',
        getReferenceTime: () => appState.startTime,
        getTotalDurationSeconds: () => appState.durationSeconds(),
        ...toggleDefaultsFromEstimate(estimate, appState.startTime),
        onChange: (result) => {
          lastResult = result;
          // Nothing to say while the time is still being typed.
          const problem = result.isValid ? checks.typedTimeProblem(distanceFromStart, result.resolvedDate) : null;
          usable = result.isValid && problem === null;
          confirmBtn.disabled = !usable;
          warning.textContent = problem ?? '';
          warning.hidden = problem === null;
        },
      });

      confirmBtn.addEventListener('click', () => {
        if (!usable) {
          return;
        }
        anchors.addAnchor({
          lat: candidate.lat,
          lon: candidate.lon,
          distanceFromStart,
          timestamp: lastResult.resolvedDate,
          offsetSeconds: offsetSecondsOf(lastResult),
        });
        close();
      });
    });
  }

  // A popup that only explains, at the point it's about.
  function openNotice(candidate, headingText, hintText) {
    popovers.open(candidate.lat, candidate.lon, (container) => {
      container.append(popoverHeading(headingText), popoverHint(hintText));
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
   * Picks a photo's point, whichever way it got one (placed, resolved or
   * dragged). One candidate goes straight to `onPick`, or to a notice saying
   * why it can't be used. Several open the "Which pass?" picker, with the
   * passes that are out of order disabled. `onDismiss` runs when nothing is
   * picked.
   *
   * @param {import('./candidatePicker.js').Candidate[]} candidates
   * @param {Date} time - the photo's capture time
   * @param {(candidate: import('./candidatePicker.js').Candidate) => void} onPick
   * @param {{ excludeAnchorId?: number, onDismiss?: () => void }} [opts]
   */
  function choosePhotoCandidate(candidates, time, onPick, { excludeAnchorId, onDismiss } = {}) {
    const conflicts = candidates.map((c) => checks.photoOrderConflict(c.distance_from_start, time, excludeAnchorId));
    if (candidates.length > 1) {
      pickCandidate(candidates, onPick, { photoTime: time, conflicts, onDismiss });
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

  function appendOutsideWindowHint(container, timestamp) {
    if (!checks.isInsideWindow(timestamp)) {
      container.append(popoverHint(PHOTO_OUTSIDE_WINDOW_HINT, { warning: true }));
    }
  }

  /**
   * The card a tap opens while a photo waits to be placed. The photo's time
   * is shown but can't be changed. "Not here" just closes the card: the
   * photo stays armed for another tap.
   */
  function openPhotoPlaceCard(candidate, photo) {
    if (isAtRouteEnd(candidate)) {
      openRouteEndHint(candidate, 'place this photo');
      return;
    }
    popovers.open(candidate.lat, candidate.lon, (container, close) => {
      container.append(photoSummary(photo, `At ${formatDistanceKm(candidate.distance_from_start)} along the route`));
      appendOutsideWindowHint(container, photo.timestamp);
      const { el, cancelBtn, confirmBtn } = cardActions('Not here', 'Place photo here');
      container.append(el);
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
    popovers.open(candidate.lat, candidate.lon, (container, close) => {
      container.append(photoSummary(
        { thumbnailUrl: anchor.thumbnailUrl ?? null, timestamp: taken },
        `Move from ${formatDistanceKm(anchor.distanceFromStart)} to ${formatDistanceKm(candidate.distance_from_start)}? Its time stays the same.`,
      ));
      appendOutsideWindowHint(container, taken);
      const { el, cancelBtn, confirmBtn } = cardActions('Keep it where it was', 'Move');
      container.append(el);
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
    if (isAtRouteEnd(candidate)) {
      openRouteEndHint(candidate, 'add an anchor or stop');
      return;
    }
    popovers.open(candidate.lat, candidate.lon, (container, close) => {
      const anchorBtn = optionButton('Add anchor', 'A single known timestamp');
      anchorBtn.addEventListener('click', () => {
        close();
        openAnchorTimePopover(candidate);
      });

      const stopBtn = optionButton('Add stop', 'A real pause at this location');
      stopBtn.addEventListener('click', () => {
        close();
        openStopPopover(candidate, { popovers, appState, stops, checks, estimateArrival });
      });

      const list = document.createElement('div');
      list.className = 'candidate-list';
      list.append(anchorBtn, stopBtn);
      container.append(popoverHeading(formatDistanceKm(candidate.distance_from_start)), list);
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
        pickCandidate(candidates, openKindChoicePopover);
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
      if (isAtRouteEnd(candidate)) {
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
