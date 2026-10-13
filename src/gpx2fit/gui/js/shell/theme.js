// Light/dark theme persistence and application. Whenever the theme changes
// it sets the `data-theme` attribute (which css/tokens.css keys off of), the
// toggle button's icon and the browser toolbar's tint, then tells `onChange`.
// That is how the map restyles itself (map.js's setTheme: Leaflet tile layers
// can't be restyled with CSS alone) without this module knowing about it.

import { readCssVar } from '../util/cssVars.js';
import { readSetting, writeSetting } from '../state/storage.js';

const SUN_ICON =
  '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<circle cx="12" cy="12" r="4"></circle>' +
  '<line x1="12" y1="2" x2="12" y2="4"></line><line x1="12" y1="20" x2="12" y2="22"></line>' +
  '<line x1="4.93" y1="4.93" x2="6.34" y2="6.34"></line><line x1="17.66" y1="17.66" x2="19.07" y2="19.07"></line>' +
  '<line x1="2" y1="12" x2="4" y2="12"></line><line x1="20" y1="12" x2="22" y2="12"></line>' +
  '<line x1="4.93" y1="19.07" x2="6.34" y2="17.66"></line><line x1="17.66" y1="6.34" x2="19.07" y2="4.93"></line>' +
  '</svg>';
const MOON_ICON =
  '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>' +
  '</svg>';

/**
 * Wires up the theme toggle button and applies the initial theme (persisted
 * choice, falling back to the OS `prefers-color-scheme`). Call once at
 * startup, once whatever `onChange` restyles exists (the map).
 *
 * @param {HTMLElement} themeToggleButton
 * @param {{ onChange?: (theme: 'light'|'dark') => void }} [opts] - runs for
 *   the initial theme and every change after, once `data-theme` is set, so
 *   CSS variables read in it are the new theme's
 */
export function initTheme(themeToggleButton, { onChange } = {}) {
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    themeToggleButton.innerHTML = theme === 'dark' ? SUN_ICON : MOON_ICON;
    // A phone browser tints its toolbar with this, so it follows the page.
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', readCssVar('--bg-base'));
    onChange?.(theme);
  }

  themeToggleButton.addEventListener('click', () => {
    const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    writeSetting('theme', next);
    applyTheme(next);
  });

  const stored = readSetting('theme');
  if (stored === 'light' || stored === 'dark') {
    applyTheme(stored);
    return;
  }
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  applyTheme(prefersDark ? 'dark' : 'light');
}
