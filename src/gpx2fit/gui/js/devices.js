// The devices the picker offers, and the pure logic around a device choice
// (labels, custom-ID validation, storage round trip). No DOM here, so all of
// it is testable under jsdom; devicePicker.js is the dialog on top.
//
// Strava shows a device only when the FIT file_id's manufacturer + product
// pair is in its own table, and ignores the embedded product name. So only
// IDs with a real source belong in this list: the Garmin ones are copied from
// fit_tool's GarminProduct enum (the FIT SDK profile). No other brand
// publishes its product IDs, so they are added only from a real recording
// whose device Strava named, or from another FIT reader's device table built
// the same way. A guessed ID would show nothing, which is the exact problem
// this list exists to fix.

/** FIT manufacturer IDs (FIT SDK `manufacturer` type). */
export const GARMIN = 1;
export const WAHOO = 32;

/**
 * Upper bounds of the FIT file_id fields: manufacturer and product are
 * uint16, serial_number is uint32z (0 is its invalid value).
 */
export const MAX_UINT16 = 0xffff;
export const MAX_UINT32 = 0xffffffff;

/**
 * Brands in display order, each with its devices in display order.
 * @type {{brand: string, devices: {name: string, manufacturer: number, product: number}[]}[]}
 */
export const DEVICE_CATALOG = [
  {
    brand: 'Garmin',
    devices: [
      // 3076 (Forerunner 245) is confirmed to show on Strava (2026-09-21).
      // The rest come from the same enum but haven't been upload-tested.
      { name: 'Forerunner 55', manufacturer: GARMIN, product: 3869 },
      { name: 'Forerunner 165', manufacturer: GARMIN, product: 4432 },
      { name: 'Forerunner 245', manufacturer: GARMIN, product: 3076 },
      { name: 'Forerunner 255', manufacturer: GARMIN, product: 3992 },
      { name: 'Forerunner 265', manufacturer: GARMIN, product: 4257 },
      { name: 'Forerunner 945', manufacturer: GARMIN, product: 3113 },
      { name: 'Forerunner 955', manufacturer: GARMIN, product: 4024 },
      { name: 'Forerunner 965', manufacturer: GARMIN, product: 4315 },
      { name: 'fēnix 6', manufacturer: GARMIN, product: 3290 },
      { name: 'fēnix 7', manufacturer: GARMIN, product: 3906 },
      { name: 'fēnix 7 Pro', manufacturer: GARMIN, product: 4375 },
      { name: 'fēnix 8', manufacturer: GARMIN, product: 4536 },
      { name: 'epix (Gen 2)', manufacturer: GARMIN, product: 3943 },
      { name: 'Enduro 3', manufacturer: GARMIN, product: 4575 },
      { name: 'Instinct 3', manufacturer: GARMIN, product: 4586 },
      { name: 'Venu 3', manufacturer: GARMIN, product: 4260 },
      { name: 'vívoactive 5', manufacturer: GARMIN, product: 4426 },
    ],
  },
  {
    brand: 'Wahoo',
    devices: [
      // From GoldenCheetah's FITmetadata.json (its FIT reader's device-name
      // table). 31 also appears in real BOLT recordings in the tuning corpus.
      // Neither has been upload-tested on Strava yet. ROAM, BOLT v2/3 and
      // RIVAL have no public source, and the corpus's 32/43 is still unnamed.
      { name: 'ELEMNT', manufacturer: WAHOO, product: 28 },
      { name: 'ELEMNT BOLT', manufacturer: WAHOO, product: 31 },
    ],
  },
];

/**
 * A device choice. `manufacturer: null` means no device: the FIT file gets a
 * neutral "development" ID (see fit_writer.write_fit). `name` is the text
 * written as the file's product name, which Strava doesn't show; it is
 * independent of the IDs.
 * @typedef {{manufacturer: number|null, product: number|null, serialNumber: number|null, name: string}} DeviceSelection
 */

/** @type {DeviceSelection} */
export const NO_DEVICE = Object.freeze({ manufacturer: null, product: null, serialNumber: null, name: '' });

/**
 * The catalog entry with exactly these IDs, if any.
 * @param {number|null} manufacturer
 * @param {number|null} product
 * @returns {{brand: string, name: string, manufacturer: number, product: number}|null}
 */
