// The sidebar's device button and the popup it opens. The popup is a native
// <dialog> opened with showModal(), which puts it in the top layer (above
// Leaflet's panes without a z-index race) and gives Esc-to-close, a focus
// trap and a ::backdrop for free. What to offer and how a choice is
// validated or stored lives in devices.js; this module is only the DOM.

import {
  DEVICE_CATALOG,
  MAX_UINT16,
  MAX_UINT32,
  NO_DEVICE,
  fullName,
  isAutoName,
  parseCustomIds,
  parseStoredSelection,
  selectionLabel,
  toConvertDevice,
} from './devices.js';
import { wireNumericBox } from '../inputs/numericInput.js';
import { readSetting, writeSetting } from '../state/storage.js';

// Remembered across visits like the other sidebar settings: most people
// convert every route for the same watch.
const STORAGE_KEY = 'device';

// Matches .device-grid's `grid-template-columns` in css/dialogs.css.
const GRID_COLUMNS = 2;

const NAVIGATION_KEYS = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End']);

/**
 * Where an arrow key moves focus among the options, laid out as rows of
 * `rowLengths[i]` options each (all rows read left to right, one after
 * another). Left/Right step in reading order, Up/Down keep the column (or
 * take the row's last option, if it is shorter), Home/End jump to the ends.
 * Every move stops at the edges rather than wrapping.
 *
 * @param {number[]} rowLengths - options per row, each at least 1
 * @param {number} index - the focused option's index in reading order
 * @param {string} key - the KeyboardEvent key
 * @returns {number} the option to focus (`index` itself when nothing moves)
 */
export function moveFocusIndex(rowLengths, index, key) {
  const total = rowLengths.reduce((sum, length) => sum + length, 0);
  if (key === 'Home') {
    return 0;
  }
  if (key === 'End') {
    return total - 1;
  }
  if (key === 'ArrowLeft') {
    return Math.max(index - 1, 0);
  }
  if (key === 'ArrowRight') {
    return Math.min(index + 1, total - 1);
  }
  if (key !== 'ArrowUp' && key !== 'ArrowDown') {
    return index;
  }

  let row = 0;
  let rowStart = 0;
  while (index >= rowStart + rowLengths[row]) {
    rowStart += rowLengths[row];
    row += 1;
  }
  const column = index - rowStart;
  const target = key === 'ArrowUp' ? row - 1 : row + 1;
  if (target < 0 || target >= rowLengths.length) {
    return index;
  }
  const targetStart = key === 'ArrowUp' ? rowStart - rowLengths[target] : rowStart + rowLengths[row];
  return targetStart + Math.min(column, rowLengths[target] - 1);
}

/**
 * Wires the device button and dialog. Call once at startup.
 *
 * @param {object} opts
 * @param {HTMLButtonElement} opts.buttonEl - the sidebar button that opens the dialog
 * @param {HTMLDialogElement} opts.dialogEl - the dialog skeleton from index.html
 * @returns {{getConvertDevice: () => object}} `getConvertDevice()` is the
 *   `device` argument for pyodideBridge's convert()
 */
