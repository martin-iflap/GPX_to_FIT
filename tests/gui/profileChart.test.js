import './testUtils/domSetup.js';

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  areaPath,
  buildSeries,
  createProfileChart,
  linearScale,
  linePath,
  nearestSampleIndex,
  niceTicks,
  stepTicks,
  stopRuns,
} from '../../src/gpx2fit/gui/js/profileChart.js';

const identity = (value) => value;

function sample(distanceFromStart, elapsedSeconds, speedMps, extra = {}) {
  return { distanceFromStart, elapsedSeconds, speedMps, elevation: 100, lat: 46, lon: 8, isStop: false, ...extra };
}

const SAMPLES = [
  sample(0, 0, 2.5),
  sample(500, 200, 2.5, { elevation: 120 }),
  sample(500, 200, 0, { isStop: true, elevation: 120 }),
  sample(500, 500, 0, { isStop: true, elevation: 120 }),
  sample(1000, 750, 2, { elevation: 90 }),
];

function sizedContainer(width, height) {
  const container = document.createElement('div');
  Object.defineProperty(container, 'clientWidth', { value: width });
  Object.defineProperty(container, 'clientHeight', { value: height });
  document.body.append(container);
  return container;
}

describe('niceTicks', () => {
  it('uses 1/2/5 steps covering the range', () => {
    assert.deepEqual(niceTicks(0, 10, 5), [0, 2, 4, 6, 8, 10]);
    assert.deepEqual(niceTicks(0, 1000, 4), [0, 500, 1000]);
  });

  it('avoids float drift in decimal ticks', () => {
    assert.deepEqual(niceTicks(0, 0.6, 6), [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6]);
  });

  it('returns a single tick for an empty range', () => {
    assert.deepEqual(niceTicks(5, 5), [5]);
  });
});

describe('stepTicks', () => {
  it('picks the smallest candidate step that fits the count', () => {
    // 2 hours in at most 5 ticks: 30-minute steps.
    assert.deepEqual(stepTicks(0, 7200, 5, [60, 600, 1800, 3600]), [0, 1800, 3600, 5400, 7200]);
  });
});

describe('linearScale', () => {
  it('maps and inverts', () => {
    const scale = linearScale(0, 10, 100, 200);
    assert.equal(scale(5), 150);
    assert.equal(scale.invert(150), 5);
  });

  it('supports an inverted range', () => {
    const scale = linearScale(0, 10, 200, 100);
    assert.equal(scale(10), 100);
  });
});

describe('buildSeries', () => {
  it('uses elapsed seconds or distance for x', () => {
    assert.deepEqual(buildSeries(SAMPLES, 'time', 'speed').xs, [0, 200, 200, 500, 750]);
    assert.deepEqual(buildSeries(SAMPLES, 'distance', 'speed').xs, [0, 500, 500, 500, 1000]);
  });

  it('shows speed in km/h, with stops at zero', () => {
    assert.deepEqual(buildSeries(SAMPLES, 'time', 'speed').speeds, [9, 9, 0, 0, 7.2]);
  });

  it('shows pace in seconds per km, with stops as gaps', () => {
    assert.deepEqual(buildSeries(SAMPLES, 'time', 'pace').speeds, [400, 400, null, null, 500]);
  });

  it('caps very slow paces', () => {
    const { speeds } = buildSeries([sample(0, 0, 0.01)], 'time', 'pace');
    assert.equal(speeds[0], 30 * 60);
  });

  it('turns missing elevation into null', () => {
    const { elevations } = buildSeries([sample(0, 0, 1, { elevation: undefined })], 'time', 'speed');
    assert.deepEqual(elevations, [null]);
  });
});

describe('linePath', () => {
  it('starts a new subpath after a null', () => {
    assert.equal(linePath([0, 1, 2, 3], [5, null, 6, 7], identity, identity), 'M0.0,5.0M2.0,6.0L3.0,7.0');
  });

  it('leaves out skipped segments', () => {
    assert.equal(linePath([0, 1, 2], [5, 6, 7], identity, identity, (i) => i === 2), 'M0.0,5.0L1.0,6.0M2.0,7.0');
  });
});

describe('stopRuns', () => {
  it('groups consecutive stop samples', () => {
    assert.deepEqual(stopRuns(SAMPLES), [[2, 3]]);
  });

  it('keeps separate stops apart', () => {
    const stop = { isStop: true };
    const moving = { isStop: false };
    assert.deepEqual(stopRuns([stop, stop, moving, stop, moving]), [[0, 1], [3, 3]]);
  });
});

