// Custom replacements for native <input type="time"> / <input type="datetime-local">.
// Neither their popup's tiny spinner buttons nor (for datetime-local) the
// lack of any confirm step can be styled reliably across browsers, so time
// entry gets its own typeable "HH:MM" field. Where a quick-pick list makes
// sense (Start/End time — see quickPicks below), it opens right under the
// field on focus: every half-hour of the day, scrolled so the one closest
// to right now is the first one visible. The date part stays a native
// <input type="date"> — day-picking there mostly works fine, and a full
// custom calendar felt like overkill — just with a bigger click target on
// its calendar icon (see styles.css).

import { pad } from './format.js';

const SLOT_STEP_MINUTES = 30;
const SLOTS_PER_DAY = (24 * 60) / SLOT_STEP_MINUTES;

/** Every half-hour slot of the day in order: [{hours:0,minutes:0}, {hours:0,minutes:30}, ..., {hours:23,minutes:30}]. */
function buildDaySlots() {
  return Array.from({ length: SLOTS_PER_DAY }, (_, i) => {
    const totalMinutes = i * SLOT_STEP_MINUTES;
    return { hours: Math.floor(totalMinutes / 60), minutes: totalMinutes % 60 };
  });
}

/** Index into `buildDaySlots()`'s result of the slot closest to `now` (ties/midnight wrap to slot 0). */
function nearestSlotIndex(now) {
  const totalMinutes = now.getHours() * 60 + now.getMinutes();
  return Math.round(totalMinutes / SLOT_STEP_MINUTES) % SLOTS_PER_DAY;
}

/** @returns {{hours: number, minutes: number}|null} parsed "H:MM"/"HH:MM", or null if `text` isn't one. */
function parseTimeText(text) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(text.trim());
  if (!match) {
    return null;
  }
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) {
    return null;
  }
  return { hours, minutes };
}

/**
 * A typeable "HH:MM" field, optionally with a scrollable dropdown of every
 * half-hour of the day as a click-to-fill shortcut.
 *
 * @param {object} opts
 * @param {HTMLElement} opts.container
 * @param {string} [opts.ariaLabel]
 * @param {boolean} [opts.quickPicks] - show the half-hour dropdown on focus
 *   (default true). Turn this off where the user is expected to enter a
 *   precise time rather than pick a round one (e.g. anchor arrival times).
 * @param {(value: {hours: number, minutes: number}|null) => void} opts.onChange -
 *   called with the parsed value, or null while the text field holds
 *   something unparseable (including empty)
 * @returns {{ setValue: (hours: number, minutes: number) => void }}
 */
export function createTimeField({ container, ariaLabel = 'Time', quickPicks = true, onChange }) {
  const wrapper = document.createElement('div');
  wrapper.className = 'time-field';

  const input = document.createElement('input');
  input.type = 'text';
  input.inputMode = 'numeric';
  input.placeholder = 'HH:MM';
  input.className = 'text-input time-field-input';
  input.setAttribute('aria-label', ariaLabel);

  wrapper.append(input);
  container.append(wrapper);

  input.addEventListener('input', () => onChange(parseTimeText(input.value)));

  if (quickPicks) {
    const dropdown = document.createElement('div');
    dropdown.className = 'time-field-dropdown hidden';
    wrapper.append(dropdown);

    function closeDropdown() {
      dropdown.classList.add('hidden');
    }

    function openDropdown() {
      dropdown.innerHTML = '';
      buildDaySlots().forEach(({ hours, minutes }) => {
        const label = `${pad(hours)}:${pad(minutes)}`;
        const option = document.createElement('button');
        option.type = 'button';
        option.className = 'time-field-option';
        option.textContent = label;
        // Selection fires on 'click', but focus-stealing is blocked on
        // 'mousedown' — otherwise the input would blur (closing this
        // dropdown) before the click ever registers.
        option.addEventListener('mousedown', (event) => event.preventDefault());
        option.addEventListener('click', () => {
          input.value = label;
          onChange(parseTimeText(label));
          closeDropdown();
          input.focus();
        });
        dropdown.append(option);
      });
      dropdown.classList.remove('hidden');

      const nearestOption = dropdown.children[nearestSlotIndex(new Date())];
      if (nearestOption) {
        dropdown.scrollTop = nearestOption.offsetTop;
      }
    }

    input.addEventListener('focus', openDropdown);
    input.addEventListener('blur', closeDropdown);
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        input.blur();
      }
    });
  }

  return {
    setValue(hours, minutes) {
      input.value = `${pad(hours)}:${pad(minutes)}`;
      onChange({ hours, minutes });
    },
  };
}

/**
 * Pairs a native <input type="date"> with a `createTimeField`, exposing the
 * combination as a single Date.
 *
 * @param {object} opts
 * @param {HTMLElement} opts.container
 * @param {string} [opts.dateAriaLabel]
 * @param {string} [opts.timeAriaLabel]
 * @param {(date: Date|null) => void} opts.onChange
 * @returns {{ getValue: () => Date|null, setValue: (date: Date) => void, prefillDateIfEmpty: (date: Date) => void }}
 */
export function createDateTimeField({ container, dateAriaLabel = 'Date', timeAriaLabel = 'Time', onChange }) {
  const wrapper = document.createElement('div');
  wrapper.className = 'date-time-field';

  const dateInput = document.createElement('input');
  dateInput.type = 'date';
  dateInput.className = 'text-input date-time-field-date';
  dateInput.setAttribute('aria-label', dateAriaLabel);

  const timeContainer = document.createElement('div');
  timeContainer.className = 'date-time-field-time';

  wrapper.append(dateInput, timeContainer);
  container.append(wrapper);

  let timeValue = null;

  function currentValue() {
    if (!dateInput.value || !timeValue) {
      return null;
    }
    const [year, month, day] = dateInput.value.split('-').map(Number);
    return new Date(year, month - 1, day, timeValue.hours, timeValue.minutes, 0, 0);
  }

  dateInput.addEventListener('input', () => onChange(currentValue()));

  const timeField = createTimeField({
    container: timeContainer,
    ariaLabel: timeAriaLabel,
    onChange: (value) => {
      timeValue = value;
      onChange(currentValue());
    },
  });

  return {
    getValue: currentValue,
    setValue(date) {
      dateInput.value = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
      timeField.setValue(date.getHours(), date.getMinutes());
    },
    // Sets just the date (not the time) as a starting-point default, but
    // only if the user hasn't already picked one — used to default "End
    // time" to the same day as the start without ever clobbering an edit.
    prefillDateIfEmpty(date) {
      if (!dateInput.value) {
        dateInput.value = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
        onChange(currentValue());
      }
    },
  };
}
