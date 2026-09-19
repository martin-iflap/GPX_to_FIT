// Hand-rolled SVG chart for the post-conversion activity profile: elevation
// as a gray filled area in the background, speed (km/h) or pace (min/km,
// inverted so faster is higher) as a line on top, over either elapsed time
// or distance. Either series can be hidden. Pure presentation — the samples arrive
// already downsampled from core/activity_profile.py (see pyodideBridge.js
// convert()), so everything here is scales, paths, and hover lookup.
//
// All colors come from CSS classes keyed off theme tokens in styles.css, so
// a theme switch restyles the chart without a redraw.

import { formatDistanceKm, formatElapsed, formatPace, formatSpeedKmh } from './format.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

// Room for the left (speed/pace), right (elevation), and bottom (x) tick labels.
const MARGIN = { top: 12, right: 52, bottom: 24, left: 52 };

// Paces slower than this are drawn at this value, so one near-stationary
// sample can't squash the rest of a running line into a sliver.
const PACE_CAP_SECONDS_PER_KM = 30 * 60;

// Tick steps (seconds) that read naturally on a time axis or a pace axis.
const TIME_TICK_STEPS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400];
const PACE_TICK_STEPS = [10, 15, 30, 60, 120, 300, 600];

/**
 * Evenly spaced "nice" ticks (steps of 1, 2, or 5 × 10^n) covering [min, max].
 * @param {number} min
 * @param {number} max
 * @param {number} [count] - rough number of ticks wanted
 * @returns {number[]}
 */
export function niceTicks(min, max, count = 5) {
  if (!(max > min)) {
    return [min];
  }
  const rawStep = (max - min) / count;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const residual = rawStep / magnitude;
  const step = (residual > 5 ? 10 : residual > 2 ? 5 : residual > 1 ? 2 : 1) * magnitude;
  return ticksWithStep(min, max, step);
}

/**
 * Ticks covering [min, max] using the smallest of `steps` that yields at most `count` ticks.
 * @param {number} min
 * @param {number} max
 * @param {number} count
 * @param {number[]} steps - candidate steps, ascending
 * @returns {number[]}
 */
export function stepTicks(min, max, count, steps) {
  if (!(max > min)) {
    return [min];
  }
  const step = steps.find((s) => (max - min) / s <= count) ?? steps[steps.length - 1];
  return ticksWithStep(min, max, step);
}

function ticksWithStep(min, max, step) {
  const ticks = [];
  // Rounded to the step's own precision so 0.1 + 0.2 style float drift doesn't reach the labels.
  const decimals = Math.max(0, -Math.floor(Math.log10(step)) + 1);
  for (let value = Math.ceil(min / step) * step; value <= max + step * 1e-9; value += step) {
    ticks.push(Number(value.toFixed(decimals)));
  }
  return ticks;
}

/**
 * A linear scale mapping [domainMin, domainMax] onto [rangeMin, rangeMax].
 * @returns {((value: number) => number) & { invert: (pixel: number) => number }}
 */
export function linearScale(domainMin, domainMax, rangeMin, rangeMax) {
  const span = domainMax - domainMin || 1;
  const scale = (value) => rangeMin + ((value - domainMin) / span) * (rangeMax - rangeMin);
  scale.invert = (pixel) => domainMin + ((pixel - rangeMin) / (rangeMax - rangeMin || 1)) * span;
  return scale;
}

/**
 * Turns profile samples into the chart's plotted series.
 *
 * @param {{distanceFromStart: number, elapsedSeconds: number, speedMps: number, elevation?: number|null, isStop?: boolean}[]} samples
 * @param {'time'|'distance'} axis
 * @param {'pace'|'speed'} metric
 * @returns {{xs: number[], speeds: (number|null)[], elevations: (number|null)[]}}
 *   `xs` in seconds (time) or meters (distance). `speeds` in seconds per km
 *   (pace) or km/h (speed). For pace, a stop's samples are null, since a
 *   stop has no finite pace, which breaks the line there.
 */
