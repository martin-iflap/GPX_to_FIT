// Shared multimode time input: a segmented toggle, one editable field for
// whichever mode is active, and a live-computed, non-editable preview of the
// resulting value underneath. Used both for the main start-time control
// ("Duration" / "End time") and for each anchor popover ("Duration since
// start" / "Time of day") — same shape, different mode pair, selected via
// `variant`.
//
// The main control gets one extra, opt-in mode: "Avg speed" (see the
// `speedMode` option), which derives the duration from the route's distance
// instead of asking for it. Every mode resolves to the same
// `{ isValid, mode, resolvedDate, durationSeconds }` shape, so nothing
// downstream — and nothing in `core/` — has to know which one produced it.

import {
  formatClock,
  formatDateTime,
  formatDistanceKm,
  formatDuration,
  formatPace,
  formatSpeedKmh,
  pad,
} from '../util/format.js';
import { createDateTimeField, createDaySelector, createTimeField, linkSegmentPair } from './dateTimeField.js';
import { computeDayCount } from '../util/dates.js';
import { createFocusDropdown } from './focusDropdown.js';
import { createNumericInput, wireNumericBox } from './numericInput.js';
import { createSegmented } from './segmented.js';

const MINUTES_MAX = 59;
const SECONDS_MAX = 59;
// A tenth of a km/h is the finest anybody can honestly claim to hold.
const SPEED_STEP_KMH = 0.1;

// The avg-speed quick-picks: the band a person on foot plausibly holds for a
// whole activity, so the common case is one click instead of typing. Anything
// outside it (or between two entries) is still typed into the boxes.
const PACE_PRESET_MIN_SECONDS = 3 * 60 + 30; // 3:30 /km
const PACE_PRESET_MAX_SECONDS = 7 * 60 + 30; // 7:30 /km
const PACE_PRESET_STEP_SECONDS = 30;
const SPEED_PRESET_MIN_KMH = 3;
const SPEED_PRESET_MAX_KMH = 10;
const SPEED_PRESET_STEP_KMH = 0.5;

/** The label a unit is written as, both on the switch button and in the preview. */
function unitLabel(unit) {
  return unit === 'speed' ? 'km/h' : 'min/km';
}

/**
 * The quick-pick list for one speed unit, in ascending label order (3:30 →
 * 7:30, 3.0 → 10.0) so each list reads the way its numbers do rather than
 * the way its speeds do — they run opposite ways, and the number is what the
 * eye scans. Each entry carries its own m/s so a pick can fill the boxes
 * through the same `writeSpeedMps` the unit switch uses.
 * @param {'pace'|'speed'} unit
 * @returns {{label: string, metersPerSecond: number}[]}
 */
function unitPresets(unit) {
  const presets = [];
  if (unit === 'speed') {
    // Counted in tenths rather than adding 0.5 repeatedly, which drifts into
    // values like 7.499999999999999 and then labels them "7.5" anyway.
    const stepTenths = SPEED_PRESET_STEP_KMH * 10;
    for (let tenths = SPEED_PRESET_MIN_KMH * 10; tenths <= SPEED_PRESET_MAX_KMH * 10; tenths += stepTenths) {
      const kmh = tenths / 10;
      presets.push({ label: kmh.toFixed(1), metersPerSecond: kmh / 3.6 });
    }
    return presets;
  }
  for (let seconds = PACE_PRESET_MIN_SECONDS; seconds <= PACE_PRESET_MAX_SECONDS; seconds += PACE_PRESET_STEP_SECONDS) {
    presets.push({ label: `${Math.floor(seconds / 60)}:${pad(seconds % 60)}`, metersPerSecond: 1000 / seconds });
  }
  return presets;
}

