// Shared dual-mode time input: a segmented Duration/Absolute toggle, one
// editable field for whichever mode is active, and a live-computed,
// non-editable preview of the other value underneath. Used both for the
// main start-time control ("Duration" / "End time") and for each anchor
// popover ("Duration since start" / "Time of day") — same shape, different
// mode pair, selected via `variant`.

import { formatClock, formatDateTime, formatDuration } from './format.js';
import { createDateTimeField, createDaySelector, createTimeField } from './dateTimeField.js';

/**
 * How many distinct calendar days `referenceStart` through
 * `referenceStart + totalDurationSeconds` spans, e.g. a start at 22:00 plus
 * a 4-hour duration spans 2 calendar days despite being under 24h. Returns
 * 1 (i.e. "single-day, no day selector needed") if the duration is unknown.
 */
export function computeDayCount(referenceStart, totalDurationSeconds) {
  if (!Number.isFinite(totalDurationSeconds) || totalDurationSeconds <= 0) {
    return 1;
  }
  const end = new Date(referenceStart.getTime() + totalDurationSeconds * 1000);
  const startMidnight = new Date(referenceStart.getFullYear(), referenceStart.getMonth(), referenceStart.getDate());
  const endMidnight = new Date(end.getFullYear(), end.getMonth(), end.getDate());
  const dayDiff = Math.round((endMidnight.getTime() - startMidnight.getTime()) / 86_400_000);
  return dayDiff + 1;
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
 *   prefills a time that's invalid to confirm (earlier than an anchor
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
}) {
  const isDurationOnly = variant === 'durationOnly';
  const state = { mode: 'duration', dayOffset: 0 };
  const otherKey = variant === 'durationOrEnd' ? 'end' : 'timeOfDay';
  // Consumed once, the first time refreshDaySelector runs (see below) —
  // after that the day selector's own click handler is the only thing that
  // should move it, so a later, unrelated refresh doesn't keep snapping the
  // day back to this initial value.
  let pendingInitialDayOffset = typeof initialDayOffset === 'number' ? initialDayOffset : null;

  const modesEl = document.createElement('div');
  modesEl.className = 'time-toggle-modes segmented';
  modesEl.setAttribute('role', 'tablist');

  const durationBtn = document.createElement('button');
  durationBtn.type = 'button';
  durationBtn.className = 'segmented-option is-active';
  durationBtn.textContent = variant === 'durationOrEnd' ? 'Duration' : 'Duration since start';
  durationBtn.setAttribute('role', 'tab');

  const otherBtn = document.createElement('button');
  otherBtn.type = 'button';
  otherBtn.className = 'segmented-option';
  otherBtn.textContent = variant === 'durationOrEnd' ? 'End time' : 'Time of day';
  otherBtn.setAttribute('role', 'tab');

  if (!isDurationOnly) {
    modesEl.append(durationBtn, otherBtn);
  }

  const fieldsEl = document.createElement('div');
  fieldsEl.className = 'time-toggle-fields';

  const durationFields = document.createElement('div');
  durationFields.className = 'duration-fields';

  const hoursInput = document.createElement('input');
  hoursInput.type = 'number';
  hoursInput.min = '0';
  hoursInput.step = '1';
  hoursInput.value = '0';
  hoursInput.className = 'text-input duration-input';
  hoursInput.setAttribute('aria-label', 'Hours');

  const hoursSuffix = document.createElement('span');
  hoursSuffix.className = 'input-suffix';
  hoursSuffix.textContent = 'h';

  const minutesInput = document.createElement('input');
  minutesInput.type = 'number';
  minutesInput.min = '0';
  minutesInput.max = '59';
  minutesInput.step = '1';
  minutesInput.value = '0';
  minutesInput.className = 'text-input duration-input';
  minutesInput.setAttribute('aria-label', 'Minutes');

  const minutesSuffix = document.createElement('span');
  minutesSuffix.className = 'input-suffix';
  minutesSuffix.textContent = 'm';

  durationFields.append(hoursInput, hoursSuffix, minutesInput, minutesSuffix);

  const otherFields = document.createElement('div');
  otherFields.className = 'other-fields hidden';
  fieldsEl.append(durationFields, otherFields);

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
  // isDurationOnly never populates modesEl with buttons (see above) — skip
  // mounting it too, otherwise its empty `.segmented` background/padding
  // still renders as a blank grey box with nothing inside it. Add the
  // equivalent spacing back as plain padding (see .time-toggle--duration-only
  // in styles.css) so the fields don't end up sitting flush against whatever
  // is above this toggle in the caller's own layout (e.g. the stop
  // popover's outer Duration-only/Start & end time buttons).
  if (!isDurationOnly) {
    container.append(modesEl);
  } else {
    container.classList.add('time-toggle--duration-only');
  }
  container.append(fieldsEl, previewEl);

  function currentReference() {
    const ref = getReferenceTime();
    return ref instanceof Date && !Number.isNaN(ref.getTime()) ? ref : null;
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

  function updatePreview() {
    const reference = currentReference();
    refreshDaySelector(reference);
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
      previewEl.textContent =
        state.mode === 'duration' ? 'Enter a duration greater than zero.' : 'Enter a valid time.';
      onChange({ isValid: false });
      return;
    }

    if (state.mode === 'duration') {
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

  function setMode(mode) {
    state.mode = mode;
    durationBtn.classList.toggle('is-active', mode === 'duration');
    otherBtn.classList.toggle('is-active', mode !== 'duration');
    durationBtn.setAttribute('aria-selected', String(mode === 'duration'));
    otherBtn.setAttribute('aria-selected', String(mode !== 'duration'));
    durationFields.classList.toggle('hidden', mode !== 'duration');
    otherFields.classList.toggle('hidden', mode === 'duration');

    if (mode === otherKey && endDateTimeField) {
      const reference = currentReference();
      if (reference) {
        endDateTimeField.prefillDateIfEmpty(reference);
      }
    }

    updatePreview();
  }

  if (!isDurationOnly) {
    durationBtn.addEventListener('click', () => setMode('duration'));
    otherBtn.addEventListener('click', () => setMode(otherKey));
  }
  [hoursInput, minutesInput].forEach((el) => el.addEventListener('input', updatePreview));

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