export function buildSeries(samples, axis, metric) {
  const xs = samples.map((s) => (axis === 'time' ? s.elapsedSeconds : s.distanceFromStart));
  const speeds = samples.map((s) => {
    if (metric === 'pace') {
      return s.speedMps > 0 ? Math.min(1000 / s.speedMps, PACE_CAP_SECONDS_PER_KM) : null;
    }
    return s.speedMps * 3.6;
  });
  const elevations = samples.map((s) => (s.elevation == null ? null : s.elevation));
  return { xs, speeds, elevations };
}

/**
 * Index ranges of consecutive stop samples. core/activity_profile.py emits a
 * stop as two zero-speed samples (arrival, departure), so each run is
 * normally a pair.
 * @param {{isStop?: boolean}[]} samples
 * @returns {[number, number][]} inclusive [first, last] indices
 */
export function stopRuns(samples) {
  const runs = [];
  for (let i = 0; i < samples.length; i++) {
    if (!samples[i].isStop) {
      continue;
    }
    if (runs.length && runs[runs.length - 1][1] === i - 1) {
      runs[runs.length - 1][1] = i;
    } else {
      runs.push([i, i]);
    }
  }
  return runs;
}

/**
 * SVG path data for a line through (xs[i], ys[i]), starting a new subpath after every null y.
 * @param xs
 * @param ys
 * @param scaleX
 * @param scaleY
 * @param {(i: number) => boolean} [skipSegment] - true leaves out the segment
 *   from point i-1 to point i, as if there were a null between them
 * @returns {string}
 */
export function linePath(xs, ys, scaleX, scaleY, skipSegment) {
  let path = '';
  let penDown = false;
  for (let i = 0; i < xs.length; i++) {
    if (ys[i] == null) {
      penDown = false;
      continue;
    }
    if (penDown && skipSegment?.(i)) {
      penDown = false;
    }
    path += `${penDown ? 'L' : 'M'}${scaleX(xs[i]).toFixed(1)},${scaleY(ys[i]).toFixed(1)}`;
    penDown = true;
  }
  return path;
}

/**
 * SVG path data for the area under (xs[i], ys[i]) down to `baselinePixel`.
 * Null ys are bridged over rather than breaking the area.
 * @returns {string}
 */
export function areaPath(xs, ys, scaleX, scaleY, baselinePixel) {
  const points = [];
  for (let i = 0; i < xs.length; i++) {
    if (ys[i] != null) {
      points.push(`${scaleX(xs[i]).toFixed(1)},${scaleY(ys[i]).toFixed(1)}`);
    }
  }
  if (points.length === 0) {
    return '';
  }
  const firstX = points[0].split(',')[0];
  const lastX = points[points.length - 1].split(',')[0];
  const baseline = baselinePixel.toFixed(1);
  return `M${firstX},${baseline}L${points.join('L')}L${lastX},${baseline}Z`;
}

/**
 * Index of the value in ascending `xs` closest to `x` (binary search).
 * @param {number[]} xs
 * @param {number} x
 * @returns {number} -1 if `xs` is empty
 */
export function nearestSampleIndex(xs, x) {
  if (xs.length === 0) {
    return -1;
  }
  let lo = 0;
  let hi = xs.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] < x) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  if (lo > 0 && x - xs[lo - 1] <= xs[lo] - x) {
    return lo - 1;
  }
  return lo;
}