export function initDevicePicker({ buttonEl, dialogEl }) {
  const panelEl = /** @type {HTMLElement} */ (dialogEl.querySelector('.modal-panel'));
  const catalogEl = /** @type {HTMLElement} */ (dialogEl.querySelector('.device-catalog'));
  const noneButton = /** @type {HTMLButtonElement} */ (dialogEl.querySelector('[data-device-none]'));
  const customDetails = /** @type {HTMLDetailsElement} */ (dialogEl.querySelector('.device-custom'));
  const manufacturerInput = /** @type {HTMLInputElement} */ (dialogEl.querySelector('[data-custom="manufacturer"]'));
  const productInput = /** @type {HTMLInputElement} */ (dialogEl.querySelector('[data-custom="product"]'));
  const serialInput = /** @type {HTMLInputElement} */ (dialogEl.querySelector('[data-custom="serialNumber"]'));
  const customError = /** @type {HTMLElement} */ (dialogEl.querySelector('.device-custom-error'));
  const customApply = /** @type {HTMLButtonElement} */ (dialogEl.querySelector('[data-custom-apply]'));
  const nameInput = /** @type {HTMLInputElement} */ (dialogEl.querySelector('.device-name-input'));

  // Nothing stored, or nothing valid, is NO_DEVICE.
  let selection = parseStoredSelection(readSetting(STORAGE_KEY));

  function save() {
    writeSetting(STORAGE_KEY, JSON.stringify(selection));
  }

  /* ---------- rendering ---------- */

  const optionButtons = [];
  // Every pickable button in reading order, and how they fall into visual
  // rows: "No device" alone, then each brand's grid GRID_COLUMNS at a time.
  const navOptions = [noneButton];
  const rowLengths = [1];

  DEVICE_CATALOG.forEach(({ brand, devices }) => {
    const heading = document.createElement('p');
    heading.className = 'device-brand';
    heading.textContent = brand;

    const grid = document.createElement('div');
    grid.className = 'device-grid';
    devices.forEach((device) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'device-option';
      option.textContent = device.name;
      option.addEventListener('click', () => {
        const name = isAutoName(nameInput.value) ? fullName({ brand, ...device }) : nameInput.value;
        choose({ manufacturer: device.manufacturer, product: device.product, serialNumber: null, name });
      });
      optionButtons.push({ option, device });
      navOptions.push(option);
      grid.append(option);
    });
    for (let left = devices.length; left > 0; left -= GRID_COLUMNS) {
      rowLengths.push(Math.min(left, GRID_COLUMNS));
    }

    catalogEl.append(heading, grid);
  });

  function render() {
    buttonEl.textContent = selectionLabel(selection);
    buttonEl.classList.toggle('is-empty', selection.manufacturer === null);
    noneButton.classList.toggle('is-selected', selection.manufacturer === null);
    optionButtons.forEach(({ option, device }) => {
      const isSelected = device.manufacturer === selection.manufacturer && device.product === selection.product;
      option.classList.toggle('is-selected', isSelected);
      option.setAttribute('aria-pressed', String(isSelected));
    });
    nameInput.value = selection.name;
  }

  function choose(next) {
    selection = next;
    save();
    render();
    dialogEl.close();
  }

  /* ---------- opening and closing ---------- */

  buttonEl.addEventListener('click', () => {
    customError.hidden = true;
    const isCustom = selection.manufacturer !== null && !optionButtons.some(
      ({ device }) => device.manufacturer === selection.manufacturer && device.product === selection.product,
    );
    // The form shows the current custom IDs, so tweaking one is one edit.
    manufacturerInput.value = isCustom ? String(selection.manufacturer) : '';
    productInput.value = isCustom ? String(selection.product) : '';
    serialInput.value = isCustom && selection.serialNumber !== null ? String(selection.serialNumber) : '';
    customDetails.open = isCustom;
    dialogEl.showModal();
    // showModal would focus the close button. The panel (tabindex -1) takes
    // focus instead, so nothing is highlighted until the user presses Tab.
    panelEl.focus({ preventScroll: true });
  });

  /* ---------- keyboard ---------- */

  dialogEl.addEventListener('keydown', (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) {
      return;
    }
    // The first Tab after opening lands on the current pick (by default
    // "No device", the first option), so the arrows have a place to start.
    if (event.key === 'Tab' && !event.shiftKey && document.activeElement === panelEl) {
      event.preventDefault();
      (navOptions.find((option) => option.classList.contains('is-selected')) ?? noneButton).focus();
      return;
    }
    // Only among the options, so arrows still move the caret in the boxes.
    const index = navOptions.indexOf(/** @type {HTMLButtonElement} */ (document.activeElement));
    if (index === -1 || !NAVIGATION_KEYS.has(event.key)) {
      return;
    }
    event.preventDefault();
    navOptions[moveFocusIndex(rowLengths, index, event.key)].focus();
  });

  dialogEl.querySelector('[data-device-close]').addEventListener('click', () => dialogEl.close());

  // A click on the dialog element itself (not on anything inside its panel)
  // can only be a click on the backdrop, since the panel fills the box.
  dialogEl.addEventListener('click', (event) => {
    if (event.target === dialogEl) {
      dialogEl.close();
    }
  });

  // Back to the button that opened it, which showModal's own focus return
  // doesn't do reliably in every browser.
  dialogEl.addEventListener('close', () => buttonEl.focus());

  noneButton.addEventListener('click', () => {
    // The name stays: it's separate from the IDs, and it's what was typed.
    choose({ ...NO_DEVICE, name: isAutoName(nameInput.value) ? '' : nameInput.value });
  });

  /* ---------- custom IDs ---------- */

  // The FIT field's top value means "invalid", so the last usable ID is one below.
  wireNumericBox(manufacturerInput, { max: MAX_UINT16 - 1 });
  wireNumericBox(productInput, { max: MAX_UINT16 - 1 });
  wireNumericBox(serialInput, { max: MAX_UINT32 - 1 });

  function applyCustom() {
    const parsed = parseCustomIds({
      manufacturer: manufacturerInput.value,
      product: productInput.value,
      serialNumber: serialInput.value,
    });
    // `'value' in` narrows without strictNullChecks, which `!parsed.ok` doesn't.
    if (!('value' in parsed)) {
      customError.textContent = parsed.error;
      customError.hidden = false;
      return;
    }
    // A preset's filled-in name would now mislabel these IDs; a typed one stays.
    choose({ ...parsed.value, name: isAutoName(nameInput.value) ? '' : nameInput.value });
  }

  customApply.addEventListener('click', applyCustom);
  [manufacturerInput, productInput, serialInput].forEach((input) => {
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        applyCustom();
      }
    });
  });

  /* ---------- embedded name ---------- */

  // Live, not on close: the name is its own setting, not part of a pick.
  nameInput.addEventListener('input', () => {
    selection = { ...selection, name: nameInput.value };
    save();
  });

  render();

  return {
    getConvertDevice: () => toConvertDevice(selection),
  };
}
