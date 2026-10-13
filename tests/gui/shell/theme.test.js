// initTheme: which theme the page opens in, the toggle, and telling whoever
// restyles itself (the map) about each change through `onChange`, once
// `data-theme` already holds the new theme.

import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import { initTheme } from '../../../src/gpx2fit/gui/js/shell/theme.js';

function stubPrefersDark(prefersDark) {
  window.matchMedia = (query) => ({ matches: query === '(prefers-color-scheme: dark)' && prefersDark });
}

function mount({ stored, prefersDark = false } = {}) {
  if (stored) {
    localStorage.setItem('theme', stored);
  }
  stubPrefersDark(prefersDark);
  const button = document.createElement('button');
  document.body.append(button);
  const changes = [];
  initTheme(button, {
    onChange: (theme) => changes.push({ theme, dataTheme: document.documentElement.getAttribute('data-theme') }),
  });
  return { button, changes };
}

const currentTheme = () => document.documentElement.getAttribute('data-theme');

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('initTheme', () => {
  it('opens in the stored theme, whatever the system prefers', () => {
    const { changes } = mount({ stored: 'light', prefersDark: true });
    assert.equal(currentTheme(), 'light');
    assert.deepEqual(changes.map((c) => c.theme), ['light']);
  });

  it('follows the system without a stored choice', () => {
    mount({ prefersDark: true });
    assert.equal(currentTheme(), 'dark');
  });

  it('ignores a stored value that is not a theme', () => {
    mount({ stored: 'sepia', prefersDark: false });
    assert.equal(currentTheme(), 'light');
  });

  it('tells onChange once data-theme already holds the new theme, so the map reads the new accent', () => {
    const { button, changes } = mount({ stored: 'light' });
    button.click();

    assert.deepEqual(changes, [
      { theme: 'light', dataTheme: 'light' },
      { theme: 'dark', dataTheme: 'dark' },
    ]);
  });

  it('the toggle switches the theme back and forth and remembers the choice', () => {
    const { button } = mount({ stored: 'dark' });
    button.click();
    assert.equal(currentTheme(), 'light');
    assert.equal(localStorage.getItem('theme'), 'light');

    button.click();
    assert.equal(currentTheme(), 'dark');
    assert.equal(localStorage.getItem('theme'), 'dark');
  });

  it('does not store the system\'s theme, only a choice the user made', () => {
    mount({ prefersDark: true });
    assert.equal(localStorage.getItem('theme'), null);
  });

  it('shows the sun in dark mode and the moon in light mode (the theme a click switches to)', () => {
    const { button } = mount({ stored: 'dark' });
    const darkIcon = button.innerHTML;
    button.click();
    const lightIcon = button.innerHTML;

    assert.match(darkIcon, /<circle/);
    assert.match(lightIcon, /<path/);
    assert.doesNotMatch(lightIcon, /<circle/);
  });

  it('works without an onChange', () => {
    stubPrefersDark(false);
    const button = document.createElement('button');
    assert.doesNotThrow(() => initTheme(button));
    assert.doesNotThrow(() => button.click());
  });
});
