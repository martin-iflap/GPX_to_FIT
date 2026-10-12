// Reads the theme's color tokens (styles.css `:root` and its dark blocks)
// for what CSS can't style: Leaflet's route line and hover dot, and the
// browser toolbar's theme-color. Read on each use, since the theme can change.

/** The fallback when a token is missing, e.g. styles.css not loaded yet. */
const ACCENT_FALLBACK = '#0088ff';

/**
 * The current value of a custom property on the root element, trimmed, or
 * '' when it isn't set.
 * @param {string} name - e.g. '--bg-base'
 * @returns {string}
 */
export function readCssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** The theme's `--accent` color. @returns {string} */
export function accentColor() {
  return readCssVar('--accent') || ACCENT_FALLBACK;
}
