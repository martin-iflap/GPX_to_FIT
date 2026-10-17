import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import { createSegmented, wireSegmented } from '../../../src/gpx2fit/gui/js/inputs/segmented.js';

function options(el) {
  return [...el.querySelectorAll('.segmented-option')];
}

function activeValues(el) {
  return options(el)
    .filter((button) => button.classList.contains('is-active'))
    .map((button) => button.dataset.value);
}

function attrValues(el, name) {
  return options(el).map((button) => button.getAttribute(name));
}

function press(target, key) {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
  target.dispatchEvent(event);
  return event;
}

const SPORTS = [
  { value: 'hiking', label: 'Hiking' },
  { value: 'running', label: 'Running' },
];

const THREE = [
  { value: 'a', label: 'A' },
  { value: 'b', label: 'B' },
  { value: 'c', label: 'C' },
];

/** A control mounted in the document, so focus can be checked. */
function mounted(opts) {
  const picked = [];
  const control = createSegmented({ onSelect: (v) => picked.push(v), ...opts });
  document.body.append(control.el);
  return { control, picked, el: control.el };
}

describe('createSegmented', () => {
  it('builds a radio group by default, with only the given value checked', () => {
    const control = createSegmented({ options: SPORTS, value: 'running', onSelect: () => {}, ariaLabel: 'Sport' });
    assert.deepEqual(options(control.el).map((b) => b.textContent), ['Hiking', 'Running']);
    assert.equal(control.el.getAttribute('role'), 'radiogroup');
    assert.deepEqual(attrValues(control.el, 'role'), ['radio', 'radio']);
    assert.deepEqual(attrValues(control.el, 'aria-checked'), ['false', 'true']);
    assert.deepEqual(attrValues(control.el, 'aria-selected'), [null, null]);
    assert.equal(control.el.getAttribute('aria-label'), 'Sport');
    assert.deepEqual(activeValues(control.el), ['running']);
    assert.equal(control.getValue(), 'running');
  });

  it("builds tabs with kind 'tabs', marked by aria-selected", () => {
    const control = createSegmented({ options: SPORTS, value: 'hiking', onSelect: () => {}, kind: 'tabs' });
    assert.equal(control.el.getAttribute('role'), 'tablist');
    assert.deepEqual(attrValues(control.el, 'role'), ['tab', 'tab']);
    assert.deepEqual(attrValues(control.el, 'aria-selected'), ['true', 'false']);
    assert.deepEqual(attrValues(control.el, 'aria-checked'), [null, null]);
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
    assert.deepEqual(attrValues(control.el, 'aria-checked'), ['false', 'true']);
  });

  it('select() moves the active option without reporting it', () => {
    const picked = [];
    const control = createSegmented({ options: SPORTS, value: 'hiking', onSelect: (v) => picked.push(v) });
    control.select('running');
    assert.deepEqual(picked, []);
    assert.equal(control.getValue(), 'running');
    assert.deepEqual(attrValues(control.el, 'aria-checked'), ['false', 'true']);
  });
});

