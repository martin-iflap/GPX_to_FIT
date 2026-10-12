import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import { readSetting, writeSetting } from '../../../src/gpx2fit/gui/js/state/storage.js';

describe('readSetting and writeSetting', () => {
  beforeEach(() => localStorage.clear());

  it('reads back what was written', () => {
    assert.equal(writeSetting('theme', 'dark'), true);
    assert.equal(readSetting('theme'), 'dark');
  });

  it('reads null for a setting never stored', () => {
    assert.equal(readSetting('smoothness'), null);
  });
});

// A private window or blocked site data makes localStorage itself throw.
describe('with storage blocked', () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');

  beforeEach(() => {
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      get() {
        throw new DOMException('The operation is insecure.', 'SecurityError');
      },
    });
  });

  afterEach(() => {
    Object.defineProperty(globalThis, 'localStorage', descriptor);
  });

  it('reads null instead of throwing', () => {
    assert.equal(readSetting('theme'), null);
  });

  it('reports a write as not stored instead of throwing', () => {
    assert.equal(writeSetting('theme', 'dark'), false);
  });
});
