// Shared multimode time input: a segmented toggle, one editable field for
// whichever mode is active, and a live-computed, non-editable preview of the
// resulting value underneath. Used both for the main start-time control
// ("Duration" / "End time") and for each anchor popover ("Duration since
// start" / "Time of day") — same shape, different mode pair, selected via
// `variant`.
//
// The main control gets one extra, opt-in mode: "Avg speed" (see the
// `speedMode` option), which derives the duration from the route's distance
// instead of asking for it (its boxes are speedEntry.js's). Every mode
// resolves to the same `{ isValid, mode, resolvedDate, durationSeconds }`
// shape, so nothing downstream — and nothing in `core/` — has to know which
// one produced it.

import {
  formatClock,
  formatDateTime,
  formatDistanceKm,
  formatDuration,
  formatPace,
  formatSpeedKmh,
} from '../util/format.js';
import { createDateTimeField, createDaySelector, createTimeField, linkSegmentPair } from './dateTimeField.js';
import { computeDayCount } from '../util/dates.js';
import { createNumericInput, wireNumericBox } from './numericInput.js';
import { createSegmented } from './segmented.js';
import { createSpeedEntry } from './speedEntry.js';

/**
 * What every mode resolves to. Only `durationSeconds` crosses the bridge;
 * "Avg speed" adds the parts it was worked out from, for its preview.
 *
 * @typedef {{
 *   isValid: boolean,
 *   mode?: 'duration'|'end'|'timeOfDay'|'avgSpeed',
 *   resolvedDate?: Date,
 *   durationSeconds?: number,
 *   movingSeconds?: number,
 *   stopSeconds?: number,
 *   speedMps?: number,
 * }} TimeResult
 */

const MINUTES_MAX = 59;

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
 * @param {(result: TimeResult) => void} opts.onChange -
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
 *   short for its own stops (combine.py's stop-budget InputError). The boxes,
 *   their unit and their quick-picks are speedEntry.js's. All three getters
 *   are re-read on every resolve, so the derived duration follows the route,
 *   the sport and the stop list as they change.
 * @returns {{ refresh: () => void, getResult: () => TimeResult,
 *   focusMode: (mode: string) => boolean, nextMode: () => boolean }}
 *   `refresh()` re-resolves and re-renders the preview against the current
 *   reference time (call this if `getReferenceTime()`'s value changes
 *   elsewhere); `getResult()` re-resolves without touching the DOM.
 *   `focusMode(mode)` opens that tab, as a click on it would, and focuses its
 *   first box (false if this toggle has no such mode); `nextMode()` does the
 *   same for the tab after the active one, wrapping (false without tabs).
 *   They are for the keyboard shortcuts, which then needn't know this markup.
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
    : createSegmented({
        options: modeOptions,
        value: 'duration',
        onSelect: setMode,
        kind: 'tabs',
        className: 'time-toggle-modes',
      });

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

  // Only the main control has the tab, so only it gets the boxes.
  const speedEntry = speedEnabled
    ? createSpeedEntry({ getSport: () => speedMode.getSport(), onInput: () => updatePreview() })
    : null;
  if (speedEntry) {
    fieldsEl.append(speedEntry.el);
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
  // .time-toggle--duration-only in css/time.css), so the fields don't sit
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

  /** The route's length in meters, or null before a GPX has been loaded. */
  function currentTotalDistance() {
    const distance = speedMode?.getTotalDistance();
    return Number.isFinite(distance) && distance > 0 ? distance : null;
  }

  /** @returns {TimeResult} */
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
      const speedMps = speedEntry.getSpeedMps();
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
    const speedText = speedEntry.getUnit() === 'pace' ? formatPace(result.speedMps) : formatSpeedKmh(result.speedMps);
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
      speedEntry.refresh();
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
  /** @type {Record<string, HTMLElement>} */
  const modeFields = { duration: durationFields, [otherKey]: otherFields };
  if (speedEntry) {
    modeFields.avgSpeed = speedEntry.el;
  }
  Object.entries(modeFields).forEach(([mode, fields]) => modes?.linkPanel(mode, fields));

  // A function declaration, so the tabs built above can already name it.
  function setMode(mode) {
    state.mode = mode;
    modes?.select(mode);
    Object.entries(modeFields).forEach(([key, fields]) => fields.classList.toggle('hidden', key !== mode));

    if (speedEnabled && mode !== 'avgSpeed') {
      // Otherwise it is still open, unfocused, when the tab comes back.
      speedEntry.closePresets();
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

  // How each mode's first box takes focus, for focusMode.
  const modeFocus = {
    duration: () => {
      hoursInput.focus();
      hoursInput.select();
    },
    [otherKey]: () => (endDateTimeField ?? timeOfDayField)?.focus(),
    avgSpeed: () => speedEntry.focus(),
  };

  function focusMode(mode) {
    if (!modeOptions.some((option) => option.value === mode)) {
      return false;
    }
    setMode(mode);
    modeFocus[mode]();
    return true;
  }

  return {
    refresh: updatePreview,
    getResult: resolve,
    focusMode,
    nextMode() {
      if (!modes) {
        return false;
      }
      const index = modeOptions.findIndex((option) => option.value === state.mode);
      return focusMode(modeOptions[(index + 1) % modeOptions.length].value);
    },
  };
}
