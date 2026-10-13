// Turns on the liquid-glass refraction (css/glass.css's html.glass-refraction
// rules, which run index.html's #liquidGlass filter in backdrop-filter) in
// browsers that render it. Only Chromium applies an SVG filter as a
// backdrop-filter; Safari and Firefox would drop the whole declaration and
// lose the blur too, so they never get the class and keep the plain glass.
//
// `@supports` only tells whether the value parses, not whether the filter
// is actually rendered, hence a brand check rather than a feature query.

/**
 * Whether this browser bends the backdrop through an SVG filter.
 * @param {Navigator|{userAgentData?: {brands?: {brand: string}[]}}} nav
 * @returns {boolean}
 */
export function supportsBackdropRefraction(nav) {
  // userAgentData is Chromium-only, so the DOM types don't have it.
  const brands = /** @type {{userAgentData?: {brands?: {brand: string}[]}}} */ (nav)?.userAgentData?.brands ?? [];
  return brands.some(({ brand }) => brand === 'Chromium');
}

/**
 * Sets `.glass-refraction` on the root element when supported. Call once at
 * startup; only index.html carries the filter, so about.html doesn't call it.
 * @param {HTMLElement} [root]
 */
export function initGlass(root = document.documentElement) {
  root.classList.toggle('glass-refraction', supportsBackdropRefraction(navigator));
}
