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

/**
 * A typeable "HH" / "MM" pair of boxes, optionally with a scrollable
 * dropdown of every half-hour of the day as a click-to-fill shortcut.
 *
 * Each box only ever holds a value valid for its own range (0-23 / 0-59):
 * a digit that can't start a valid two-digit value in that range (3-9 for
 * hours, 6-9 for minutes) is treated as already complete rather than making
 * the user type a second digit, and a completed hour auto-advances focus to
 * the minutes box. This is what lets entry work without a literal ":"
 * keystroke, without the ambiguity a single free-typed "HH:MM" field would
 * have to guess around.
 *
 * @param {object} opts
 * @param {HTMLElement} opts.container
 * @param {string} [opts.ariaLabel]
 * @param {boolean} [opts.quickPicks] - show the half-hour dropdown on focus
 *   (default true). Turn this off where the user is expected to enter a
 *   precise time rather than pick a round one (e.g. anchor arrival times).
 * @param {(value: {hours: number, minutes: number}|null) => void} opts.onChange -
 *   called with the value once both boxes hold one, or null while either
 *   box is incomplete (including empty)
 * @returns {{ setValue: (hours: number, minutes: number) => void }}
 */
export function createTimeField({ container, ariaLabel = 'Time', quickPicks = true, onChange }) {
  const wrapper = document.createElement('div');
  wrapper.className = 'time-field';

  const boxes = document.createElement('div');
  boxes.className = 'time-field-boxes';

  const hoursInput = document.createElement('input');
  hoursInput.type = 'text';
  hoursInput.inputMode = 'numeric';
  hoursInput.maxLength = 2;
  hoursInput.placeholder = 'HH';
  hoursInput.className = 'text-input time-field-box';
  hoursInput.setAttribute('aria-label', `${ariaLabel} hours`);

  const colon = document.createElement('span');
  colon.className = 'time-field-colon';
  colon.textContent = ':';
  colon.setAttribute('aria-hidden', 'true');

  const minutesInput = document.createElement('input');
  minutesInput.type = 'text';
  minutesInput.inputMode = 'numeric';
  minutesInput.maxLength = 2;
  minutesInput.placeholder = 'MM';
  minutesInput.className = 'text-input time-field-box';
  minutesInput.setAttribute('aria-label', `${ariaLabel} minutes`);

  boxes.append(hoursInput, colon, minutesInput);
  wrapper.append(boxes);
  container.append(wrapper);

  function pairValue() {
    if (!/^\d{2}$/.test(hoursInput.value) || !/^\d{2}$/.test(minutesInput.value)) {
      return null;
    }
    return { hours: Number(hoursInput.value), minutes: Number(minutesInput.value) };
  }

  function notifyChange() {
    onChange(pairValue());
  }

  hoursInput.addEventListener('input', () => {
    const digits = hoursInput.value.replace(/\D/g, '').slice(0, 2);
    if (digits.length === 2) {
      hoursInput.value = pad(Math.min(Number(digits), 23));
      minutesInput.focus();
      minutesInput.select();
    } else if (digits.length === 1 && Number(digits) >= 3) {
      // No valid two-digit hour starts with 3-9 (30-99 is out of range), so
      // a single such digit is already an unambiguous, complete hour.
      hoursInput.value = pad(Number(digits));
      minutesInput.focus();
      minutesInput.select();
    } else {
      hoursInput.value = digits;
    }
    notifyChange();
  });

  minutesInput.addEventListener('input', () => {
    const digits = minutesInput.value.replace(/\D/g, '').slice(0, 2);
    if (digits.length === 2) {
      minutesInput.value = pad(Math.min(Number(digits), 59));
    } else if (digits.length === 1 && Number(digits) >= 6) {
      // No valid two-digit minute starts with 6-9 (60-99 is out of range).
      minutesInput.value = pad(Number(digits));
    } else {
      minutesInput.value = digits;
    }
    notifyChange();
  });

  minutesInput.addEventListener('keydown', (event) => {
    if (event.key === 'Backspace' && minutesInput.value === '') {
      hoursInput.focus();
      hoursInput.select();
    }
  });

  if (quickPicks) {
    const dropdown = document.createElement('div');
    dropdown.className = 'time-field-dropdown hidden';
    wrapper.append(dropdown);

    // Tracks whichever box last received focus, so a quick-pick click can
    // restore focus to it without moving focus to the *other* box — moving
    // focus would fire that box's own 'focus' listener and reopen the
    // dropdown right after this closes it.
    let lastFocused = hoursInput;

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
        // 'mousedown' — otherwise the boxes would blur (closing this
        // dropdown) before the click ever registers.
        option.addEventListener('mousedown', (event) => event.preventDefault());
        option.addEventListener('click', () => {
          hoursInput.value = pad(hours);
          minutesInput.value = pad(minutes);
          notifyChange();
          closeDropdown();
          lastFocused.focus();
        });
        dropdown.append(option);
      });
      dropdown.classList.remove('hidden');

      const nearestOption = dropdown.children[nearestSlotIndex(new Date())];
      if (nearestOption) {
        dropdown.scrollTop = nearestOption.offsetTop;
      }
    }

    [hoursInput, minutesInput].forEach((input) => {
      input.addEventListener('focus', () => {
        lastFocused = input;
        openDropdown();
      });
      // Focus moves between the two boxes as part of normal typing (e.g.
      // auto-advance after the hour completes) — only actually close once
      // focus has left the field entirely, not on every inter-box hop.
      input.addEventListener('blur', () => {
        window.setTimeout(() => {
          if (!wrapper.contains(document.activeElement)) {
            closeDropdown();
          }
        }, 0);
      });
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          input.blur();
        }
      });
    });
  }

  return {
    setValue(hours, minutes) {
      hoursInput.value = pad(hours);
      minutesInput.value = pad(minutes);
      onChange({ hours, minutes });
    },
  };
}

