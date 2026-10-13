import assert from 'node:assert/strict';
import { beforeEach, describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import { createAnchorPlacer } from '../../../src/gpx2fit/gui/js/placement/anchorPopovers.js';
import { createAppState } from '../../../src/gpx2fit/gui/js/state/appState.js';

const at = (hours, minutes, seconds = 0) => new Date(2024, 5, 1, hours, minutes, seconds);

// A 20 km route from 10:00 to 14:00, so the uniform-pace estimate at 10 km is 12:00.
const START = at(10, 0);
const END = at(14, 0);
const TOTAL_DISTANCE = 20_000;

const candidateAt = (distance) => ({ lat: 46 + distance / 1e6, lon: 8, distance_from_start: distance });

/**
 * Stands in for the popover host: every popover is built into a plain <div>,
 * and like Leaflet only one is open at a time, so opening one closes the last
 * (and runs its `onClose`).
 */
function createFakePopovers() {
  const popups = [];
  function close(popup) {
    if (popup.closed) {
      return;
    }
    popup.closed = true;
    popup.container.remove();
    popup.onClose?.();
  }
  return {
    popups,
    current: () => popups.findLast((popup) => !popup.closed) ?? null,
    open(lat, lon, build, { onClose } = {}) {
      popups.filter((popup) => !popup.closed).forEach(close);
      const popup = { lat, lon, container: document.createElement('div'), closed: false, onClose };
      document.body.append(popup.container);
      popups.push(popup);
      build(popup.container, () => close(popup), () => {});
    },
    closeCurrent() {
      const popup = this.current();
      if (popup) {
        close(popup);
      }
    },
  };
}

/** Stands in for map.js: records the placement mode's on/off. */
function createFakeMap() {
  return {
    placementModes: [],
    setPlacementMode(on) {
      this.placementModes.push(on);
    },
  };
}

function mount({ activeAnchors = [], activeStops = [] } = {}) {
  const appState = createAppState({ sport: 'hiking' });
  appState.setRoute({ points: [], totalDistance: TOTAL_DISTANCE, fileName: 'route.gpx' });
  appState.setTime(START, { isValid: true, mode: 'duration', resolvedDate: END, durationSeconds: 4 * 3600 });

  const added = { anchors: [], stops: [], moves: [] };
  const statuses = [];
  const ctx = { candidates: [] };
  const map = createFakeMap();
  const popovers = createFakePopovers();
  const placer = createAnchorPlacer({
    appState,
    anchors: {
      getActiveAnchors: () => activeAnchors,
      addAnchor: (anchor) => added.anchors.push(anchor),
      moveAnchor: (id, position) => added.moves.push({ id, ...position }),
    },
    stops: {
      getActiveStops: () => activeStops,
      addStop: (stop) => added.stops.push(stop),
    },
    map,
    popovers,
    resolveAnchorCandidates: async () => ctx.candidates,
    revealMap: () => {},
    setStatus: (message, kind) => statuses.push({ message, kind }),
  });
  return { placer, map, popovers, added, statuses, ctx };
}

function type(input, value) {
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function typeTime(container, index, hours, minutes) {
  type(container.querySelectorAll('input[aria-label="Time of day hours"]')[index], hours);
  type(container.querySelectorAll('input[aria-label="Time of day minutes"]')[index], minutes);
}

function optionTitled(container, title) {
  const option = [...container.querySelectorAll('.candidate-option')]
    .find((button) => button.querySelector('.candidate-distance')?.textContent === title);
  assert.ok(option, `no option titled "${title}"`);
  return option;
}

function confirmButton(container) {
  return container.querySelector('.popover-confirm');
}

/** The warning under the time fields, or null while it's hidden. */
function warning(container) {
  const shown = [...container.querySelectorAll('.popover-hint.is-warning')].filter((hint) => !hint.hidden);
  return shown.map((hint) => hint.textContent).join(' ') || null;
}

async function openKindChoice(m, distance) {
  m.ctx.candidates = [candidateAt(distance)];
  await m.placer.handleRouteClick(0, 0);
  return m.popovers.current().container;
}

async function openAnchorPopover(m, distance) {
  optionTitled(await openKindChoice(m, distance), 'Add anchor').click();
  return m.popovers.current().container;
}

async function openStopPopover(m, distance) {
  optionTitled(await openKindChoice(m, distance), 'Add stop').click();
  return m.popovers.current().container;
}

describe('createAnchorPlacer', () => {
  beforeEach(() => {
    document.body.replaceChildren();
  });

  describe('a route tap', () => {
    it('offers an anchor or a stop at the tapped point', async () => {
      const m = mount();
      const container = await openKindChoice(m, 10_000);

      assert.equal(container.querySelector('.popover-heading').textContent, '10.00 km');
      assert.ok(optionTitled(container, 'Add anchor'));
      assert.ok(optionTitled(container, 'Add stop'));
    });

    it('explains the route\'s start instead of offering anything there', async () => {
      const m = mount();
      const container = await openKindChoice(m, 0);

      assert.equal(container.querySelectorAll('.candidate-option').length, 0);
      assert.match(container.textContent, /This is the route's start/);
    });

    it('asks which pass first on a spot the route visits twice', async () => {
      const m = mount();
      m.ctx.candidates = [candidateAt(4_000), candidateAt(16_000)];
      await m.placer.handleRouteClick(0, 0);

      const picker = m.popovers.current().container;
      const estimates = [...picker.querySelectorAll('.candidate-estimate')].map((el) => el.textContent);
      assert.deepEqual(estimates, ['~10:48 (uniform-pace estimate)', '~13:12 (uniform-pace estimate)']);

      optionTitled(picker, '16.00 km').click();
      assert.equal(m.popovers.current().container.querySelector('.popover-heading').textContent, '16.00 km');
    });

    it('needs a valid start and duration first', async () => {
      // No setTime, so there's no valid start–end.
      const appState = createAppState();
      const statuses = [];
      const placer = createAnchorPlacer({
        appState,
        anchors: {},
        stops: {},
        map: createFakeMap(),
        popovers: createFakePopovers(),
        resolveAnchorCandidates: async () => [candidateAt(10_000)],
        revealMap: () => {},
        setStatus: (message, kind) => statuses.push({ message, kind }),
      });

      await placer.handleRouteClick(0, 0);

      assert.deepEqual(statuses, [{ message: 'Set a valid duration or end time before adding anchors.', kind: 'input' }]);
    });
  });

  describe('the anchor popover', () => {
    it('opens on the time of day, pre-filled from the estimate', async () => {
      const m = mount();
      const container = await openAnchorPopover(m, 10_000);

      assert.equal(container.querySelector('input[aria-label="Time of day hours"]').value, '12');
      assert.equal(container.querySelector('input[aria-label="Time of day minutes"]').value, '00');
      assert.equal(confirmButton(container).disabled, false);
    });

    it('adds an anchor at the time entered', async () => {
      const m = mount();
      const container = await openAnchorPopover(m, 10_000);
      typeTime(container, 0, '11', '30');
      confirmButton(container).click();

      assert.equal(m.added.anchors.length, 1);
      const [anchor] = m.added.anchors;
      assert.equal(anchor.distanceFromStart, 10_000);
      assert.deepEqual(anchor.timestamp, at(11, 30));
      assert.equal(anchor.offsetSeconds, undefined);
      assert.equal(m.popovers.current(), null);
    });

    it('keeps the offset of a time entered as a duration since the start', async () => {
      const m = mount();
      const container = await openAnchorPopover(m, 10_000);
      container.querySelector('.segmented-option[data-value="duration"]').click();
      type(container.querySelector('input[aria-label="Hours"]'), '1');
      type(container.querySelector('input[aria-label="Minutes"]'), '15');
      confirmButton(container).click();

      assert.equal(m.added.anchors[0].offsetSeconds, 75 * 60);
      assert.deepEqual(m.added.anchors[0].timestamp, at(11, 15));
    });

    it('refuses a time outside the activity, and says so', async () => {
      const m = mount();
      const container = await openAnchorPopover(m, 10_000);
      typeTime(container, 0, '15', '00');

      assert.equal(confirmButton(container).disabled, true);
      assert.equal(warning(container), 'This time isn\'t between the activity\'s start and end.');
      confirmButton(container).click();
      assert.deepEqual(m.added.anchors, []);
    });

    it('refuses a time earlier than an anchor behind it, and says which way to go', async () => {
      const m = mount({
        activeAnchors: [{ id: 1, distanceFromStart: 5_000, timestamp: at(12, 30), source: 'user' }],
      });
      const container = await openAnchorPopover(m, 10_000);

      // The estimate, 12:00, is already out of order.
      assert.equal(confirmButton(container).disabled, true);
      assert.equal(warning(container), 'The anchor at 5.00 km is at 12:30:00, so this time has to be later.');

      typeTime(container, 0, '12', '45');
      assert.equal(confirmButton(container).disabled, false);
      assert.equal(warning(container), null);
    });

    it('refuses a time later than a stop ahead of it', async () => {
      const m = mount({
        activeStops: [{ id: 1, distanceFromStart: 15_000, mode: 'startEnd', arrival: at(11, 0), departure: at(11, 10) }],
      });
      const container = await openAnchorPopover(m, 10_000);

      assert.equal(confirmButton(container).disabled, true);
      assert.equal(
        warning(container),
        'The stop at 15.00 km is at 11:00:00–11:10:00, so this time has to be earlier.',
      );
      confirmButton(container).click();
      assert.deepEqual(m.added.anchors, []);
    });

    it('refuses a point another anchor is already on', async () => {
      const m = mount({
        activeAnchors: [{ id: 1, distanceFromStart: 10_000, timestamp: at(11, 0), source: 'photo' }],
      });
      const container = await openAnchorPopover(m, 10_000);

      assert.equal(confirmButton(container).disabled, true);
      assert.match(warning(container), /^The photo at 10\.00 km is already on this point\./);
    });
  });

  describe('the stop popover', () => {
    function showStartEnd(container) {
      container.querySelector('.segmented-option[data-value="startEnd"]').click();
    }

    it('makes its two modes tabs, each tied to the panel it shows', async () => {
      const m = mount({});
      const container = await openStopPopover(m, 10_000);
      const tabs = [...container.querySelectorAll('.segmented-option[data-value]')].slice(0, 2);
      assert.deepEqual(tabs.map((tab) => tab.dataset.value), ['duration', 'startEnd']);
      assert.deepEqual(tabs.map((tab) => tab.getAttribute('role')), ['tab', 'tab']);
      const [durationPanel, startEndPanel] = tabs.map((tab) =>
        container.querySelector(`#${tab.getAttribute('aria-controls')}`),
      );
      assert.ok(durationPanel.querySelector('input[aria-label="Minutes"]'), 'the duration panel holds the duration');
      assert.ok(startEndPanel.querySelector('.stop-time-columns'), 'the start & end panel holds the columns');
      assert.equal(startEndPanel.getAttribute('role'), 'tabpanel');
    });

    it('adds a duration-only stop, which has no clock time to be out of order', async () => {
      const m = mount({
        activeAnchors: [{ id: 1, distanceFromStart: 5_000, timestamp: at(13, 0), source: 'user' }],
      });
      const container = await openStopPopover(m, 10_000);
      type(container.querySelector('input[aria-label="Minutes"]'), '15');
      confirmButton(container).click();

      assert.deepEqual(m.added.stops, [{
        lat: candidateAt(10_000).lat,
        lon: 8,
        distanceFromStart: 10_000,
        mode: 'duration',
        durationSeconds: 900,
      }]);
    });

    it('adds a start/end stop from the arrival and the departure', async () => {
      const m = mount();
      const container = await openStopPopover(m, 10_000);
      showStartEnd(container);
      // The arrival is pre-filled from the estimate, 12:00.
      typeTime(container, 1, '12', '20');
      assert.equal(warning(container), null);
      confirmButton(container).click();

      assert.equal(m.added.stops.length, 1);
      const [stop] = m.added.stops;
      assert.equal(stop.mode, 'startEnd');
      assert.deepEqual(stop.arrival, at(12, 0));
      assert.deepEqual(stop.departure, at(12, 20));
    });

    it('refuses a departure before the arrival, and says so', async () => {
      const m = mount();
      const container = await openStopPopover(m, 10_000);
      showStartEnd(container);
      typeTime(container, 1, '11', '50');

      assert.equal(confirmButton(container).disabled, true);
      assert.equal(warning(container), 'The departure has to be after the arrival.');
    });

    it('refuses a stay that runs past the next point\'s time', async () => {
      const m = mount({
        activeAnchors: [{ id: 1, distanceFromStart: 15_000, timestamp: at(12, 30), source: 'user' }],
      });
      const container = await openStopPopover(m, 10_000);
      showStartEnd(container);
      typeTime(container, 1, '12', '40');

      assert.equal(confirmButton(container).disabled, true);
      assert.equal(warning(container), 'The anchor at 15.00 km is at 12:30:00, so the departure has to be earlier.');

      typeTime(container, 1, '12', '20');
      assert.equal(confirmButton(container).disabled, false);
      assert.equal(warning(container), null);
    });

    it('refuses an arrival before the point behind it was passed', async () => {
      const m = mount({
        activeAnchors: [{ id: 1, distanceFromStart: 5_000, timestamp: at(12, 10), source: 'user' }],
      });
      const container = await openStopPopover(m, 10_000);
      showStartEnd(container);
      typeTime(container, 1, '12', '30');

      assert.equal(confirmButton(container).disabled, true);
      assert.equal(warning(container), 'The anchor at 5.00 km is at 12:10:00, so the arrival has to be later.');
    });

    it('refuses a time outside the activity', async () => {
      const m = mount();
      const container = await openStopPopover(m, 10_000);
      showStartEnd(container);
      typeTime(container, 1, '14', '30');

      assert.equal(confirmButton(container).disabled, true);
      assert.equal(warning(container), 'This time isn\'t between the activity\'s start and end.');
    });
  });

  describe('placing a photo', () => {
    function armPhoto(m, timestamp) {
      const photo = { timestamp, thumbnailUrl: null, placed: [], cancelled: 0 };
      photo.onPlaced = (position) => photo.placed.push(position);
      photo.onCancel = () => {
        photo.cancelled += 1;
      };
      m.placer.placePhoto(photo);
      return photo;
    }

    it('opens a card at the tap, and places the photo on confirm', async () => {
      const m = mount();
      const photo = armPhoto(m, at(11, 0));
      m.ctx.candidates = [candidateAt(10_000)];
      await m.placer.handleRouteClick(0, 0);

      const card = m.popovers.current().container;
      assert.equal(card.querySelector('.popover-heading').textContent, 'Photo taken 11:00:00');
      card.querySelector('.popover-confirm').click();

      assert.deepEqual(photo.placed, [{ lat: candidateAt(10_000).lat, lon: 8, distanceFromStart: 10_000 }]);
      assert.deepEqual(m.map.placementModes, [true, false]);
    });

    it('keeps the photo armed on "Not here"', async () => {
      const m = mount();
      const photo = armPhoto(m, at(11, 0));
      m.ctx.candidates = [candidateAt(10_000)];
      await m.placer.handleRouteClick(0, 0);
      m.popovers.current().container.querySelector('.popover-secondary').click();

      assert.deepEqual(photo.placed, []);
      assert.equal(photo.cancelled, 0);
      assert.deepEqual(m.map.placementModes, [true]);
    });

    it('refuses a point out of time order, with the reason, and stays armed', async () => {
      const m = mount({
        activeAnchors: [{ id: 1, distanceFromStart: 5_000, timestamp: at(12, 0), source: 'user' }],
      });
      const photo = armPhoto(m, at(11, 0));
      m.ctx.candidates = [candidateAt(10_000)];
      await m.placer.handleRouteClick(0, 0);

      const notice = m.popovers.current().container;
      assert.equal(notice.querySelector('.popover-heading').textContent, 'Not in time order here');
      assert.match(notice.textContent, /place this one before 5\.00 km/);
      assert.equal(photo.cancelled, 0);
    });

    it('places a photo from outside the activity\'s time anywhere, since it isn\'t used yet', async () => {
      const m = mount({
        activeAnchors: [{ id: 1, distanceFromStart: 5_000, timestamp: at(12, 0), source: 'user' }],
      });
      // After the 14:00 end, and behind nothing in time: in order it would
      // have to go past 5 km.
      armPhoto(m, at(15, 0));
      m.ctx.candidates = [candidateAt(4_000)];
      await m.placer.handleRouteClick(0, 0);

      const card = m.popovers.current().container;
      assert.equal(card.querySelector('.popover-heading').textContent, 'Photo taken 15:00:00');
      assert.match(warning(card), /^Taken outside the activity's time/);
    });
  });

  describe('dragging a photo pin', () => {
    const photoAnchor = { id: 7, distanceFromStart: 8_000, timestamp: at(11, 0), source: 'photo', thumbnailUrl: null };

    async function drop(m, distance) {
      let reverted = 0;
      m.ctx.candidates = [candidateAt(distance)];
      await m.placer.handleAnchorDrag(photoAnchor, 0, 0, () => {
        reverted += 1;
      });
      return () => reverted;
    }

    it('moves only once "Move" is confirmed', async () => {
      const m = mount();
      const reverted = await drop(m, 9_000);
      m.popovers.current().container.querySelector('.popover-confirm').click();

      assert.deepEqual(m.added.moves, [{ id: 7, lat: candidateAt(9_000).lat, lon: 8, distanceFromStart: 9_000 }]);
      assert.equal(reverted(), 0);
    });

    it('puts the pin back when the card closes any other way', async () => {
      const m = mount();
      const reverted = await drop(m, 9_000);
      m.popovers.closeCurrent();

      assert.deepEqual(m.added.moves, []);
      assert.equal(reverted(), 1);
    });

    it('doesn\'t offer a move of under 10 m', async () => {
      const m = mount();
      const reverted = await drop(m, 8_006);

      assert.equal(m.popovers.current(), null);
      assert.equal(reverted(), 1);
      assert.match(m.statuses.at(-1).message, /less than 10 m/);
    });
  });
});
