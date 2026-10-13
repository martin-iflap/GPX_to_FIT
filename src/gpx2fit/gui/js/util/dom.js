// Looking up the page's own elements. index.html and the code have to agree
// on every id and on the markup inside a component; a lookup that comes back
// empty is a bug in one of them, so it fails at startup, naming what's
// missing, rather than as a null somewhere later.

/**
 * The element with the given id, checked to be of the expected type (which
 * also types it: `requireElement('runButton', HTMLButtonElement)` is an
 * `HTMLButtonElement`).
 *
 * @template {HTMLElement} [T=HTMLElement]
 * @param {string} id
 * @param {new (...args: any[]) => T} [type] - the expected element class,
 *   `HTMLElement` by default
 * @returns {T}
 */
export function requireElement(id, type) {
  return checked(document.getElementById(id), `#${id}`, type);
}

/**
 * The first element inside `parent` matching `selector`, checked and typed
 * like `requireElement`.
 *
 * @template {HTMLElement} [T=HTMLElement]
 * @param {ParentNode} parent
 * @param {string} selector
 * @param {new (...args: any[]) => T} [type]
 * @returns {T}
 */
export function requireChild(parent, selector, type) {
  return checked(parent.querySelector(selector), selector, type);
}

/**
 * @template {HTMLElement} T
 * @param {Element|null} el
 * @param {string} what - how the error names the element
 * @param {new (...args: any[]) => T} [type]
 * @returns {T}
 */
function checked(el, what, type) {
  // The default only applies while T is HTMLElement, which TypeScript can't see.
  const expected = type ?? /** @type {new (...args: any[]) => T} */ (/** @type {unknown} */ (HTMLElement));
  if (!el) {
    throw new Error(`The page has no ${what}`);
  }
  if (!(el instanceof expected)) {
    throw new Error(`${what} is a <${el.tagName.toLowerCase()}>, not the ${expected.name} expected`);
  }
  return el;
}