/**
 * Builds the segmented Duration/Absolute toggle described above and mounts
 * it into `container`.
 *
 * @param {object} opts
 * @param {HTMLElement} opts.container - element to mount the toggle into
 * @param {'durationOrEnd'|'durationOrTimeOfDay'|'durationOnly'} opts.variant - which pair of
 *   modes to offer: elapsed-duration vs. either an absolute end time (main
 *   start-time control) or a same-day time-of-day (anchor popovers);
 *   'durationOnly' omits the mode toggle entirely and always resolves in
 *   duration mode (used by a stop's duration-only entry)
 * @param {() => Date|null} opts.getReferenceTime - the moment durations are
 *   measured from (the route's start time); re-read on every resolve, so the
 *   toggle stays correct if the reference changes after mount
 * @param {() => number|null} [opts.getTotalDurationSeconds] - the whole
 *   activity's total duration, used only by the 'durationOrTimeOfDay'
 *   variant's "Time of day" mode to decide whether the activity spans more
 *   than one calendar day. When it does, a thin day selector appears above
 *   the time-of-day field (defaulting to day 1) so the same clock time can
 *   be disambiguated between days; single-day activities are unaffected
 *   (the selector never renders). Omitted entirely for the other variants.
 * @param {(result: {isValid: boolean, mode?: string, resolvedDate?: Date, durationSeconds?: number}) => void} opts.onChange -
 *   called with the freshly-resolved value whenever the user edits a field,
 *   switches mode, or the caller calls `refresh()`
 * @param {{hours: number, minutes: number}} [opts.initialTimeOfDay] - for the
 *   'durationOrTimeOfDay' variant only: pre-fills the "Time of day" field and
 *   opens on that tab instead of "Duration since start" (used to seed an
 *   anchor popover with its estimated arrival time)
 * @param {number} [opts.initialDayOffset] - for the 'durationOrTimeOfDay'
 *   variant only, paired with `initialTimeOfDay`: which day (0-based offset
 *   from the activity's first day) that estimated time actually falls on,
 *   so a multi-day activity's day selector opens pre-set to the right day
 *   instead of always defaulting to day 1 — which otherwise routinely
 *   pre-fills a time that's invalid to confirm (earlier than an anchor
 *   already placed on a later day). Applied once, the first time the day
 *   selector's day count is computed; ignored without `initialTimeOfDay`.
 * @param {number} [opts.initialDurationSeconds] - pre-fills the Duration
 *   fields (hours/minutes) regardless of which mode ends up active, so
 *   switching tabs later still shows a sensible value instead of 0h 00m
 * @param {'duration'|'timeOfDay'|'end'} [opts.initialMode] - which tab opens
 *   initially when there's no `initialTimeOfDay` to imply it (e.g. a stop's
 *   departure toggle, which has no reasonable time to pre-fill but should
 *   still open on the same tab as its arrival toggle); ignored if
 *   `initialTimeOfDay` is set, since that already forces the other-key tab
 * @param {{getTotalDistance: () => number|null, getStopSeconds: () => number,
 *   getSport: () => string}} [opts.speedMode] - opt-in third tab, "Avg speed",
 *   honored only by the 'durationOrEnd' variant so the anchor/stop popovers
 *   keep exactly their own two (or zero) modes. The entered speed is a
 *   **moving** speed: the duration resolves to
 *   `getTotalDistance() / speed + getStopSeconds()`, which is both how people
 *   talk about pace and the reason this mode can never produce a duration too
 *   short for its own stops (combine.py's stop-budget InputError). The unit
 *   defaults from `getSport()` (min/km for running, km/h otherwise) until the
 *   user picks one, mirroring profilePanel.js metric default; the single
 *   button beside the boxes always names the *other* unit, so it reads as the
 *   alternative rather than repeating the suffix next to it. Focusing a box
 *   opens a quick-pick list (see `unitPresets`). All three getters are
 *   re-read on every resolve, so the derived duration follows the route, the
 *   sport and the stop list as they change.
 * @returns {{ refresh: () => void, getResult: () => object }} `refresh()`
 *   re-resolves and re-renders the preview against the current reference
 *   time (call this if `getReferenceTime()`'s value changes elsewhere);
 *   `getResult()` re-resolves without touching the DOM.
 */