describe('segmented keyboard model', () => {
  it('puts only the active option in the tab order, and keeps it there as the choice moves', () => {
    const { control, el } = mounted({ options: THREE, value: 'b' });
    assert.deepEqual(attrValues(el, 'tabindex'), ['-1', '0', '-1']);
    control.buttonFor('c').click();
    assert.deepEqual(attrValues(el, 'tabindex'), ['-1', '-1', '0']);
    control.select('a');
    assert.deepEqual(attrValues(el, 'tabindex'), ['0', '-1', '-1']);
    el.remove();
  });

  it('ArrowRight and ArrowDown select, report and focus the next option', () => {
    const { control, picked, el } = mounted({ options: THREE, value: 'a' });
    control.buttonFor('a').focus();
    press(control.buttonFor('a'), 'ArrowRight');
    assert.deepEqual(picked, ['b']);
    assert.equal(control.getValue(), 'b');
    assert.equal(document.activeElement, control.buttonFor('b'));
    press(control.buttonFor('b'), 'ArrowDown');
    assert.deepEqual(picked, ['b', 'c']);
    assert.equal(document.activeElement, control.buttonFor('c'));
    el.remove();
  });

  it('ArrowLeft and ArrowUp go back', () => {
    const { control, picked, el } = mounted({ options: THREE, value: 'c' });
    press(control.buttonFor('c'), 'ArrowLeft');
    press(control.buttonFor('b'), 'ArrowUp');
    assert.deepEqual(picked, ['b', 'a']);
    assert.equal(document.activeElement, control.buttonFor('a'));
    el.remove();
  });

  it('wraps around at both ends', () => {
    const { control, picked, el } = mounted({ options: THREE, value: 'c' });
    press(control.buttonFor('c'), 'ArrowRight');
    assert.deepEqual(picked, ['a']);
    press(control.buttonFor('a'), 'ArrowLeft');
    assert.deepEqual(picked, ['a', 'c']);
    el.remove();
  });

  it('Home and End jump to the first and last option', () => {
    const { control, picked, el } = mounted({ options: THREE, value: 'b' });
    press(control.buttonFor('b'), 'End');
    press(control.buttonFor('c'), 'Home');
    assert.deepEqual(picked, ['c', 'a']);
    assert.equal(document.activeElement, control.buttonFor('a'));
    el.remove();
  });

  it("doesn't report a key that lands on the option already chosen", () => {
    const { control, picked, el } = mounted({ options: THREE, value: 'a' });
    const event = press(control.buttonFor('a'), 'Home');
    assert.deepEqual(picked, []);
    assert.equal(event.defaultPrevented, true);
    el.remove();
  });

  it('prevents the default of the keys it handles (so the page does not scroll), and no others', () => {
    const { control, picked, el } = mounted({ options: THREE, value: 'a' });
    assert.equal(press(control.buttonFor('a'), 'ArrowDown').defaultPrevented, true);
    assert.equal(press(control.buttonFor('b'), 'Tab').defaultPrevented, false);
    assert.equal(press(control.buttonFor('b'), 'x').defaultPrevented, false);
    assert.deepEqual(picked, ['b']);
    el.remove();
  });

  it('moves from the focused option, not the checked one', () => {
    // After a click and Shift+Tab back in, focus and choice agree, but a
    // control the code re-selected may not: the key acts where focus is.
    const { control, picked, el } = mounted({ options: THREE, value: 'a' });
    press(control.buttonFor('b'), 'ArrowRight');
    assert.deepEqual(picked, ['c']);
    el.remove();
  });

  it('ignores a key that comes from anything but an option', () => {
    const { control, picked, el } = mounted({ options: THREE, value: 'b' });
    const event = press(el, 'ArrowRight');
    assert.deepEqual(picked, []);
    assert.equal(event.defaultPrevented, false);
    assert.equal(control.getValue(), 'b');
    el.remove();
  });

  it('skips a hidden option', () => {
    const { control, picked, el } = mounted({ options: THREE, value: 'a' });
    control.buttonFor('b').hidden = true;
    press(control.buttonFor('a'), 'ArrowRight');
    assert.deepEqual(picked, ['c']);
    el.remove();
  });
});

describe('thumb', () => {
  // jsdom lays nothing out, so the layout is faked: a 400 px control whose
  // options sit at 4, 134 and 264 px, each 132 px wide.
  function laidOut(value) {
    const control = createSegmented({ options: THREE, value: 'a', onSelect: () => {} });
    Object.defineProperty(control.el, 'clientWidth', { value: 400 });
    options(control.el).forEach((button, i) => {
      Object.defineProperty(button, 'offsetLeft', { value: 4 + i * 130 });
      Object.defineProperty(button, 'offsetWidth', { value: 132 });
    });
    control.select(value);
    return control;
  }

  const edges = (el) => [el.style.getPropertyValue('--thumb-left'), el.style.getPropertyValue('--thumb-right')];

  it('sits under the active option, its edges in percent of the control', () => {
    const { el } = laidOut('b');
    assert.deepEqual(edges(el), ['33.5%', '33.5%']);
    assert.ok(el.classList.contains('has-thumb'));
  });

  it('records which way it moves, so the right edge leads', () => {
    const control = laidOut('a');
    assert.deepEqual(edges(control.el), ['1%', '66%']);
    control.buttonFor('c').click();
    assert.deepEqual(edges(control.el), ['66%', '1%']);
    assert.equal(control.el.dataset.thumbMoving, 'right');
    control.select('b');
    assert.equal(control.el.dataset.thumbMoving, 'left');
  });

  it('keeps the direction when the choice does not move it', () => {
    const control = laidOut('a');
    control.select('c');
    control.select('c');
    assert.equal(control.el.dataset.thumbMoving, 'right');
  });

  it('is not placed before the control is laid out, so the active background shows', () => {
    const control = createSegmented({ options: THREE, value: 'b', onSelect: () => {} });
    assert.equal(control.el.classList.contains('has-thumb'), false);
    assert.deepEqual(edges(control.el), ['', '']);
  });

  it('goes away when no option is active', () => {
    const control = laidOut('b');
    control.select('none of them');
    assert.equal(control.el.classList.contains('has-thumb'), false);
  });

  it('goes away when the active option is hidden', () => {
    const control = laidOut('b');
    control.buttonFor('b').hidden = true;
    control.select('b');
    assert.equal(control.el.classList.contains('has-thumb'), false);
  });
});

