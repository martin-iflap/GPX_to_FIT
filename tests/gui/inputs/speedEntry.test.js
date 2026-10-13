import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import { createSpeedEntry } from '../../../src/gpx2fit/gui/js/inputs/speedEntry.js';

function type(input, value) {
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function mount({ sport = 'hiking' } = {}) {
  const state = { sport };
  const inputs = [];
  const entry = createSpeedEntry({ getSport: () => state.sport, onInput: () => inputs.push(entry.getSpeedMps()) });
  document.body.append(entry.el);
  const box = (label) => entry.el.querySelector(`input[aria-label="${label}"]`);
  const isShown = (selector) => !entry.el.querySelector(selector).classList.contains('hidden');
  return { entry, state, inputs, box, isShown };
}

describe('createSpeedEntry', () => {
  it('reads km/h as metres per second', () => {
    const { entry, box } = mount({ sport: 'hiking' });
    type(box('Average speed'), '5.4');

    assert.equal(entry.getUnit(), 'speed');
    assert.equal(entry.getSpeedMps(), 1.5);
  });

  it('reads a pace as metres per second', () => {
    const { entry, box } = mount({ sport: 'running' });
    type(box('Pace minutes'), '4');
    type(box('Pace seconds'), '10');

    assert.equal(entry.getUnit(), 'pace');
    // 4:10 /km is 250 s per km.
    assert.equal(entry.getSpeedMps(), 4);
  });

  it('has no speed while blank or zero', () => {
    const { entry, box } = mount();
    type(box('Average speed'), '');
    assert.equal(entry.getSpeedMps(), null);
    type(box('Average speed'), '0');
    assert.equal(entry.getSpeedMps(), null);
  });

  it('reports every edit, with the speed already readable', () => {
    const { inputs, box } = mount();
    type(box('Average speed'), '3.6');

    assert.deepEqual(inputs, [1]);
  });

  it('follows the sport on refresh, until the user picks a unit', () => {
    const { entry, state, isShown } = mount({ sport: 'hiking' });
    assert.equal(isShown('.speed-fields-kmh'), true);

    state.sport = 'running';
    entry.refresh();
    assert.equal(entry.getUnit(), 'pace');
    assert.equal(isShown('.speed-fields-pace'), true);

    entry.el.querySelector('.speed-unit-button').click(); // to km/h
    state.sport = 'running';
    entry.refresh();
    assert.equal(entry.getUnit(), 'speed');
  });

  it('carries the speed across a unit switch, and reports it', () => {
    const { entry, inputs, box } = mount({ sport: 'hiking' });
    type(box('Average speed'), '12');
    inputs.splice(0);

    entry.el.querySelector('.speed-unit-button').click();

    assert.equal(box('Pace minutes').value, '5');
    assert.equal(box('Pace seconds').value, '00');
    assert.equal(inputs.length, 1);
    assert.ok(Math.abs(inputs[0] - 12 / 3.6) < 1e-9);
  });

  it('fills the boxes from a quick-pick, and reports it', () => {
    const { entry, inputs, box } = mount({ sport: 'hiking' });
    box('Average speed').focus();
    const option = [...entry.el.querySelectorAll('.dropdown-option')].find((o) => o.textContent === '4.5');
    option.click();

    assert.equal(box('Average speed').value, '4.5');
    assert.deepEqual(inputs, [4.5 / 3.6]);
  });

  it('closes the quick-picks on request', () => {
    const { entry, box } = mount();
    box('Average speed').focus();
    entry.closePresets();

    assert.equal(entry.el.querySelector('.speed-preset-dropdown').classList.contains('hidden'), true);
  });

  it('focuses and selects the first box of the unit in use', () => {
    const { entry, box } = mount({ sport: 'running' });
    type(box('Pace minutes'), '5');
    entry.focus();

    assert.equal(document.activeElement, box('Pace minutes'));
    assert.equal(box('Pace minutes').selectionStart, 0);
    assert.equal(box('Pace minutes').selectionEnd, 1);
  });
});
