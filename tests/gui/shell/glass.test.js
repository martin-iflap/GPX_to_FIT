import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import { initGlass, supportsBackdropRefraction } from '../../../src/gpx2fit/gui/js/shell/glass.js';

const brands = (...names) => ({ userAgentData: { brands: names.map((brand) => ({ brand, version: '140' })) } });

describe('supportsBackdropRefraction', () => {
  it('is on for Chromium-based browsers, whatever their own brand', () => {
    assert.equal(supportsBackdropRefraction(brands('Not.A/Brand', 'Chromium', 'Google Chrome')), true);
    assert.equal(supportsBackdropRefraction(brands('Chromium', 'Microsoft Edge')), true);
  });

  it('is off without userAgentData, as in Safari and Firefox', () => {
    assert.equal(supportsBackdropRefraction({}), false);
    assert.equal(supportsBackdropRefraction(undefined), false);
    assert.equal(supportsBackdropRefraction(brands('Not.A/Brand')), false);
  });
});

describe('initGlass', () => {
  it('leaves the class off under jsdom, which has no userAgentData', () => {
    const root = document.createElement('html');
    root.classList.add('glass-refraction');
    initGlass(root);
    assert.equal(root.classList.contains('glass-refraction'), false);
  });
});
