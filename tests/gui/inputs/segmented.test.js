import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import { createSegmented, wireSegmented } from '../../../src/gpx2fit/gui/js/inputs/segmented.js';

function activeValues(el) {
  return [...el.querySelectorAll('.segmented-option')]
    .filter((button) => button.classList.contains('is-active'))
    .map((button) => button.dataset.value);
}

function selectedValues(el) {
  return [...el.querySelectorAll('.segmented-option')]
    .filter((button) => button.getAttribute('aria-selected') === 'true')
    .map((button) => button.dataset.value);
}

const SPORTS = [
  { value: 'hiking', label: 'Hiking' },
  { value: 'running', label: 'Running' },
];

describe('createSegmented', () => {
  it('builds one tab per option, with only the given value active', () => {
    const control = createSegmented({ options: SPORTS, value: 'running', onSelect: () => {}, ariaLabel: 'Sport' });
    const buttons = [...control.el.querySelectorAll('.segmented-option')];
    assert.deepEqual(buttons.map((b) => b.textContent), ['Hiking', 'Running']);
    assert.deepEqual(buttons.map((b) => b.getAttribute('role')), ['tab', 'tab']);
    assert.equal(control.el.getAttribute('role'), 'tablist');
    assert.equal(control.el.getAttribute('aria-label'), 'Sport');
    assert.deepEqual(activeValues(control.el), ['running']);
    assert.deepEqual(selectedValues(control.el), ['running']);
    assert.equal(control.getValue(), 'running');
  });

  it('adds extra classes next to .segmented', () => {
    const control = createSegmented({ options: SPORTS, value: 'hiking', onSelect: () => {}, className: 'time-toggle-modes' });
    assert.equal(control.el.className, 'segmented time-toggle-modes');
  });

  it('selects a clicked option and reports it', () => {
    const picked = [];
    const control = createSegmented({ options: SPORTS, value: 'hiking', onSelect: (v) => picked.push(v) });
    control.buttonFor('running').click();
    assert.deepEqual(picked, ['running']);
    assert.deepEqual(activeValues(control.el), ['running']);
    assert.deepEqual(selectedValues(control.el), ['running']);
  });

  it('select() moves the active option without reporting it', () => {
    const picked = [];
    const control = createSegmented({ options: SPORTS, value: 'hiking', onSelect: (v) => picked.push(v) });
    control.select('running');
    assert.deepEqual(picked, []);
    assert.equal(control.getValue(), 'running');
  });
});

describe('wireSegmented', () => {
  it('wires a control already in the markup', () => {
    const el = document.createElement('div');
    el.innerHTML =
      '<button class="segmented-option is-active" data-value="time" aria-selected="true">Time</button>' +
      '<button class="segmented-option" data-value="distance" aria-selected="false">Distance</button>';
    const picked = [];
    const control = wireSegmented(el, (v) => picked.push(v));
    assert.equal(control.getValue(), 'time');
    control.buttonFor('distance').click();
    assert.deepEqual(picked, ['distance']);
    assert.deepEqual(activeValues(el), ['distance']);
    assert.deepEqual(selectedValues(el), ['distance']);
  });
});
