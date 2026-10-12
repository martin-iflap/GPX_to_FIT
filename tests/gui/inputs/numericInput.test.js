import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import { createNumericInput, wireNumericBox } from '../../../src/gpx2fit/gui/js/inputs/numericInput.js';

function box(options) {
  const input = createNumericInput('Test');
  wireNumericBox(input, options);
  return input;
}

function type(input, value) {
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function press(input, key) {
  const event = new KeyboardEvent('keydown', { key, cancelable: true });
  input.dispatchEvent(event);
  return event;
}

describe('createNumericInput', () => {
  it('is a labelled text box with a numeric keyboard, not a number input', () => {
    const input = createNumericInput('Hours', '0');
    assert.equal(input.type, 'text');
    assert.equal(input.inputMode, 'numeric');
    assert.equal(input.getAttribute('aria-label'), 'Hours');
    assert.equal(input.placeholder, '0');
  });
});

describe('wireNumericBox', () => {
  it('keeps only digits in a whole-number box', () => {
    const input = box();
    type(input, '1a2.5');
    assert.equal(input.value, '125');
  });

  it('keeps the first decimal point only in a decimal box', () => {
    const input = box({ decimal: true });
    type(input, '4.5.2x');
    assert.equal(input.value, '4.52');
  });

  it('steps with Up and Down, and claims the key', () => {
    const input = box();
    type(input, '7');
    assert.equal(press(input, 'ArrowUp').defaultPrevented, true);
    assert.equal(input.value, '8');
    press(input, 'ArrowDown');
    press(input, 'ArrowDown');
    assert.equal(input.value, '6');
  });

  it('clamps a step at zero and at max, but not a typed value', () => {
    const input = box({ max: 59 });
    type(input, '0');
    press(input, 'ArrowDown');
    assert.equal(input.value, '0');
    type(input, '59');
    press(input, 'ArrowUp');
    assert.equal(input.value, '59');
    type(input, '90');
    assert.equal(input.value, '90');
  });

  it('steps a decimal box without float drift', () => {
    const input = box({ decimal: true, step: 0.1 });
    // 0.2 + 0.1 is 0.30000000000000004 in floating point.
    type(input, '0.2');
    press(input, 'ArrowUp');
    assert.equal(input.value, '0.3');
  });

  it('fires input after a step, so listeners see the new value', () => {
    const input = box();
    const seen = [];
    input.addEventListener('input', () => seen.push(input.value));
    press(input, 'ArrowUp');
    assert.deepEqual(seen, ['1']);
  });

  it('leaves other keys alone', () => {
    const input = box();
    assert.equal(press(input, 'ArrowLeft').defaultPrevented, false);
  });
});
