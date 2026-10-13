// The pace-smoothness slider's center-biased fill (css/forms.css, .range-input):
// the accent fill runs from an origin value (5 = automatic) to the knob, in
// whichever direction, rather than from the slider's left end. CSS can't read
// an input's value, so this keeps two custom properties in step with it.

/**
 * The fill's two ends as fractions of the slider's travel (0 = min, 1 = max).
 * Both values are clamped into range, so `lo === hi` (an empty fill) when the
 * value sits on the origin.
 * @param {number} value
 * @param {number} min
 * @param {number} max
 * @param {number} origin - the value the fill grows out of
 * @returns {{lo: number, hi: number}}
 */
export function centerBiasedFill(value, min, max, origin) {
  const span = max - min;
  if (!(span > 0)) {
    return { lo: 0, hi: 0 };
  }
  const fraction = (v) => Math.min(Math.max((v - min) / span, 0), 1);
  const a = fraction(origin);
  const b = fraction(value);
  return { lo: Math.min(a, b), hi: Math.max(a, b) };
}

/**
 * Sets `--range-lo` / `--range-hi` on `input` now and on every `input` event.
 * Call after any stored value has been restored into the slider.
 * @param {HTMLInputElement} input - an `<input type="range">`
 * @param {number} origin
 */
export function initCenterBiasedRange(input, origin) {
  const update = () => {
    const { lo, hi } = centerBiasedFill(Number(input.value), Number(input.min), Number(input.max), origin);
    input.style.setProperty('--range-lo', String(lo));
    input.style.setProperty('--range-hi', String(hi));
  };
  input.addEventListener('input', update);
  update();
}
