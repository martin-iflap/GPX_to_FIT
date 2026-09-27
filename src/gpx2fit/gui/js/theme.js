// Light/dark theme persistence and application. Syncs three things whenever
// the theme changes: the `data-theme` attribute (which styles.css keys off
// of), the toggle button's icon, and the map's basemap tiles + route line
// color (both live in map.js, since Leaflet tile layers can't be restyled
// with CSS alone).

import * as mapModule from './map.js';

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
 * startup, after `mapModule.initMap`.
 *
 * @param {HTMLElement} themeToggleButton
 */
export function initTheme(themeToggleButton) {
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    themeToggleButton.innerHTML = theme === 'dark' ? SUN_ICON : MOON_ICON;
    mapModule.setMapTheme(theme);
    const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
    mapModule.updateRouteColor(accent);
    // A phone browser tints its toolbar with this, so it follows the page.
    const background = getComputedStyle(document.documentElement).getPropertyValue('--bg-base').trim();
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', background);
  }

  themeToggleButton.addEventListener('click', () => {
    const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    try {
      localStorage.setItem('theme', next);
    } catch {
      // Not remembered this time; the switch itself still applies.
    }
    applyTheme(next);
  });

  // Storage can throw (site data blocked). Uncaught here, that would stop
  // main.js before it wires everything after the theme.
  let stored = null;
  try {
    stored = localStorage.getItem('theme');
  } catch {
    // Fall back to the OS preference below.
  }
  if (stored === 'light' || stored === 'dark') {
    applyTheme(stored);
    return;
  }
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  applyTheme(prefersDark ? 'dark' : 'light');
}
