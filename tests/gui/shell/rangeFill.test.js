import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import { centerBiasedFill, initCenterBiasedRange } from '../../../src/gpx2fit/gui/js/shell/rangeFill.js';

describe('centerBiasedFill', () => {
  it('is empty when the value sits on the origin', () => {
    assert.deepEqual(centerBiasedFill(5, 1, 10, 5), { lo: 4 / 9, hi: 4 / 9 });
  });

  it('runs from the origin up to a higher value', () => {
    assert.deepEqual(centerBiasedFill(10, 1, 10, 5), { lo: 4 / 9, hi: 1 });
  });

  it('runs from a lower value up to the origin', () => {
    assert.deepEqual(centerBiasedFill(1, 1, 10, 5), { lo: 0, hi: 4 / 9 });
  });

  it('clamps values outside the range', () => {
    assert.deepEqual(centerBiasedFill(42, 1, 10, 5), { lo: 4 / 9, hi: 1 });
    assert.deepEqual(centerBiasedFill(-3, 1, 10, 5), { lo: 0, hi: 4 / 9 });
  });

  it('is empty for a range with no span', () => {
    assert.deepEqual(centerBiasedFill(3, 3, 3, 3), { lo: 0, hi: 0 });
  });
});

describe('initCenterBiasedRange', () => {
  function slider(value) {
    const input = document.createElement('input');
    input.type = 'range';
    input.min = '1';
    input.max = '10';
    input.value = String(value);
    return input;
  }

  it('sets the fill for the current value at once', () => {
    const input = slider(8);
    initCenterBiasedRange(input, 5);
    assert.equal(Number(input.style.getPropertyValue('--range-lo')), 4 / 9);
    assert.equal(Number(input.style.getPropertyValue('--range-hi')), 7 / 9);
  });

  it('follows the value on input', () => {
    const input = slider(5);
    initCenterBiasedRange(input, 5);
    input.value = '2';
    input.dispatchEvent(new Event('input'));
    assert.equal(Number(input.style.getPropertyValue('--range-lo')), 1 / 9);
    assert.equal(Number(input.style.getPropertyValue('--range-hi')), 4 / 9);
  });
});
