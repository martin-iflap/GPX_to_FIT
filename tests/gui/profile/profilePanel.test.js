// createProfilePanel with the real chart, against #profilePanel's markup from
// index.html and a fake map. requestAnimationFrame is queued by hand, so the
// "open on a clean frame" wait is stepped explicitly.

import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import { createProfilePanel } from '../../../src/gpx2fit/gui/js/profile/profilePanel.js';

function sample(distanceFromStart, elapsedSeconds, speedMps) {
  return { distanceFromStart, elapsedSeconds, speedMps, elevation: 100, lat: 46, lon: 8, isStop: false };
}

const SAMPLES = [sample(0, 0, 2.5), sample(500, 200, 2.5), sample(1000, 450, 2)];

let frames = [];

/** Runs the animation frames queued so far (not ones they queue in turn). */
function nextFrame() {
  const due = frames;
  frames = [];
  due.forEach((callback) => callback());
}

/** Runs frames until none are left, as a browser would before painting twice. */
function settleFrames() {
  for (let i = 0; i < 10 && frames.length > 0; i += 1) {
    nextFrame();
  }
}

beforeEach(() => {
  frames = [];
  globalThis.requestAnimationFrame = (callback) => frames.push(callback);
});

afterEach(() => {
  delete globalThis.requestAnimationFrame;
  document.body.innerHTML = '';
});

function mount() {
  document.body.innerHTML = `
    <div id="appShell">
      <section id="profilePanel" aria-hidden="true" inert>
        <button id="profileSpeedToggle" aria-pressed="true"><span id="profileSpeedLabel">Speed (km/h)</span></button>
        <button id="profileElevationToggle" aria-pressed="true">Elevation (m)</button>
        <div id="profileMetricControl">
          <button class="segmented-option" data-value="pace">Pace</button>
          <button class="segmented-option is-active" data-value="speed">km/h</button>
        </div>
        <div id="profileAxisControl">
          <button class="segmented-option is-active" data-value="time">Time</button>
          <button class="segmented-option" data-value="distance">Distance</button>
        </div>
        <button id="profileCloseButton">Hide</button>
        <div id="profileChart"></div>
      </section>
      <button id="profileShowButton" hidden>Show profile</button>
    </div>`;
  const byId = (id) => document.getElementById(id);
  // jsdom lays nothing out, and the chart draws only into a sized area.
  Object.defineProperty(byId('profileChart'), 'clientWidth', { value: 600 });
  Object.defineProperty(byId('profileChart'), 'clientHeight', { value: 200 });
  const map = { hoverHidden: 0, showHoverMarker() {}, hideHoverMarker() { this.hoverHidden += 1; } };
  const panel = createProfilePanel({
    shellEl: byId('appShell'),
    panelEl: byId('profilePanel'),
    chartEl: byId('profileChart'),
    axisControlEl: byId('profileAxisControl'),
    metricControlEl: byId('profileMetricControl'),
    speedLabelEl: byId('profileSpeedLabel'),
    speedToggleEl: byId('profileSpeedToggle'),
    elevationToggleEl: byId('profileElevationToggle'),
    closeButtonEl: byId('profileCloseButton'),
    showButtonEl: byId('profileShowButton'),
    map,
  });
  const isOpen = () => byId('appShell').classList.contains('has-profile');
  const activeMetric = () => byId('profileMetricControl').querySelector('.is-active')?.dataset.value;
  return { panel, map, byId, isOpen, activeMetric };
}