function formatPaceTick(secondsPerKm) {
  const rounded = Math.round(secondsPerKm);
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, '0')}`;
}

function formatKmTick(meters, stepMeters) {
  const decimals = stepMeters >= 1000 ? 0 : stepMeters >= 100 ? 1 : 2;
  return `${(meters / 1000).toFixed(decimals)} km`;
}

function svgEl(tag, attrs = {}, className) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    el.setAttribute(key, String(value));
  }
  if (className) {
    el.setAttribute('class', className);
  }
  return el;
}

function extent(values) {
  let min = Infinity;
  let max = -Infinity;
  for (const value of values) {
    if (value != null) {
      min = Math.min(min, value);
      max = Math.max(max, value);
    }
  }
  return min <= max ? [min, max] : null;
}

/**
 * Builds the chart inside `container` (which must be sized by CSS) and
 * re-renders it whenever the container resizes.
 *
 * @param {HTMLElement} container
 * @param {{onHover?: (sample: object|null) => void}} [opts] - `onHover` gets the
 *   sample under the cursor, or null when the cursor leaves the chart
 * @returns {{
 *   setData: (samples: object[]) => void,
 *   setOptions: (options: Partial<ChartOptions>) => void,
 *   render: () => void,
 * }}
 *
 * @typedef {object} ChartOptions
 * @property {'time'|'distance'} axis
 * @property {'pace'|'speed'} metric
 * @property {boolean} showSpeed - draw the speed/pace line and its axis
 * @property {boolean} showElevation - draw the elevation area and its axis
 */
export function createProfileChart(container, { onHover } = {}) {
  container.classList.add('profile-chart');
  const svg = svgEl('svg', { role: 'img', 'aria-label': 'Speed and elevation profile' });
  const tooltip = document.createElement('div');
  tooltip.className = 'profile-tooltip';
  tooltip.hidden = true;
  container.append(svg, tooltip);

  let samples = [];
  /** @type {ChartOptions} */
  const options = { axis: 'time', metric: 'speed', showSpeed: true, showElevation: true };
  // Recomputed on every render; read by the pointer handlers.
  let layout = null;

  function hideHover() {
    if (layout) {
      layout.hoverGroup.setAttribute('visibility', 'hidden');
    }
    tooltip.hidden = true;
    onHover?.(null);
  }

  function showHover(index) {
    const { series, scaleX, scaleSpeed, scaleElevation, stopY, hoverGroup, crosshair, speedDot, elevationDot, plot } = layout;
    const sample = samples[index];
    const px = scaleX(series.xs[index]);
    crosshair.setAttribute('x1', px);
    crosshair.setAttribute('x2', px);

    // A stop's dot rides its amber line, which on a pace chart isn't at the (null) pace value.
    const speedY = !scaleSpeed ? null : sample.isStop ? stopY : series.speeds[index] == null ? null : scaleSpeed(series.speeds[index]);
    speedDot.setAttribute('visibility', speedY == null ? 'hidden' : 'visible');
    speedDot.classList.toggle('is-stop', Boolean(sample.isStop));
    if (speedY != null) {
      speedDot.setAttribute('cx', px);
      speedDot.setAttribute('cy', speedY);
    }
    const elevation = series.elevations[index];
    elevationDot.setAttribute('visibility', elevation == null || !scaleElevation ? 'hidden' : 'visible');
    if (elevation != null && scaleElevation) {
      elevationDot.setAttribute('cx', px);
      elevationDot.setAttribute('cy', scaleElevation(elevation));
    }
    hoverGroup.setAttribute('visibility', 'visible');

    const isPace = options.metric === 'pace';
    const rows = [
      ['Distance', formatDistanceKm(sample.distanceFromStart)],
      ['Time', formatElapsed(sample.elapsedSeconds)],
      ['Elevation', sample.elevation == null ? '—' : `${Math.round(sample.elevation)} m`],
      [isPace ? 'Pace' : 'Speed', sample.isStop ? 'Stopped' : isPace ? formatPace(sample.speedMps) : formatSpeedKmh(sample.speedMps)],
    ];
    tooltip.replaceChildren(
      ...rows.map(([label, value]) => {
        const row = document.createElement('div');
        row.className = 'profile-tooltip-row';
        const labelEl = document.createElement('span');
        labelEl.className = 'profile-tooltip-label';
        labelEl.textContent = label;
        const valueEl = document.createElement('span');
        valueEl.textContent = value;
        row.append(labelEl, valueEl);
        return row;
      }),
    );
    tooltip.hidden = false;
    // Flip to the cursor's left once it's past the middle, so it never runs off the right edge.
    const flip = px > plot.left + plot.width / 2;
    tooltip.style.top = `${plot.top}px`;
    tooltip.style.left = flip ? '' : `${px + 12}px`;
    tooltip.style.right = flip ? `${container.clientWidth - px + 12}px` : '';

    onHover?.(sample);
  }

  function render() {
    svg.replaceChildren();
    layout = null;
    tooltip.hidden = true;
    const width = container.clientWidth;
    const height = container.clientHeight;
    if (!samples.length || width <= 0 || height <= 0) {
      return;
    }
    svg.setAttribute('width', width);
    svg.setAttribute('height', height);
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);

    const plot = {
      left: MARGIN.left,
      top: MARGIN.top,
      width: Math.max(1, width - MARGIN.left - MARGIN.right),
      height: Math.max(1, height - MARGIN.top - MARGIN.bottom),
    };
    const plotBottom = plot.top + plot.height;
    const plotRight = plot.left + plot.width;
    const { axis, metric } = options;
    const series = buildSeries(samples, axis, metric);

    const xMax = series.xs[series.xs.length - 1] || 1;
    const scaleX = linearScale(0, xMax, plot.left, plotRight);

    // Speed/pace: km/h from zero up; pace inverted, so the fastest pace is at the top.
    let scaleSpeed = null;
    let speedTicks = [];
    if (options.showSpeed) {
      const speedExtent = extent(series.speeds) ?? [0, 1];
      if (metric === 'pace') {
        const [fast, slow] = speedExtent;
        const pad = Math.max((slow - fast) * 0.1, 15);
        const lo = Math.max(0, fast - pad);
        const hi = slow + pad;
        scaleSpeed = linearScale(hi, lo, plotBottom, plot.top);
        speedTicks = stepTicks(lo, hi, 5, PACE_TICK_STEPS);
      } else {
        const hi = Math.max(speedExtent[1] * 1.15, 1);
        speedTicks = niceTicks(0, hi, 5);
        scaleSpeed = linearScale(0, Math.max(hi, speedTicks[speedTicks.length - 1]), plotBottom, plot.top);
      }
    }

    // Elevation gets headroom above its peak, so the speed line mostly rides
    // above the gray area. Shown alone, it gets the full height instead.
    const elevationExtent = options.showElevation ? extent(series.elevations) : null;
    let scaleElevation = null;
    let elevationTicks = [];
    if (elevationExtent) {
      const [minElevation, maxElevation] = elevationExtent;
      const range = Math.max(maxElevation - minElevation, 20);
      const lo = minElevation - range * 0.05;
      const hi = minElevation + range * (scaleSpeed ? 1.4 : 1.1);
      scaleElevation = linearScale(lo, hi, plotBottom, plot.top);
      elevationTicks = niceTicks(minElevation, maxElevation, scaleSpeed ? 3 : 4);
    }

    const xTicks = axis === 'time'
      ? stepTicks(0, xMax, Math.max(2, Math.floor(plot.width / 80)), TIME_TICK_STEPS)
      : niceTicks(0, xMax, Math.max(2, Math.floor(plot.width / 80)));
    const xStep = xTicks.length > 1 ? xTicks[1] - xTicks[0] : xMax;

    // Grid lines follow the speed axis, or the elevation axis when speed is hidden.
    const grid = svgEl('g', {}, 'profile-grid');
    const [gridTicks, gridScale] = scaleSpeed ? [speedTicks, scaleSpeed] : [elevationTicks, scaleElevation];
    for (const tick of gridTicks) {
      const y = gridScale(tick).toFixed(1);
      grid.append(svgEl('line', { x1: plot.left, x2: plotRight, y1: y, y2: y }));
    }
    svg.append(grid);

    if (scaleElevation) {
      svg.append(
        svgEl('path', { d: areaPath(series.xs, series.elevations, scaleX, scaleElevation, plotBottom) }, 'profile-elevation-area'),
        svgEl('path', { d: linePath(series.xs, series.elevations, scaleX, scaleElevation) }, 'profile-elevation-line'),
      );
    }
    // Stops are drawn in their own muted amber, so a flat stretch at zero reads
    // as a break rather than a glitch. On a pace chart a stop has no pace, so
    // it sits along the bottom (the slow end) instead.
    const stopY = scaleSpeed ? (metric === 'pace' ? plotBottom : scaleSpeed(0)) : null;
    if (scaleSpeed) {
      const insideStop = (i) => samples[i].isStop && samples[i - 1].isStop;
      svg.append(svgEl('path', { d: linePath(series.xs, series.speeds, scaleX, scaleSpeed, insideStop) }, 'profile-speed-line'));
      const stops = svgEl('g', {}, 'profile-stops');
      for (const [first, last] of stopRuns(samples)) {
        const x1 = scaleX(series.xs[first]);
        const x2 = scaleX(series.xs[last]);
        // On the distance axis a stop has no width; a dot keeps it visible.
        stops.append(x2 - x1 < 2
          ? svgEl('circle', { cx: x1.toFixed(1), cy: stopY.toFixed(1), r: 2.5 }, 'profile-stop-dot')
          : svgEl('line', { x1: x1.toFixed(1), x2: x2.toFixed(1), y1: stopY.toFixed(1), y2: stopY.toFixed(1) }, 'profile-stop-line'));
      }
      svg.append(stops);
    }

    const labels = svgEl('g', {}, 'profile-axis-labels');
    for (const tick of speedTicks) {
      const text = svgEl('text', { x: plot.left - 8, y: scaleSpeed(tick), 'text-anchor': 'end', 'dominant-baseline': 'middle' }, 'profile-axis-label profile-axis-label--speed');
      text.textContent = metric === 'pace' ? formatPaceTick(tick) : String(tick);
      labels.append(text);
    }
    if (scaleElevation) {
      for (const tick of elevationTicks) {
        const text = svgEl('text', { x: plotRight + 8, y: scaleElevation(tick), 'text-anchor': 'start', 'dominant-baseline': 'middle' }, 'profile-axis-label');
        text.textContent = `${Math.round(tick)} m`;
        labels.append(text);
      }
    }
    for (const tick of xTicks) {
      const text = svgEl('text', { x: scaleX(tick), y: plotBottom + 16, 'text-anchor': 'middle' }, 'profile-axis-label');
      text.textContent = axis === 'time' ? formatElapsed(tick) : formatKmTick(tick, xStep);
      labels.append(text);
    }
    svg.append(labels);

    const hoverGroup = svgEl('g', { visibility: 'hidden' }, 'profile-hover');
    const crosshair = svgEl('line', { y1: plot.top, y2: plotBottom }, 'profile-crosshair');
    const elevationDot = svgEl('circle', { r: 3.5 }, 'profile-elevation-dot');
    const speedDot = svgEl('circle', { r: 4.5 }, 'profile-speed-dot');
    hoverGroup.append(crosshair, elevationDot, speedDot);
    svg.append(hoverGroup);

    layout = { series, plot, scaleX, scaleSpeed, scaleElevation, stopY, hoverGroup, crosshair, speedDot, elevationDot };
  }

  svg.addEventListener('pointermove', (event) => {
    if (!layout) {
      return;
    }
    const rect = svg.getBoundingClientRect();
    const px = Math.min(Math.max(event.clientX - rect.left, layout.plot.left), layout.plot.left + layout.plot.width);
    showHover(nearestSampleIndex(layout.series.xs, layout.scaleX.invert(px)));
  });
  svg.addEventListener('pointerleave', hideHover);

  if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(() => render()).observe(container);
  }

  return {
    setData(newSamples) {
      samples = newSamples;
      render();
    },
    setOptions(newOptions) {
      Object.assign(options, newOptions);
      render();
    },
    render,
  };
}
