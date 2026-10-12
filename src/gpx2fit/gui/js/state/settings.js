// The two conversion settings remembered across visits: the surface lookup
// checkbox and the pace smoothness slider. Both are plain form controls in
// index.html; this restores a stored value into each and stores every change.
// The keys are listed in about.html#privacy.

import { readSetting, writeSetting } from './storage.js';
import { initCenterBiasedRange } from '../shell/rangeFill.js';

export const SURFACE_LOOKUP_STORAGE_KEY = 'surfaceLookup';
export const SMOOTHNESS_STORAGE_KEY = 'smoothness';

/**
 * The one setting that sends route data off the device (to Valhalla), so the
 * user's choice is remembered. With nothing stored it stays as the HTML has
 * it (on).
 * @param {HTMLInputElement} checkboxEl
 */
export function initSurfaceLookupSetting(checkboxEl) {
  if (readSetting(SURFACE_LOOKUP_STORAGE_KEY) === 'off') {
    checkboxEl.checked = false;
  }
  checkboxEl.addEventListener('change', () => {
    writeSetting(SURFACE_LOOKUP_STORAGE_KEY, checkboxEl.checked ? 'on' : 'off');
  });
}

/**
 * Remembered like the surface checkbox: someone who prefers a smoother (or
 * rougher) pace likely wants it for every route. The slider's own min/max
 * bound the value, so only a stored whole number inside them is restored.
 * @param {HTMLInputElement} sliderEl - an `<input type="range">`
 * @param {HTMLElement} valueEl - shows the slider's current value
 */
export function initSmoothnessSetting(sliderEl, valueEl) {
  const stored = readSetting(SMOOTHNESS_STORAGE_KEY);
  if (stored !== null) {
    const level = Number(stored);
    if (Number.isInteger(level) && level >= Number(sliderEl.min) && level <= Number(sliderEl.max)) {
      sliderEl.value = String(level);
    }
  }
  valueEl.textContent = sliderEl.value;
  // The fill grows out of the HTML default (5 = automatic), not the left end.
  initCenterBiasedRange(sliderEl, Number(sliderEl.defaultValue));

  sliderEl.addEventListener('input', () => {
    valueEl.textContent = sliderEl.value;
  });
  sliderEl.addEventListener('change', () => {
    writeSetting(SMOOTHNESS_STORAGE_KEY, sliderEl.value);
  });
}
