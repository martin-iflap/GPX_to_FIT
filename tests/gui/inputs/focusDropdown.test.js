import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import { createFocusDropdown } from '../../../src/gpx2fit/gui/js/inputs/focusDropdown.js';

// The list closes on blur only once the focus change has settled (a 0 ms
// window.setTimeout), so tests wait past that.
const settle = () => new Promise((resolve) => setTimeout(resolve, 5));

const OPTIONS = ['a', 'b', 'c'].map((value) => ({ label: value.toUpperCase(), value }));

function mount(extra = {}) {
  const wrapper = document.createElement('div');
  const first = document.createElement('input');
  const second = document.createElement('input');
  const outside = document.createElement('input');
  wrapper.append(first, second);
  document.body.append(wrapper, outside);
  const picks = [];
  let builds = 0;
  const dropdown = createFocusDropdown({
    triggers: [first, second],
    className: 'list',
    optionClassName: 'list-option',
    getOptions: () => {
      builds += 1;
      return OPTIONS;
    },
    onPick: (value) => picks.push(value),
    ...extra,
  });
  wrapper.append(dropdown.el);
  return { first, second, outside, dropdown, picks, builds: () => builds };
}

const isOpen = (dropdown) => !dropdown.el.classList.contains('hidden');
const labels = (dropdown) => [...dropdown.el.children].map((option) => option.textContent);

afterEach(() => {
  document.body.replaceChildren();
});

describe('createFocusDropdown, opened on focus', () => {
  it('starts closed and opens with fresh options when a trigger is focused', () => {
    const { first, dropdown, builds } = mount();
    assert.ok(!isOpen(dropdown));
    first.focus();
    assert.ok(isOpen(dropdown));
    assert.deepEqual(labels(dropdown), ['A', 'B', 'C']);
    assert.equal(builds(), 1);
  });

  it('stays open while focus moves between its triggers', async () => {
    const { first, second, dropdown } = mount();
    first.focus();
    second.focus();
    await settle();
    assert.ok(isOpen(dropdown));
  });

  it('closes once focus leaves the triggers', async () => {
    const { first, outside, dropdown } = mount();
    first.focus();
    outside.focus();
    await settle();
    assert.ok(!isOpen(dropdown));
  });

  it('closes on Escape', async () => {
    const { first, dropdown } = mount();
    first.focus();
    first.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await settle();
    assert.ok(!isOpen(dropdown));
  });

  it("keeps the trigger's focus through an option's mousedown", () => {
    const { first, dropdown } = mount();
    first.focus();
    const mousedown = new MouseEvent('mousedown', { cancelable: true });
    dropdown.el.children[1].dispatchEvent(mousedown);
    assert.equal(mousedown.defaultPrevented, true);
  });

  it('reports a pick, closes, and hands focus back to the last trigger without reopening', () => {
    const { second, dropdown, picks, builds } = mount();
    second.focus();
    // A keyboard user can Tab onto an option, so focus really leaves the box.
    dropdown.el.children[2].focus();
    dropdown.el.children[2].click();
    assert.deepEqual(picks, ['c']);
    assert.ok(!isOpen(dropdown));
    assert.equal(document.activeElement, second);
    assert.equal(builds(), 1);
  });

  it('scrolls to the option getScrollIndex names', () => {
    const indexes = [];
    const { first } = mount({
      getScrollIndex: (options) => {
        indexes.push(options.length);
        return 2;
      },
    });
    first.focus();
    // jsdom has no layout, so only the call is observable.
    assert.deepEqual(indexes, [3]);
  });
});

describe('createFocusDropdown, opened on click', () => {
  it('toggles on clicks and not on focus', () => {
    const button = document.createElement('button');
    document.body.append(button);
    const dropdown = createFocusDropdown({
      triggers: [button],
      openOn: 'click',
      className: 'list',
      optionClassName: 'list-option',
      getOptions: () => OPTIONS,
      onPick: () => {},
    });
    document.body.append(dropdown.el);
    button.focus();
    assert.ok(!isOpen(dropdown));
    button.click();
    assert.ok(isOpen(dropdown));
    button.click();
    assert.ok(!isOpen(dropdown));
  });
});
