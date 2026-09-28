import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  DEVICE_CATALOG,
  MAX_UINT16,
  NO_DEVICE,
  findDevice,
  isAutoName,
  parseCustomIds,
  parseStoredSelection,
  selectionLabel,
  toConvertDevice,
} from '../../src/gpx2fit/gui/js/devices.js';

describe('DEVICE_CATALOG', () => {
  const all = DEVICE_CATALOG.flatMap(({ brand, devices }) => devices.map((d) => ({ brand, ...d })));

  it('lists every manufacturer/product pair once', () => {
    const keys = all.map((d) => `${d.manufacturer}/${d.product}`);
    assert.equal(new Set(keys).size, keys.length);
  });

  it('keeps every ID inside the FIT uint16 range, excluding its invalid value', () => {
    for (const d of all) {
      assert.ok(Number.isInteger(d.manufacturer) && d.manufacturer > 0 && d.manufacturer < MAX_UINT16, d.name);
      assert.ok(Number.isInteger(d.product) && d.product >= 0 && d.product < MAX_UINT16, d.name);
    }
  });

  it('includes the Forerunner 245, the one pair confirmed on Strava', () => {
    assert.equal(findDevice(1, 3076)?.name, 'Forerunner 245');
  });

  it('includes the Wahoo ELEMNT BOLT, the ID the corpus BOLT files carry', () => {
    assert.equal(selectionLabel({ ...NO_DEVICE, manufacturer: 32, product: 31 }), 'Wahoo ELEMNT BOLT');
  });

  it('includes the Wahoo ELEMNT BOLT V2, the ID real BOLT V2 files carry', () => {
    assert.equal(selectionLabel({ ...NO_DEVICE, manufacturer: 32, product: 43 }), 'Wahoo ELEMNT BOLT V2');
  });

  it("uses Suunto's own product IDs for its older watches", () => {
    assert.equal(selectionLabel({ ...NO_DEVICE, manufacturer: 23, product: 56 }), 'Suunto 5 Peak');
    assert.equal(selectionLabel({ ...NO_DEVICE, manufacturer: 23, product: 34 }), 'Suunto 9 Baro');
  });

  it("leaves out the COROS source's product 294, which is COROS's manufacturer ID", () => {
    assert.equal(findDevice(294, 294), null);
    assert.equal(findDevice(294, 832)?.name, 'VERTIX 2');
  });
});

describe('selectionLabel', () => {
  it('prompts for a choice when there is no device', () => {
    assert.equal(selectionLabel(NO_DEVICE), 'Select a device');
  });

  it('names a catalog device with its brand', () => {
    assert.equal(selectionLabel({ ...NO_DEVICE, manufacturer: 1, product: 4315 }), 'Garmin Forerunner 965');
  });

  it('shows the IDs of a custom device', () => {
    assert.equal(selectionLabel({ ...NO_DEVICE, manufacturer: 32, product: 1164 }), 'Custom device (32/1164)');
  });
});

describe('isAutoName', () => {
  it('treats an empty name and a catalog name as replaceable', () => {
    assert.ok(isAutoName(''));
    assert.ok(isAutoName('  Garmin Forerunner 965 '));
  });

  it('keeps a name the user typed', () => {
    assert.ok(!isAutoName('My old watch'));
  });
});

describe('parseCustomIds', () => {
  it('accepts whole numbers and an empty serial', () => {
    assert.deepEqual(parseCustomIds({ manufacturer: '32', product: '43', serialNumber: '' }), {
      ok: true,
      value: { manufacturer: 32, product: 43, serialNumber: null },
    });
  });

  it('accepts a serial number', () => {
    const result = parseCustomIds({ manufacturer: '1', product: '3076', serialNumber: ' 1234 ' });
    assert.equal(result.ok && result.value.serialNumber, 1234);
  });

  it('rejects a missing, zero or out-of-range manufacturer', () => {
    for (const manufacturer of ['', '0', '65535', 'abc', '1.5']) {
      const result = parseCustomIds({ manufacturer, product: '1', serialNumber: '' });
      assert.equal(result.ok, false, manufacturer);
      assert.match(result.error, /Manufacturer/);
    }
  });

  it('rejects an out-of-range product', () => {
    assert.equal(parseCustomIds({ manufacturer: '1', product: '65535', serialNumber: '' }).ok, false);
  });

  it('rejects a serial of zero or past uint32', () => {
    for (const serialNumber of ['0', '4294967295']) {
      assert.equal(parseCustomIds({ manufacturer: '1', product: '1', serialNumber }).ok, false, serialNumber);
    }
  });
});

describe('parseStoredSelection', () => {
  it('round-trips a saved selection', () => {
    const saved = { manufacturer: 1, product: 4315, serialNumber: null, name: 'Garmin Forerunner 965' };
    assert.deepEqual(parseStoredSelection(JSON.stringify(saved)), saved);
  });

  it('falls back to no device for nothing stored or malformed JSON', () => {
    assert.deepEqual(parseStoredSelection(null), NO_DEVICE);
    assert.deepEqual(parseStoredSelection('{not json'), NO_DEVICE);
    assert.deepEqual(parseStoredSelection('42'), NO_DEVICE);
  });

  it('drops out-of-range IDs but keeps the name', () => {
    const stored = JSON.stringify({ manufacturer: 70000, product: 1, name: 'Watch' });
    assert.deepEqual(parseStoredSelection(stored), { ...NO_DEVICE, name: 'Watch' });
  });
});

describe('toConvertDevice', () => {
  it('sends only a name when there is no device', () => {
    assert.deepEqual(toConvertDevice({ ...NO_DEVICE, name: ' Watch ' }), { name: 'Watch' });
    assert.deepEqual(toConvertDevice(NO_DEVICE), { name: undefined });
  });

  it('sends the IDs, and the serial only when set', () => {
    assert.deepEqual(toConvertDevice({ manufacturer: 1, product: 4315, serialNumber: null, name: '' }), {
      name: undefined,
      manufacturer: 1,
      product: 4315,
      serialNumber: undefined,
    });
    assert.equal(toConvertDevice({ manufacturer: 32, product: 43, serialNumber: 7, name: '' }).serialNumber, 7);
  });
});