describe('createProfilePanel', () => {
  it('opens a frame after the next one, not in the same task as the chart build', () => {
    const { panel, isOpen, byId } = mount();
    panel.show(SAMPLES, 'hiking');
    assert.equal(isOpen(), false);

    nextFrame();
    assert.equal(isOpen(), false);
    nextFrame();
    assert.equal(isOpen(), true);
    assert.equal(byId('profilePanel').getAttribute('aria-hidden'), 'false');
    assert.equal(byId('profilePanel').inert, false);
    assert.equal(byId('profileShowButton').hidden, true);
  });

  it('swaps the data in place, without waiting, when it is already open', () => {
    const { panel, isOpen } = mount();
    panel.show(SAMPLES, 'hiking');
    settleFrames();
    panel.show(SAMPLES, 'running');

    assert.equal(isOpen(), true);
    assert.equal(frames.length, 0);
  });

  it('defaults the unit to the sport: pace for a run, speed for a hike', () => {
    const { panel, byId, activeMetric } = mount();
    panel.show(SAMPLES, 'running');
    assert.equal(activeMetric(), 'pace');
    assert.equal(byId('profileSpeedLabel').textContent, 'Pace (min/km)');

    panel.show(SAMPLES, 'hiking');
    assert.equal(activeMetric(), 'speed');
    assert.equal(byId('profileSpeedLabel').textContent, 'Speed (km/h)');
  });

  it('keeps a unit the user picked, whatever the next conversion\'s sport', () => {
    const { panel, byId, activeMetric } = mount();
    panel.show(SAMPLES, 'hiking');
    byId('profileMetricControl').querySelector('[data-value="pace"]').click();
    assert.equal(byId('profileSpeedLabel').textContent, 'Pace (min/km)');

    panel.show(SAMPLES, 'hiking');
    assert.equal(activeMetric(), 'pace');
    assert.equal(byId('profileSpeedLabel').textContent, 'Pace (min/km)');
  });

  it('the close button hides it, leaves the "Show profile" pill and hides the map\'s hover dot', () => {
    const { panel, map, byId, isOpen } = mount();
    panel.show(SAMPLES, 'hiking');
    settleFrames();
    byId('profileCloseButton').click();

    assert.equal(isOpen(), false);
    assert.equal(byId('profilePanel').inert, true);
    assert.equal(byId('profileShowButton').hidden, false);
    assert.equal(document.activeElement, byId('profileShowButton'));
    assert.equal(map.hoverHidden, 1);
  });

  it('the pill reopens it and hides itself', () => {
    const { panel, byId, isOpen } = mount();
    panel.show(SAMPLES, 'hiking');
    settleFrames();
    byId('profileCloseButton').click();
    byId('profileShowButton').click();

    assert.equal(isOpen(), true);
    assert.equal(byId('profileShowButton').hidden, true);
    assert.equal(document.activeElement, byId('profileCloseButton'));
  });

  it('a new conversion reopens a panel the user hid', () => {
    const { panel, byId, isOpen } = mount();
    panel.show(SAMPLES, 'hiking');
    settleFrames();
    byId('profileCloseButton').click();
    panel.show(SAMPLES, 'hiking');
    settleFrames();

    assert.equal(isOpen(), true);
  });

  it('never lets the last series showing be hidden', () => {
    const { panel, byId } = mount();
    panel.show(SAMPLES, 'hiking');
    const speed = byId('profileSpeedToggle');
    const elevation = byId('profileElevationToggle');

    speed.click();
    assert.equal(speed.getAttribute('aria-pressed'), 'false');
    assert.equal(elevation.disabled, true);
    assert.equal(speed.disabled, false);

    speed.click();
    assert.equal(speed.getAttribute('aria-pressed'), 'true');
    assert.equal(elevation.disabled, false);
  });

  it('hide() closes it with no pill, and forgets the drawing', () => {
    const { panel, byId, isOpen } = mount();
    panel.show(SAMPLES, 'hiking');
    settleFrames();
    assert.notEqual(byId('profileChart').querySelector('path'), null);

    panel.hide();
    assert.equal(isOpen(), false);
    assert.equal(byId('profileShowButton').hidden, true);
    assert.equal(byId('profileChart').querySelector('path'), null);
  });

  it('a new GPX loaded before the panel finished opening keeps it closed', () => {
    const { panel, isOpen } = mount();
    panel.show(SAMPLES, 'hiking');
    panel.hide();
    settleFrames();
    assert.equal(isOpen(), false);

    // The next conversion opens it as usual.
    panel.show(SAMPLES, 'hiking');
    settleFrames();
    assert.equal(isOpen(), true);
  });
});
