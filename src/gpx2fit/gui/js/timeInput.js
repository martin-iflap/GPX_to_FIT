// Shared dual-mode time input: a segmented Duration/Absolute toggle, one
// editable field for whichever mode is active, and a live-computed,
// non-editable preview of the other value underneath. Used both for the
// main start-time control ("Duration" / "End time") and for each anchor
// popover ("Duration since start" / "Time of day") — same shape, different
// mode pair, selected via `variant`.

import { formatClock, formatDateTime, formatDuration } from './format.js';
import { createDateTimeField, createTimeField } from './dateTimeField.js';

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
 * @param {(result: {isValid: boolean, mode?: string, resolvedDate?: Date, durationSeconds?: number}) => void} opts.onChange -
 *   called with the freshly-resolved value whenever the user edits a field,
 *   switches mode, or the caller calls `refresh()`
 * @param {{hours: number, minutes: number}} [opts.initialTimeOfDay] - for the
 *   'durationOrTimeOfDay' variant only: pre-fills the "Time of day" field and
 *   opens on that tab instead of "Duration since start" (used to seed an
 *   anchor popover with its estimated arrival time)
 * @param {number} [opts.initialDurationSeconds] - pre-fills the Duration
 *   fields (hours/minutes) regardless of which mode ends up active, so
 *   switching tabs later still shows a sensible value instead of 0h 00m
 * @returns {{ refresh: () => void, getResult: () => object }} `refresh()`
 *   re-resolves and re-renders the preview against the current reference
 *   time (call this if `getReferenceTime()`'s value changes elsewhere);
 *   `getResult()` re-resolves without touching the DOM.
 */
export function createTimeToggle({
  container,
  variant,
  getReferenceTime,
  onChange,
  initialTimeOfDay,
  initialDurationSeconds,
}) {
  const isDurationOnly = variant === 'durationOnly';
  const state = { mode: 'duration' };
  const otherKey = variant === 'durationOrEnd' ? 'end' : 'timeOfDay';

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
      // No quick-picks here: an anchor's arrival time is meant to be precise
      // (that's the whole point of adding it), not rounded to a half-hour.
      timeOfDayField = createTimeField({
        container: otherFields,
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

  container.append(modesEl, fieldsEl, previewEl);

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
    resolvedDate.setHours(otherValue.hours, otherValue.minutes, 0, 0);
    const durationSeconds = (resolvedDate.getTime() - reference.getTime()) / 1000;
    return { isValid: durationSeconds >= 0, mode: 'timeOfDay', resolvedDate, durationSeconds };
  }

  function updatePreview() {
    const reference = currentReference();
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
  } else {
    setMode('duration');
  }

  return {
    refresh: updatePreview,
    getResult: resolve,
  };
}