/**
 * A thin single-button day selector: shows "Day N · <date>" for the
 * currently selected day and opens a dropdown of every day the activity
 * spans on click — no typing, no calendar widget, just the valid choices.
 * Used only above a 'timeOfDay' field on multi-day activities (see
 * timeInput.js); the caller hides it entirely via `setDayCount(1, ...)`
 * when the activity fits in a single day, so single-day activities see no
 * change at all.
 *
 * @param {object} opts
 * @param {HTMLElement} opts.container
 * @param {(dayIndex: number) => void} opts.onChange - fired only when the
 *   user actively picks a different day from the dropdown (0-based offset
 *   from the activity's first day)
 * @returns {{ setDayCount: (count: number, referenceDate: Date|null, desiredIndex?: number) => void, getDayIndex: () => number }}
 */
export function createDaySelector({ container, onChange }) {
  const wrapper = document.createElement('div');
  wrapper.className = 'day-selector hidden';

  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'day-selector-button';
  wrapper.append(button);

  const dropdown = document.createElement('div');
  dropdown.className = 'day-selector-dropdown hidden';
  wrapper.append(dropdown);

  container.append(wrapper);

  let dayIndex = 0;
  let dayCount = 1;
  let reference = null;

  function dayDate(offset) {
    const date = new Date(reference);
    date.setDate(date.getDate() + offset);
    return date;
  }

  function dayLabel(offset) {
    const dateStr = dayDate(offset).toLocaleDateString(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
    });
    return `Day ${offset + 1} · ${dateStr}`;
  }

  function renderButton() {
    button.textContent = reference ? dayLabel(dayIndex) : 'Day 1';
  }

  function closeDropdown() {
    dropdown.classList.add('hidden');
  }

  function openDropdown() {
    dropdown.innerHTML = '';
    for (let i = 0; i < dayCount; i++) {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'day-selector-option';
      option.textContent = dayLabel(i);
      // Same reasoning as time-field-option: block focus-stealing on
      // mousedown so the button's own blur handler doesn't close this
      // dropdown before the click ever registers.
      option.addEventListener('mousedown', (event) => event.preventDefault());
      option.addEventListener('click', () => {
        dayIndex = i;
        renderButton();
        closeDropdown();
        onChange(dayIndex);
      });
      dropdown.append(option);
    }
    dropdown.classList.remove('hidden');
  }

  button.addEventListener('click', () => {
    if (dropdown.classList.contains('hidden')) {
      openDropdown();
    } else {
      closeDropdown();
    }
  });
  button.addEventListener('blur', () => {
    window.setTimeout(() => {
      if (!wrapper.contains(document.activeElement)) {
        closeDropdown();
      }
    }, 0);
  });

  return {
    // Re-derives which days are selectable. Clamps silently (no onChange
    // call) if the previously-selected day no longer exists — this only
    // happens if the reference/duration changes while the popover is open,
    // which the caller re-derives on every keystroke anyway.
    //
    // `desiredIndex`, when given, forces the selection to that day (clamped
    // into range) instead of preserving whatever was selected before — used
    // once, by createTimeToggle, to open a popover on the day an estimated
    // time actually falls on rather than always day 1.
    setDayCount(count, referenceDate, desiredIndex) {
      dayCount = Math.max(1, count);
      reference = referenceDate;
      if (typeof desiredIndex === 'number') {
        dayIndex = Math.min(Math.max(desiredIndex, 0), dayCount - 1);
      } else if (dayIndex >= dayCount) {
        dayIndex = 0;
      }
      wrapper.classList.toggle('hidden', dayCount <= 1);
      if (dayCount <= 1) {
        closeDropdown();
      }
      renderButton();
    },
    getDayIndex: () => dayIndex,
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