describe('areaPath', () => {
  it('closes the area down to the baseline and bridges nulls', () => {
    assert.equal(areaPath([0, 1, 2], [5, null, 7], identity, identity, 10), 'M0.0,10.0L0.0,5.0L2.0,7.0L2.0,10.0Z');
  });

  it('is empty without any values', () => {
    assert.equal(areaPath([0, 1], [null, null], identity, identity, 10), '');
  });
});

describe('nearestSampleIndex', () => {
  it('finds the closest value', () => {
    const xs = [0, 10, 20, 30];
    assert.equal(nearestSampleIndex(xs, 14), 1);
    assert.equal(nearestSampleIndex(xs, 16), 2);
    assert.equal(nearestSampleIndex(xs, -5), 0);
    assert.equal(nearestSampleIndex(xs, 99), 3);
  });

  it('returns -1 for no values', () => {
    assert.equal(nearestSampleIndex([], 3), -1);
  });
});

describe('createProfileChart', () => {
  it('draws nothing until it has data', () => {
    const container = sizedContainer(600, 200);
    createProfileChart(container);
    assert.equal(container.querySelector('svg').childElementCount, 0);
  });

  it('draws the elevation area and the speed line', () => {
    const container = sizedContainer(600, 200);
    const chart = createProfileChart(container);
    chart.setData(SAMPLES);
    assert.ok(container.querySelector('.profile-elevation-area').getAttribute('d').endsWith('Z'));
    assert.ok(container.querySelector('.profile-speed-line').getAttribute('d').startsWith('M'));
  });

  it('leaves out a hidden series and its axis labels', () => {
    const container = sizedContainer(600, 200);
    const chart = createProfileChart(container);
    chart.setData(SAMPLES);

    chart.setOptions({ showSpeed: false });
    assert.equal(container.querySelector('.profile-speed-line'), null);
    assert.equal(container.querySelectorAll('.profile-axis-label--speed').length, 0);
    assert.ok(container.querySelector('.profile-elevation-area'));
    // Grid lines fall back to the elevation axis.
    assert.ok(container.querySelectorAll('.profile-grid line').length > 0);

    chart.setOptions({ showSpeed: true, showElevation: false });
    assert.ok(container.querySelector('.profile-speed-line'));
    assert.equal(container.querySelector('.profile-elevation-area'), null);
  });

  it('draws a stop as its own line, at zero speed or along the bottom for pace', () => {
    const container = sizedContainer(600, 200);
    const chart = createProfileChart(container);
    chart.setData(SAMPLES);
    const stopY = () => Number(container.querySelector('.profile-stop-line').getAttribute('y1'));
    // The x axis sits at the plot's bottom edge, 24px up from the container's.
    assert.equal(stopY(), 176);

    chart.setOptions({ metric: 'pace' });
    assert.equal(stopY(), 176);
  });

  it('draws a stop as a dot on the distance axis, where it has no width', () => {
    const container = sizedContainer(600, 200);
    const chart = createProfileChart(container);
    chart.setData(SAMPLES);
    chart.setOptions({ axis: 'distance' });
    assert.equal(container.querySelector('.profile-stop-line'), null);
    assert.ok(container.querySelector('.profile-stop-dot'));
  });

  it('draws faster paces higher on a pace chart', () => {
    const container = sizedContainer(600, 200);
    const chart = createProfileChart(container);
    chart.setData(SAMPLES);
    chart.setOptions({ metric: 'pace' });
    const labels = [...container.querySelectorAll('.profile-axis-label--speed')];
    const ys = labels.map((el) => Number(el.getAttribute('y')));
    // Labels are emitted from the fastest pace up to the slowest, so each sits lower on screen.
    for (let i = 1; i < ys.length; i++) {
      assert.ok(ys[i] > ys[i - 1]);
    }
  });

  it('reports the hovered sample, and null on leave', () => {
    const container = sizedContainer(600, 200);
    const hovered = [];
    const chart = createProfileChart(container, { onHover: (s) => hovered.push(s) });
    chart.setData(SAMPLES);
    const svg = container.querySelector('svg');
    svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 600, height: 200 });

    svg.dispatchEvent(new MouseEvent('pointermove', { clientX: 10000 }));
    svg.dispatchEvent(new MouseEvent('pointerleave'));

    assert.equal(hovered.length, 2);
    assert.equal(hovered[0], SAMPLES[SAMPLES.length - 1]);
    assert.equal(hovered[1], null);
    assert.equal(container.querySelector('.profile-tooltip').hidden, true);
  });
});
