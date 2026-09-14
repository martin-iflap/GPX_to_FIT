import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import './testUtils/domSetup.js';
import { computeDayCount, createTimeToggle } from '../../src/gpx2fit/gui/js/timeInput.js';

function setValueAndDispatchInput(input, value) {
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('computeDayCount', () => {
  const reference = new Date(2024, 0, 1, 22, 0); // Jan 1, 22:00

  it('returns 1 when the duration is unknown, zero, or negative', () => {
    assert.equal(computeDayCount(reference, null), 1);
    assert.equal(computeDayCount(reference, undefined), 1);
    assert.equal(computeDayCount(reference, 0), 1);
    assert.equal(computeDayCount(reference, -100), 1);
  });

  it('returns 1 for a duration that stays within the same calendar day', () => {
    assert.equal(computeDayCount(new Date(2024, 0, 1, 8, 0), 3600), 1);
  });

  it('returns 2 for an overnight span crossing midnight, even under 24h', () => {
    // 22:00 + 4h = 02:00 the next day.
    assert.equal(computeDayCount(reference, 4 * 3600), 2);
  });

  it('returns the correct count for a multi-day span', () => {
    // Jan 1 08:00 + 50h lands on Jan 3.
    assert.equal(computeDayCount(new Date(2024, 0, 1, 8, 0), 50 * 3600), 3);
  });
});

describe('createTimeToggle', () => {
  function mount(opts) {
    const container = document.createElement('div');
    document.body.append(container);
    const changes = [];
    const toggle = createTimeToggle({ container, onChange: (r) => changes.push(r), ...opts });
    return { container, toggle, changes };
  }

  describe('duration mode', () => {
    it('is invalid at the default zero duration', () => {
      const { toggle } = mount({
        variant: 'durationOrEnd',
        getReferenceTime: () => new Date(2024, 0, 1, 10, 0),
      });
      assert.equal(toggle.getResult().isValid, false);
    });

    it('resolves a positive duration against the reference time', () => {
      const reference = new Date(2024, 0, 1, 10, 0);
      const { container, toggle } = mount({
        variant: 'durationOrEnd',
        getReferenceTime: () => reference,
      });
      const hoursInput = container.querySelector('input[aria-label="Hours"]');
      const minutesInput = container.querySelector('input[aria-label="Minutes"]');
      setValueAndDispatchInput(hoursInput, '1');
      setValueAndDispatchInput(minutesInput, '30');

      const result = toggle.getResult();
      assert.equal(result.isValid, true);
      assert.equal(result.durationSeconds, 5400);
      assert.equal(result.resolvedDate.getTime(), reference.getTime() + 5400 * 1000);
    });
  });

  describe('durationOrEnd variant, end mode', () => {
    function switchToEndMode(container) {
      const buttons = container.querySelectorAll('.segmented-option');
      buttons[1].click(); // "End time"
    }

    it('is invalid when the end time is not after the reference', () => {
      const reference = new Date(2024, 0, 1, 10, 0);
      const { container, toggle } = mount({
        variant: 'durationOrEnd',
        getReferenceTime: () => reference,
      });
      switchToEndMode(container);

      const dateInput = container.querySelector('input[type="date"]');
      setValueAndDispatchInput(dateInput, '2024-01-01');
      const hoursInput = container.querySelector('input[aria-label="End time hours"]');
      const minutesInput = container.querySelector('input[aria-label="End time minutes"]');
      setValueAndDispatchInput(hoursInput, '09');
      setValueAndDispatchInput(minutesInput, '00');

      assert.equal(toggle.getResult().isValid, false);
    });

    it('resolves a valid end time to the correct duration', () => {
      const reference = new Date(2024, 0, 1, 10, 0);
      const { container, toggle } = mount({
        variant: 'durationOrEnd',
        getReferenceTime: () => reference,
      });
      switchToEndMode(container);

      const dateInput = container.querySelector('input[type="date"]');
      setValueAndDispatchInput(dateInput, '2024-01-01');
      const hoursInput = container.querySelector('input[aria-label="End time hours"]');
      const minutesInput = container.querySelector('input[aria-label="End time minutes"]');
      setValueAndDispatchInput(hoursInput, '12');
      setValueAndDispatchInput(minutesInput, '30');

      const result = toggle.getResult();
      assert.equal(result.isValid, true);
      assert.equal(result.durationSeconds, 2.5 * 3600);
    });
  });

  describe('durationOrTimeOfDay variant, time-of-day mode', () => {
    function switchToTimeOfDayMode(container) {
      const buttons = container.querySelectorAll('.segmented-option');
      buttons[1].click(); // "Time of day"
    }

    it('is invalid for a time-of-day earlier than the reference on the same day', () => {
      const reference = new Date(2024, 0, 1, 10, 0);
      const { container, toggle } = mount({
        variant: 'durationOrTimeOfDay',
        getReferenceTime: () => reference,
        getTotalDurationSeconds: () => null,
      });
      switchToTimeOfDayMode(container);

      const hoursInput = container.querySelector('input[aria-label="Time of day hours"]');
      const minutesInput = container.querySelector('input[aria-label="Time of day minutes"]');
      setValueAndDispatchInput(hoursInput, '08');
      setValueAndDispatchInput(minutesInput, '00');

      assert.equal(toggle.getResult().isValid, false);
    });

    it('resolves against the selected day when the activity spans multiple days', () => {
      const reference = new Date(2024, 0, 1, 10, 0);
      // 25h total duration pushes the activity into a second calendar day.
      const { container, toggle } = mount({
        variant: 'durationOrTimeOfDay',
        getReferenceTime: () => reference,
        getTotalDurationSeconds: () => 25 * 3600,
      });
      switchToTimeOfDayMode(container);

      // Pick day 2 from the day selector.
      container.querySelector('.day-selector-button').click();
      container.querySelectorAll('.day-selector-option')[1].click();

      const hoursInput = container.querySelector('input[aria-label="Time of day hours"]');
      const minutesInput = container.querySelector('input[aria-label="Time of day minutes"]');
      setValueAndDispatchInput(hoursInput, '09');
      setValueAndDispatchInput(minutesInput, '00');

      const result = toggle.getResult();
      assert.equal(result.isValid, true);
      // Jan 1 10:00 -> Jan 2 09:00 is 23 hours.
      assert.equal(result.durationSeconds, 23 * 3600);
      assert.equal(result.resolvedDate.getDate(), 2);
    });
  });
});