export function createTimeToggle({
  container,
  variant,
  getReferenceTime,
  getTotalDurationSeconds,
  onChange,
  initialTimeOfDay,
  initialDayOffset,
  initialDurationSeconds,
  initialMode,
  speedMode,
}) {
  const isDurationOnly = variant === 'durationOnly';
  const state = { mode: 'duration', dayOffset: 0 };
  const otherKey = variant === 'durationOrEnd' ? 'end' : 'timeOfDay';
  // Scoped deliberately: an anchor or stop popover has no "whole route" to
  // divide by, so the third tab only exists on the main start-time control.
  const speedEnabled = variant === 'durationOrEnd' && Boolean(speedMode);
  // Null until the user clicks a unit themselves; until then the sport picks
  // it (see currentUnit). Same shape as profilePanel.js `chosenMetric`.
  let chosenUnit = null;
  // Consumed once, the first time refreshDaySelector runs (see below).
  // After that the day selector's own click handler is the only thing that
  // should move it, so a later, unrelated refresh doesn't keep snapping the
  // day back to this initial value.
  let pendingInitialDayOffset = typeof initialDayOffset === 'number' ? initialDayOffset : null;

  // 'durationOnly' gets no tabs at all (see the mounting below).
  const modeOptions = [{ value: 'duration', label: variant === 'durationOrEnd' ? 'Duration' : 'Duration since start' }];
  if (!isDurationOnly) {
    modeOptions.push({ value: otherKey, label: variant === 'durationOrEnd' ? 'End time' : 'Time of day' });
  }
  if (speedEnabled) {
    modeOptions.push({ value: 'avgSpeed', label: 'Avg speed' });
  }
  const modes = isDurationOnly
    ? null
    : createSegmented({ options: modeOptions, value: 'duration', onSelect: setMode, className: 'time-toggle-modes' });

  const fieldsEl = document.createElement('div');
  fieldsEl.className = 'time-toggle-fields';

  const durationFields = document.createElement('div');
  durationFields.className = 'duration-fields';

  function createSuffix(text) {
    const el = document.createElement('span');
    el.className = 'input-suffix';
    el.textContent = text;
    return el;
  }

  // type="text" + inputMode="numeric", not type="number" — see numericInput.js.
  const hoursInput = createNumericInput('Hours');
  hoursInput.value = '0';
  const minutesInput = createNumericInput('Minutes');
  minutesInput.value = '0';

  durationFields.append(hoursInput, createSuffix('h'), minutesInput, createSuffix('m'));

  const otherFields = document.createElement('div');
  otherFields.className = 'other-fields hidden';
  fieldsEl.append(durationFields, otherFields);

  // Both unit shapes are built once and the inactive one is just hidden, so
  // switching units is a class toggle rather than a rebuild — which keeps the
  // value each box holds while the user flips back and forth.
  const speedFields = document.createElement('div');
  speedFields.className = 'speed-fields hidden';

  // .duration-fields is reused for the row layout: same centered box+suffix
  // run as the h/m pair, which is exactly what these are. What is new is
  // .speed-entry, holding the typed boxes *only* — the trailing unit suffix
  // and the switch button are its siblings in the row. The quick-pick list
  // hangs from .speed-entry, so drawing the line there is what decides where
  // the list sits: anything else put inside it drags the list off the
  // numbers by half that thing's width.
  const kmhRow = document.createElement('div');
  kmhRow.className = 'duration-fields speed-fields-row speed-fields-kmh';
  const kmhEntry = document.createElement('div');
  kmhEntry.className = 'speed-entry';
  const kmhInput = createNumericInput('Average speed', '4.5');
  kmhEntry.append(kmhInput);
  kmhRow.append(kmhEntry, createSuffix('km/h'));

  const paceRow = document.createElement('div');
  paceRow.className = 'duration-fields speed-fields-row speed-fields-pace';
  const paceEntry = document.createElement('div');
  paceEntry.className = 'speed-entry';
  const paceMinutesInput = createNumericInput('Pace minutes', '5');
  const paceSecondsInput = createNumericInput('Pace seconds', '30');
  // The ':' stays inside: it sits *between* two boxes, so it is part of the
  // number, not a trailing unit label.
  paceEntry.append(paceMinutesInput, createSuffix(':'), paceSecondsInput);
  paceRow.append(paceEntry, createSuffix('/km'));

  const speedInputs = [kmhInput, paceMinutesInput, paceSecondsInput];

  // One button rather than a two-option segmented control, sitting in the
  // entry row itself: it is always labeled with the unit it switches *to*,
  // so it states the alternative instead of restating what the suffix beside
  // it already says. `applyUnit` moves it into whichever row is visible.
  const unitButton = document.createElement('button');
  unitButton.type = 'button';
  unitButton.className = 'speed-unit-button';

  // Quick-picks, opened by focusing any speed box, like the start time's
  // half-hours. Built for the unit current when it opens, so each option
  // carries that unit: a stale list would otherwise fill the wrong boxes.
  const presetDropdown = createFocusDropdown({
    triggers: speedInputs,
    className: 'time-field-dropdown speed-preset-dropdown',
    optionClassName: 'time-field-option',
    getOptions: () => {
      const unit = currentUnit();
      return unitPresets(unit).map(({ label, metersPerSecond }) => ({ label, value: { metersPerSecond, unit } }));
    },
    getScrollIndex: nearestPresetIndex,
    onPick: ({ metersPerSecond, unit }) => {
      writeSpeedMps(metersPerSecond, unit);
      updatePreview();
    },
  });

  speedFields.append(kmhRow, paceRow);

  if (speedEnabled) {
    fieldsEl.append(speedFields);
  }

  // 'end': a full Date from createDateTimeField. 'timeOfDay': just
  // {hours, minutes} from createTimeField (the day is always the reference's).
  let otherValue = null;
  let timeOfDayField = null; // only set for the 'timeOfDay' branch, so initialTimeOfDay can prefill it
  let endDateTimeField = null; // only set for the 'end' branch, so its date can default to the start's
  let daySelector = null; // only set for the 'timeOfDay' branch; stays hidden on single-day activities

  if (!isDurationOnly) {
    if (otherKey === 'end') {
      endDateTimeField = createDateTimeField({
        container: otherFields,
        dateAriaLabel: 'End date',
        timeAriaLabel: 'End time',
        onChange: (date) => {
          otherValue = date;
          updatePreview();
        },
      });
    } else {
      // Stacked vertically (day selector above the clock boxes) rather than
      // inline with them, so it reads as "which day, then which time"
      // instead of competing for the same row.
      const timeOfDayStack = document.createElement('div');
      timeOfDayStack.className = 'time-of-day-stack';
      otherFields.append(timeOfDayStack);

      daySelector = createDaySelector({
        container: timeOfDayStack,
        onChange: (dayIndex) => {
          state.dayOffset = dayIndex;
          updatePreview();
        },
      });

      // No quick-picks here: an anchor's arrival time is meant to be precise
      // (that's the whole point of adding it), not rounded to a half-hour.
      timeOfDayField = createTimeField({
        container: timeOfDayStack,
        ariaLabel: 'Time of day',
        quickPicks: false,
        onChange: (value) => {
          otherValue = value;
          updatePreview();
        },
      });
    }
  }

  const previewEl = document.createElement('p');
  previewEl.className = 'time-toggle-preview';

  // Only the static #startTimeToggle in index.html carries this class in
  // markup; every toggle built at runtime (anchor/stop popovers) needs it
  // applied here so it gets the same gap above its fields instead of
  // sitting flush against the mode buttons.
  container.classList.add('time-toggle');
  // 'durationOnly' has no tabs: an empty `.segmented` would still render
  // as a blank gray box. Its spacing comes back as plain padding (see
  // .time-toggle--duration-only in styles.css), so the fields don't sit
  // flush against the stop popover's own mode tabs above them.
  if (modes) {
    container.append(modes.el);
  } else {
    container.classList.add('time-toggle--duration-only');
  }
  container.append(fieldsEl, previewEl);

  function currentReference() {
    const ref = getReferenceTime();
    return ref instanceof Date && !Number.isNaN(ref.getTime()) ? ref : null;
  }

  /** Which unit the speed boxes are in: the user's pick, else the sport's. */
  function currentUnit() {
    if (chosenUnit) {
      return chosenUnit;
    }
    return speedMode?.getSport() === 'running' ? 'pace' : 'speed';
  }

  /** The route's length in meters, or null before a GPX has been loaded. */
  function currentTotalDistance() {
    const distance = speedMode?.getTotalDistance();
    return Number.isFinite(distance) && distance > 0 ? distance : null;
  }

  /** Reads the boxes of one specific unit as m/s, or null if blank/zero. */
  function speedMpsFor(unit) {
    if (unit === 'speed') {
      const kmh = Number(kmhInput.value);
      return Number.isFinite(kmh) && kmh > 0 ? kmh / 3.6 : null;
    }
    const minutes = Number(paceMinutesInput.value) || 0;
    const seconds = Number(paceSecondsInput.value) || 0;
    const secondsPerKm = minutes * 60 + seconds;
    return secondsPerKm > 0 ? 1000 / secondsPerKm : null;
  }

  /** Writes a speed into one unit's boxes, e.g. when the unit is switched. */
  function writeSpeedMps(metersPerSecond, unit) {
    if (unit === 'speed') {
      kmhInput.value = (metersPerSecond * 3.6).toFixed(1);
      return;
    }
    const secondsPerKm = Math.round(1000 / metersPerSecond);
    paceMinutesInput.value = String(Math.floor(secondsPerKm / 60));
    paceSecondsInput.value = pad(secondsPerKm % 60);
  }

  function resolve() {
    const reference = currentReference();
    if (!reference) {
      return { isValid: false };
    }

    if (state.mode === 'duration') {
      const hours = Number(hoursInput.value);
      const minutes = Number(minutesInput.value);
      if (!Number.isFinite(hours) || !Number.isFinite(minutes) || hours < 0 || minutes < 0) {
        return { isValid: false };
      }
      const durationSeconds = hours * 3600 + minutes * 60;
      if (durationSeconds <= 0) {
        return { isValid: false };
      }
      const resolvedDate = new Date(reference.getTime() + durationSeconds * 1000);
      return { isValid: true, mode: 'duration', resolvedDate, durationSeconds };
    }

    if (state.mode === 'avgSpeed') {
      const totalDistance = currentTotalDistance();
      const speedMps = speedMpsFor(currentUnit());
      if (totalDistance === null || speedMps === null) {
        return { isValid: false };
      }
      // Moving speed, so stopped time is added on top rather than eaten out
      // of it — see the `speedMode` docs above.
      const movingSeconds = totalDistance / speedMps;
      const stopSeconds = Math.max(0, speedMode.getStopSeconds() || 0);
      const durationSeconds = Math.round(movingSeconds + stopSeconds);
      const resolvedDate = new Date(reference.getTime() + durationSeconds * 1000);
      return {
        isValid: true,
        mode: 'avgSpeed',
        resolvedDate,
        durationSeconds,
        movingSeconds,
        stopSeconds,
        speedMps,
      };
    }

    if (otherKey === 'end') {
      if (!otherValue) {
        return { isValid: false };
      }
      const durationSeconds = (otherValue.getTime() - reference.getTime()) / 1000;
      if (durationSeconds <= 0) {
        return { isValid: false };
      }
      return { isValid: true, mode: 'end', resolvedDate: otherValue, durationSeconds };
    }

    if (!otherValue) {
      return { isValid: false };
    }
    const resolvedDate = new Date(reference);
    resolvedDate.setDate(resolvedDate.getDate() + state.dayOffset);
    resolvedDate.setHours(otherValue.hours, otherValue.minutes, 0, 0);
    const durationSeconds = (resolvedDate.getTime() - reference.getTime()) / 1000;
    return { isValid: durationSeconds >= 0, mode: 'timeOfDay', resolvedDate, durationSeconds };
  }

  function refreshDaySelector(reference) {
    if (!daySelector) {
      return;
    }
    const totalDurationSeconds = typeof getTotalDurationSeconds === 'function' ? getTotalDurationSeconds() : null;
    const dayCount = reference ? computeDayCount(reference, totalDurationSeconds) : 1;
    daySelector.setDayCount(dayCount, reference, pendingInitialDayOffset ?? undefined);
    pendingInitialDayOffset = null;
    state.dayOffset = daySelector.getDayIndex();
  }

  /**
   * Shows whichever unit's boxes are current, and moves the switch button and
   * the quick-picks into that row — they belong to the visible entry, not to
   * a unit, so there is only ever one of each in the DOM.
   */
  function applyUnit(unit) {
    kmhRow.classList.toggle('hidden', unit !== 'speed');
    paceRow.classList.toggle('hidden', unit !== 'pace');

    const activeRow = unit === 'speed' ? kmhRow : paceRow;
    const activeEntry = unit === 'speed' ? kmhEntry : paceEntry;
    // Guarded because updatePreview re-applies the unit on every keystroke.
    // Re-appending an element already in place is still a DOM move (which
    // drops focus from it in some browsers), and the row only changes when
    // the unit does — which is also the only thing the label depends on.
    if (unitButton.parentElement === activeRow) {
      return;
    }
    const otherUnit = unit === 'speed' ? 'pace' : 'speed';
    unitButton.dataset.value = otherUnit;
    unitButton.textContent = unitLabel(otherUnit);
    unitButton.setAttribute('aria-label', `Switch to ${unitLabel(otherUnit)}`);
    unitButton.title = `Switch to ${unitLabel(otherUnit)}`;
    activeRow.append(unitButton);
    activeEntry.append(presetDropdown.el);
  }

  /**
   * The preset nearest what's already entered, so a second visit doesn't
   * start back at the top of a 15-row list. Null with nothing entered.
   */
  function nearestPresetIndex(options) {
    const current = speedMpsFor(currentUnit());
    if (current === null) {
      return null;
    }
    let nearest = 0;
    options.forEach(({ value }, index) => {
      if (Math.abs(value.metersPerSecond - current) < Math.abs(options[nearest].value.metersPerSecond - current)) {
        nearest = index;
      }
    });
    return nearest;
  }

  /** What to say when the active mode can't resolve to a duration yet. */
  function invalidMessage() {
    if (state.mode === 'avgSpeed') {
      return currentTotalDistance() === null
        ? 'Upload a route first — the duration comes from its distance.'
        : 'Enter an average speed greater than zero.';
    }
    return state.mode === 'duration' ? 'Enter a duration greater than zero.' : 'Enter a valid time.';
  }

  /**
   * Spells out the whole derivation, because the number the user typed isn't
   * the one that ends up in the FIT: they see which distance it was divided
   * by, how much stopped time was added, and where that lands them.
   */
  function describeSpeedResult(result) {
    const speedText = currentUnit() === 'pace' ? formatPace(result.speedMps) : formatSpeedKmh(result.speedMps);
    const head = `${formatDistanceKm(currentTotalDistance())} at ${speedText} → ${formatDuration(result.movingSeconds)}`;
    const body = result.stopSeconds > 0 ? `${head} moving + ${formatDuration(result.stopSeconds)} stopped` : head;
    return `${body} · ends ${formatDateTime(result.resolvedDate)}`;
  }

  function updatePreview() {
    const reference = currentReference();
    refreshDaySelector(reference);
    // Re-applied every time so the sport's default unit follows a sport
    // change, right up until the user picks a unit themselves.
    if (speedEnabled) {
      applyUnit(currentUnit());
    }
    if (!reference) {
      previewEl.textContent =
        variant === 'durationOrEnd'
          ? 'Set a start time to see the computed end time.'
          : 'Set the route start time first.';
      onChange({ isValid: false });
      return;
    }

    const result = resolve();
    if (!result.isValid) {
      previewEl.textContent = invalidMessage();
      onChange({ isValid: false });
      return;
    }

    if (state.mode === 'avgSpeed') {
      previewEl.textContent = describeSpeedResult(result);
    } else if (state.mode === 'duration') {
      if (isDurationOnly) {
        previewEl.textContent = `Duration: ${formatDuration(result.durationSeconds)}`;
      } else {
        previewEl.textContent =
          variant === 'durationOrEnd' ? `Ends at ${formatDateTime(result.resolvedDate)}` : `At ${formatClock(result.resolvedDate)}`;
      }
    } else {
      previewEl.textContent = `Duration: ${formatDuration(result.durationSeconds)}`;
    }

    onChange(result);
  }

  // Each mode's fields, shown only while its tab is active.
  const modeFields = { duration: durationFields, [otherKey]: otherFields, avgSpeed: speedFields };

  // A function declaration, so the tabs built above can already name it.
  function setMode(mode) {
    state.mode = mode;
    modes?.select(mode);
    Object.entries(modeFields).forEach(([key, fields]) => fields.classList.toggle('hidden', key !== mode));

    if (speedEnabled && mode !== 'avgSpeed') {
      // Otherwise it is still open, unfocused, when the tab comes back.
      presetDropdown.close();
    }

    if (mode === otherKey && endDateTimeField) {
      const reference = currentReference();
      if (reference) {
        endDateTimeField.prefillDateIfEmpty(reference);
      }
    }

    updatePreview();
  }

  // Registered before the preview listener so the preview always reads a
  // value the digit filter has already been through.
  wireNumericBox(hoursInput);
  wireNumericBox(minutesInput, { max: MINUTES_MAX });
  [hoursInput, minutesInput].forEach((el) => el.addEventListener('input', updatePreview));
  linkSegmentPair(hoursInput, minutesInput);

  if (speedEnabled) {
    wireNumericBox(kmhInput, { decimal: true, step: SPEED_STEP_KMH });
    wireNumericBox(paceMinutesInput);
    wireNumericBox(paceSecondsInput, { max: SECONDS_MAX });
    speedInputs.forEach((el) => el.addEventListener('input', updatePreview));
    // The pace pair gets the same edge-arrow hopping as the h/m pair; the
    // single km/h box has nowhere to hop to.
    linkSegmentPair(paceMinutesInput, paceSecondsInput);

    unitButton.addEventListener('click', () => {
      // Read both before switching: `dataset.value` is the unit being moved
      // to, and the speed has to be read in the *old* unit so it carries
      // across instead of the number jumping to a different meaning.
      const unit = unitButton.dataset.value;
      const carried = speedMpsFor(currentUnit());
      chosenUnit = unit;
      // The open list belongs to the unit being left behind.
      presetDropdown.close();
      applyUnit(unit);
      if (carried !== null) {
        writeSpeedMps(carried, unit);
      }
      updatePreview();
    });
  }

  if (typeof initialDurationSeconds === 'number' && initialDurationSeconds > 0) {
    const totalMinutes = Math.round(initialDurationSeconds / 60);
    hoursInput.value = String(Math.floor(totalMinutes / 60));
    minutesInput.value = String(totalMinutes % 60);
  }

  if (!isDurationOnly && initialTimeOfDay && timeOfDayField) {
    timeOfDayField.setValue(initialTimeOfDay.hours, initialTimeOfDay.minutes);
    setMode(otherKey);
  } else if (!isDurationOnly && initialMode === otherKey) {
    setMode(otherKey);
  } else {
    setMode('duration');
  }

  return {
    refresh: updatePreview,
    getResult: resolve,
  };
}
