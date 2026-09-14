import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import './testUtils/domSetup.js';
import { buildDaySlots, createDateTimeField, createDaySelector, createTimeField, nearestSlotIndex } from '../../src/gpx2fit/gui/js/dateTimeField.js';

function setValueAndDispatchInput(input, value) {
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('buildDaySlots', () => {
  it('returns every half-hour of the day in order', () => {
    const slots = buildDaySlots();
    assert.equal(slots.length, 48);
    assert.deepEqual(slots[0], { hours: 0, minutes: 0 });
    assert.deepEqual(slots[1], { hours: 0, minutes: 30 });
    assert.deepEqual(slots[21], { hours: 10, minutes: 30 });
    assert.deepEqual(slots[47], { hours: 23, minutes: 30 });
  });
});

describe('nearestSlotIndex', () => {
  it('finds the exact slot for a time already on a half-hour boundary', () => {
    assert.equal(nearestSlotIndex(new Date(2024, 0, 1, 10, 30)), 21);
  });

  it('rounds up to the next slot when closer to it', () => {
    assert.equal(nearestSlotIndex(new Date(2024, 0, 1, 10, 46)), 22); // rounds to 11:00
  });

  it('wraps around to slot 0 near midnight', () => {
    assert.equal(nearestSlotIndex(new Date(2024, 0, 1, 23, 50)), 0);
  });
});

describe('createTimeField', () => {
  function mount(opts = {}) {
    const container = document.createElement('div');
    document.body.append(container);
    const values = [];
    createTimeField({ container, quickPicks: false, onChange: (v) => values.push(v), ...opts });
    const hoursInput = container.querySelector('input[aria-label="Time hours"]');
    const minutesInput = container.querySelector('input[aria-label="Time minutes"]');
    return { container, hoursInput, minutesInput, values };
  }

  it('auto-completes a single hour digit >= 3 and advances focus to minutes', () => {
    const { hoursInput, minutesInput } = mount();
    setValueAndDispatchInput(hoursInput, '5');
    assert.equal(hoursInput.value, '05');
    assert.equal(document.activeElement, minutesInput);
  });

  it('waits for a second digit when the first hour digit is < 3', () => {
    const { hoursInput } = mount();
    hoursInput.focus();
    setValueAndDispatchInput(hoursInput, '2');
    assert.equal(hoursInput.value, '2');
    assert.equal(document.activeElement, hoursInput);
  });

  it('clamps a two-digit hour above 23', () => {
    const { hoursInput } = mount();
    setValueAndDispatchInput(hoursInput, '99');
    assert.equal(hoursInput.value, '23');
  });

  it('applies the same >=6 single-digit heuristic to minutes', () => {
    const { minutesInput } = mount();
    setValueAndDispatchInput(minutesInput, '7');
    assert.equal(minutesInput.value, '07');
  });

  it('clamps a two-digit minute above 59', () => {
    const { minutesInput } = mount();
    setValueAndDispatchInput(minutesInput, '75');
    assert.equal(minutesInput.value, '59');
  });

  it('moves focus back to hours on Backspace from an empty minutes box', () => {
    const { hoursInput, minutesInput } = mount();
    minutesInput.focus();
    minutesInput.value = '';
    minutesInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true }));
    assert.equal(document.activeElement, hoursInput);
  });

  it('reports null while incomplete and the pair once both boxes are filled', () => {
    const { hoursInput, minutesInput, values } = mount();
    setValueAndDispatchInput(hoursInput, '09');
    assert.equal(values.at(-1), null);
    setValueAndDispatchInput(minutesInput, '30');
    assert.deepEqual(values.at(-1), { hours: 9, minutes: 30 });
  });

  it('setValue reports the full pair immediately', () => {
    const container = document.createElement('div');
    const values = [];
    const field = createTimeField({ container, quickPicks: false, onChange: (v) => values.push(v) });
    field.setValue(14, 5);
    assert.deepEqual(values.at(-1), { hours: 14, minutes: 5 });
  });
});

describe('createDaySelector', () => {
  function mount() {
    const container = document.createElement('div');
    document.body.append(container);
    const selected = [];
    const selector = createDaySelector({ container, onChange: (i) => selected.push(i) });
    return { container, selector, selected };
  }

  function clickDayOption(container, index) {
    container.querySelector('.day-selector-button').click();
    container.querySelectorAll('.day-selector-option')[index].click();
  }

  it('hides the selector when the activity fits in a single day', () => {
    const { container, selector } = mount();
    selector.setDayCount(1, new Date(2024, 0, 1));
    assert.equal(container.querySelector('.day-selector').classList.contains('hidden'), true);
  });

  it('shows the selector and honors a desiredIndex override', () => {
    const { container, selector } = mount();
    selector.setDayCount(3, new Date(2024, 0, 1), 2);
    assert.equal(container.querySelector('.day-selector').classList.contains('hidden'), false);
    assert.equal(selector.getDayIndex(), 2);
  });

  it('clamps to day 0 when the previously-selected day no longer exists', () => {
    const { container, selector } = mount();
    selector.setDayCount(3, new Date(2024, 0, 1));
    clickDayOption(container, 2);
    assert.equal(selector.getDayIndex(), 2);

    // The activity shrinks to 2 days; the previously-picked day 3 is gone.
    selector.setDayCount(2, new Date(2024, 0, 1));
    assert.equal(selector.getDayIndex(), 0);
  });
});

describe('createDateTimeField', () => {
  it('round-trips a Date through setValue/getValue', () => {
    const container = document.createElement('div');
    document.body.append(container);
    const field = createDateTimeField({ container, onChange: () => {} });
    const date = new Date(2024, 5, 15, 9, 30);
    field.setValue(date);
    assert.equal(field.getValue().getTime(), date.getTime());
  });

  it('prefillDateIfEmpty fills an empty date but never overwrites an existing one', () => {
    const container = document.createElement('div');
    document.body.append(container);
    const field = createDateTimeField({ container, onChange: () => {} });
    field.prefillDateIfEmpty(new Date(2024, 5, 1));
    const dateInput = container.querySelector('input[type="date"]');
    assert.equal(dateInput.value, '2024-06-01');

    field.prefillDateIfEmpty(new Date(2024, 6, 20));
    assert.equal(dateInput.value, '2024-06-01');
  });

  it('getValue returns null until both a date and a time are set', () => {
    const container = document.createElement('div');
    document.body.append(container);
    const field = createDateTimeField({ container, onChange: () => {} });
    assert.equal(field.getValue(), null);
  });
});