describe('linkPanel', () => {
  it('ties a tab to the panel it shows, both ways', () => {
    const control = createSegmented({ options: SPORTS, value: 'hiking', onSelect: () => {}, kind: 'tabs' });
    const panel = document.createElement('div');
    control.linkPanel('running', panel);
    const tab = control.buttonFor('running');
    assert.equal(panel.getAttribute('role'), 'tabpanel');
    assert.ok(panel.id, 'the panel gets an id');
    assert.ok(tab.id, 'the tab gets an id');
    assert.equal(tab.getAttribute('aria-controls'), panel.id);
    assert.equal(panel.getAttribute('aria-labelledby'), tab.id);
  });

  it('gives every control its own ids', () => {
    const first = createSegmented({ options: SPORTS, value: 'hiking', onSelect: () => {}, kind: 'tabs' });
    const second = createSegmented({ options: SPORTS, value: 'hiking', onSelect: () => {}, kind: 'tabs' });
    const panelA = document.createElement('div');
    const panelB = document.createElement('div');
    first.linkPanel('hiking', panelA);
    second.linkPanel('hiking', panelB);
    assert.notEqual(panelA.id, panelB.id);
    assert.notEqual(first.buttonFor('hiking').id, second.buttonFor('hiking').id);
  });

  it('keeps an id the panel already has', () => {
    const control = createSegmented({ options: SPORTS, value: 'hiking', onSelect: () => {}, kind: 'tabs' });
    const panel = document.createElement('div');
    panel.id = 'mine';
    control.linkPanel('hiking', panel);
    assert.equal(panel.id, 'mine');
    assert.equal(control.buttonFor('hiking').getAttribute('aria-controls'), 'mine');
  });

  it('keeps an id the tab already has', () => {
    const control = createSegmented({ options: SPORTS, value: 'hiking', onSelect: () => {}, kind: 'tabs' });
    control.buttonFor('hiking').id = 'hikingTab';
    const panel = document.createElement('div');
    control.linkPanel('hiking', panel);
    assert.equal(control.buttonFor('hiking').id, 'hikingTab');
    assert.equal(panel.getAttribute('aria-labelledby'), 'hikingTab');
  });
});

describe('wireSegmented', () => {
  function markup() {
    const el = document.createElement('div');
    el.innerHTML =
      '<button class="segmented-option is-active" data-value="time">Time</button>' +
      '<button class="segmented-option" data-value="distance">Distance</button>';
    return el;
  }

  it('wires a control already in the markup, as a radio group by default', () => {
    const el = markup();
    const picked = [];
    const control = wireSegmented(el, (v) => picked.push(v));
    assert.equal(control.getValue(), 'time');
    assert.equal(el.getAttribute('role'), 'radiogroup');
    assert.deepEqual(attrValues(el, 'role'), ['radio', 'radio']);
    assert.deepEqual(attrValues(el, 'aria-checked'), ['true', 'false']);
    assert.deepEqual(attrValues(el, 'tabindex'), ['0', '-1']);
    control.buttonFor('distance').click();
    assert.deepEqual(picked, ['distance']);
    assert.deepEqual(activeValues(el), ['distance']);
    assert.deepEqual(attrValues(el, 'aria-checked'), ['false', 'true']);
  });

  it('takes the arrow keys too', () => {
    const el = markup();
    document.body.append(el);
    const picked = [];
    const control = wireSegmented(el, (v) => picked.push(v));
    press(control.buttonFor('time'), 'ArrowRight');
    assert.deepEqual(picked, ['distance']);
    assert.equal(document.activeElement, control.buttonFor('distance'));
    el.remove();
  });

  it('keeps the first option tabbable when none is active', () => {
    const el = markup();
    el.querySelector('.is-active').classList.remove('is-active');
    wireSegmented(el, () => {});
    assert.deepEqual(attrValues(el, 'tabindex'), ['0', '-1']);
  });
});