export function findDevice(manufacturer, product) {
  for (const { brand, devices } of DEVICE_CATALOG) {
    const device = devices.find((d) => d.manufacturer === manufacturer && d.product === product);
    if (device) {
      return { brand, ...device };
    }
  }
  return null;
}

/** "Garmin Forerunner 965": the brand and model together. */
export function fullName(device) {
  return `${device.brand} ${device.name}`;
}

/**
 * What the sidebar's device button says for a selection.
 * @param {DeviceSelection} selection
 * @returns {string}
 */
export function selectionLabel(selection) {
  if (selection.manufacturer === null) {
    return 'Select a device';
  }
  const device = findDevice(selection.manufacturer, selection.product);
  if (device) {
    return fullName(device);
  }
  return `Custom device (${selection.manufacturer}/${selection.product})`;
}

/**
 * Whether `name` is empty or one the picker itself filled in, i.e. safe to
 * replace when another preset is picked. A name the user typed is kept.
 * @param {string} name
 */
export function isAutoName(name) {
  const trimmed = name.trim();
  return trimmed === '' || DEVICE_CATALOG.some(({ brand, devices }) => devices.some((d) => fullName({ brand, ...d }) === trimmed));
}

/**
 * Parses one whole-number field of the custom-ID form.
 * @returns {number|null} the value, or null if it isn't a whole number in [min, max]
 */
function parseWhole(text, min, max) {
  const trimmed = String(text).trim();
  if (!/^\d+$/.test(trimmed)) {
    return null;
  }
  const value = Number(trimmed);
  return value >= min && value <= max ? value : null;
}

/**
 * Validates the custom-ID form's three boxes.
 * @param {{manufacturer: string, product: string, serialNumber: string}} fields - raw box contents
 * @returns {{ok: true, value: {manufacturer: number, product: number, serialNumber: number|null}} | {ok: false, error: string}}
 */
export function parseCustomIds({ manufacturer, product, serialNumber }) {
  const manufacturerId = parseWhole(manufacturer, 1, MAX_UINT16 - 1);
  if (manufacturerId === null) {
    return { ok: false, error: `Manufacturer ID must be a whole number from 1 to ${MAX_UINT16 - 1}.` };
  }
  const productId = parseWhole(product, 0, MAX_UINT16 - 1);
  if (productId === null) {
    return { ok: false, error: `Product ID must be a whole number from 0 to ${MAX_UINT16 - 1}.` };
  }
  let serial = null;
  if (String(serialNumber).trim() !== '') {
    serial = parseWhole(serialNumber, 1, MAX_UINT32 - 1);
    if (serial === null) {
      return { ok: false, error: `Serial number must be a whole number from 1 to ${MAX_UINT32 - 1}, or left empty.` };
    }
  }
  return { ok: true, value: { manufacturer: manufacturerId, product: productId, serialNumber: serial } };
}

/**
 * Turns whatever was in storage back into a selection, falling back to
 * NO_DEVICE for anything missing, malformed or out of range. Storage is
 * per-browser and user-editable, so nothing in it is trusted.
 * @param {string|null} stored - the raw stored string
 * @returns {DeviceSelection}
 */
export function parseStoredSelection(stored) {
  let data;
  try {
    data = JSON.parse(stored ?? '');
  } catch {
    return NO_DEVICE;
  }
  if (!data || typeof data !== 'object') {
    return NO_DEVICE;
  }
  const name = typeof data.name === 'string' ? data.name.slice(0, 50) : '';
  if (data.manufacturer === null || data.manufacturer === undefined) {
    return { ...NO_DEVICE, name };
  }
  const parsed = parseCustomIds({
    manufacturer: String(data.manufacturer),
    product: String(data.product ?? ''),
    serialNumber: data.serialNumber === null || data.serialNumber === undefined ? '' : String(data.serialNumber),
  });
  return parsed.ok ? { ...parsed.value, name } : { ...NO_DEVICE, name };
}

/**
 * The `device` argument for pyodideBridge's convert().
 * @param {DeviceSelection} selection
 */
export function toConvertDevice(selection) {
  const name = selection.name.trim() || undefined;
  if (selection.manufacturer === null) {
    return { name };
  }
  return {
    name,
    manufacturer: selection.manufacturer,
    product: selection.product ?? 0,
    serialNumber: selection.serialNumber ?? undefined,
  };
}
