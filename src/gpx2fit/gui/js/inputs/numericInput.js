// The app's number boxes: the duration and speed boxes in timeInput.js and
// the device picker's custom IDs. They are text inputs with
// `inputMode="numeric"` rather than `type="number"`, because number inputs
// refuse to report a caret position (`selectionStart` throws). And without
// one the edge-triggered left/right navigation in dateTimeField.js's
// `linkSegmentPair` can't tell an arrow at the end of the hours box from an
// arrow in the middle of it. `wireNumericBox` puts back the two things
// `type="number"` was used for. The only thing lost is the native spinner,
// which also leaves the pair looking like the HH:MM boxes beside it.

/**
 * Strips everything that isn't a digit or the first decimal point, so a typed
 * "4.5" survives the filter in `wireNumericBox` but "4.5.2" doesn't.
 * @param {string} value
 * @returns {string}
 */
function keepDecimalDigits(value) {
  const filtered = value.replace(/[^0-9.]/g, '');
  const firstDot = filtered.indexOf('.');
  if (firstDot === -1) {
    return filtered;
  }
  return filtered.slice(0, firstDot + 1) + filtered.slice(firstDot + 1).replace(/\./g, '');
}

/**
 * Gives a plain text input digits-only entry and Up/Down stepping.
 *
 * @param {HTMLInputElement} input
 * @param {{max?: number|null, decimal?: boolean, step?: number}} [options] -
 *   `max` is an upper bound for stepping only, as `max` on a number input was:
 *   a typed 90 minutes still resolves as 1h30m. `decimal` allows a single
 *   decimal point through the filter (the km/h box; every other box is
 *   whole-number) and rounds each step back to one decimal place, since
 *   repeated 0.1 steps otherwise drift into 4.300000000000001.
 */
export function wireNumericBox(input, { max = null, decimal = false, step = 1 } = {}) {
  const clean = (value) => (decimal ? keepDecimalDigits(value) : value.replace(/\D/g, ''));

  input.addEventListener('input', () => {
    const cleaned = clean(input.value);
    if (cleaned !== input.value) {
      input.value = cleaned;
    }
  });

  input.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') {
      return;
    }
    // Left as-is these would jump the caret to one end of the value instead.
    event.preventDefault();
    const current = Number(clean(input.value)) || 0;
    const stepped = current + (event.key === 'ArrowUp' ? step : -step);
    const clamped = Math.min(Math.max(stepped, 0), max ?? Number.MAX_SAFE_INTEGER);
    input.value = String(decimal ? Math.round(clamped * 10) / 10 : clamped);
    // A native spinner fires 'input' too, so everything downstream (the digit
    // filter above, a preview) stays on one path.
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

/**
 * Builds one `text` + `inputMode="numeric"` box, the shape of every editable
 * number timeInput.js builds. Not wired: call `wireNumericBox` on it.
 * @param {string} ariaLabel
 * @param {string} [placeholder] - the duration boxes default to "0" instead,
 *   so only the speed boxes, which start empty, carry one
 * @returns {HTMLInputElement}
 */
export function createNumericInput(ariaLabel, placeholder = '') {
  const input = document.createElement('input');
  input.type = 'text';
  input.inputMode = 'numeric';
  input.className = 'text-input duration-input';
  input.placeholder = placeholder;
  input.setAttribute('aria-label', ariaLabel);
  return input;
}
