// The sidebar's device button and the popup it opens. The popup is a native
// <dialog> opened with showModal(), which puts it in the top layer (above
// Leaflet's panes without a z-index race) and gives Esc-to-close, a focus
// trap and a ::backdrop for free. What to offer and how a choice is
// validated or stored lives in devices.js; this module is only the DOM.

import {
  DEVICE_CATALOG,
  NO_DEVICE,
  fullName,
  isAutoName,
  parseCustomIds,
  parseStoredSelection,
  selectionLabel,
  toConvertDevice,
} from './devices.js';
import { wireNumericBox } from './timeInput.js';

// Remembered across visits like the other sidebar settings: most people
// convert every route for the same watch.
const STORAGE_KEY = 'device';

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
  const catalogEl = dialogEl.querySelector('.device-catalog');
  const noneButton = dialogEl.querySelector('[data-device-none]');
  const customDetails = dialogEl.querySelector('.device-custom');
  const manufacturerInput = dialogEl.querySelector('[data-custom="manufacturer"]');
  const productInput = dialogEl.querySelector('[data-custom="product"]');
  const serialInput = dialogEl.querySelector('[data-custom="serialNumber"]');
  const customError = dialogEl.querySelector('.device-custom-error');
  const customApply = dialogEl.querySelector('[data-custom-apply]');
  const nameInput = dialogEl.querySelector('.device-name-input');

  let selection = NO_DEVICE;
  try {
    selection = parseStoredSelection(localStorage.getItem(STORAGE_KEY));
  } catch {
    // Storage blocked (private window, cleared site data): start with none.
  }

  function save() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(selection));
    } catch {
      // Not remembered this time; the choice still applies to this visit.
    }
  }

  /* ---------- rendering ---------- */

  const optionButtons = [];

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
      grid.append(option);
    });

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

  wireNumericBox(manufacturerInput, { max: 65534 });
  wireNumericBox(productInput, { max: 65534 });
  wireNumericBox(serialInput, { max: 4294967294 });

  function applyCustom() {
    const parsed = parseCustomIds({
      manufacturer: manufacturerInput.value,
      product: productInput.value,
      serialNumber: serialInput.value,
    });
    if (!parsed.ok) {
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
