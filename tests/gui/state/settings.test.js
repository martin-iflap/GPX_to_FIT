import assert from 'node:assert/strict';
import { beforeEach, describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import {
  initSmoothnessSetting,
  initSurfaceLookupSetting,
  SMOOTHNESS_STORAGE_KEY,
  SURFACE_LOOKUP_STORAGE_KEY,
} from '../../../src/gpx2fit/gui/js/state/settings.js';

beforeEach(() => localStorage.clear());

describe('initSurfaceLookupSetting', () => {
  function checkbox() {
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = true;
    return input;
  }

  it('stays on with nothing stored', () => {
    const input = checkbox();
    initSurfaceLookupSetting(input);
    assert.equal(input.checked, true);
  });

  it('restores a stored "off"', () => {
    localStorage.setItem(SURFACE_LOOKUP_STORAGE_KEY, 'off');
    const input = checkbox();
    initSurfaceLookupSetting(input);
    assert.equal(input.checked, false);
  });

  it('ignores anything else stored', () => {
    localStorage.setItem(SURFACE_LOOKUP_STORAGE_KEY, 'nonsense');
    const input = checkbox();
    initSurfaceLookupSetting(input);
    assert.equal(input.checked, true);
  });

  it('stores every change', () => {
    const input = checkbox();
    initSurfaceLookupSetting(input);
    input.checked = false;
    input.dispatchEvent(new Event('change'));
    assert.equal(localStorage.getItem(SURFACE_LOOKUP_STORAGE_KEY), 'off');
    input.checked = true;
    input.dispatchEvent(new Event('change'));
    assert.equal(localStorage.getItem(SURFACE_LOOKUP_STORAGE_KEY), 'on');
  });
});

describe('initSmoothnessSetting', () => {
  function slider() {
    const input = document.createElement('input');
    input.type = 'range';
    input.min = '1';
    input.max = '10';
    input.defaultValue = '5';
    const valueEl = document.createElement('span');
    return { input, valueEl };
  }

  it('shows the HTML default with nothing stored', () => {
    const { input, valueEl } = slider();
    initSmoothnessSetting(input, valueEl);
    assert.equal(input.value, '5');
    assert.equal(valueEl.textContent, '5');
  });

  it('restores a stored level inside the slider\'s range', () => {
    localStorage.setItem(SMOOTHNESS_STORAGE_KEY, '8');
    const { input, valueEl } = slider();
    initSmoothnessSetting(input, valueEl);
    assert.equal(input.value, '8');
    assert.equal(valueEl.textContent, '8');
  });

  for (const stored of ['0', '11', '4.5', 'x', '']) {
    it(`ignores a stored ${JSON.stringify(stored)}`, () => {
      localStorage.setItem(SMOOTHNESS_STORAGE_KEY, stored);
      const { input, valueEl } = slider();
      initSmoothnessSetting(input, valueEl);
      assert.equal(input.value, '5');
    });
  }

  it('shows the value while dragging and stores it once let go', () => {
    const { input, valueEl } = slider();
    initSmoothnessSetting(input, valueEl);
    input.value = '3';
    input.dispatchEvent(new Event('input'));
    assert.equal(valueEl.textContent, '3');
    assert.equal(localStorage.getItem(SMOOTHNESS_STORAGE_KEY), null);
    input.dispatchEvent(new Event('change'));
    assert.equal(localStorage.getItem(SMOOTHNESS_STORAGE_KEY), '3');
  });

  it('draws the fill from the default, not the left end', () => {
    localStorage.setItem(SMOOTHNESS_STORAGE_KEY, '8');
    const { input, valueEl } = slider();
    initSmoothnessSetting(input, valueEl);
    assert.equal(Number(input.style.getPropertyValue('--range-lo')), 4 / 9);
    assert.equal(Number(input.style.getPropertyValue('--range-hi')), 7 / 9);
  });
});
