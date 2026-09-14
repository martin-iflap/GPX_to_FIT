// Minimal jsdom global setup for tests that need a real DOM (element creation,
// focus tracking, event dispatch) but run under Node's built-in test runner
// rather than a browser or a bundler-backed runner. Importing this module for
// its side effect populates `globalThis` with `window`/`document`/`Event`/etc.
// before any test in the importing file runs.
//
// Node's test runner (`node --test`) runs each matched file as its own
// process, so this setup must be (and is) re-imported per test file rather
// than shared via a single global setup script.
//
// Deliberately overwrites globals that Node already provides (e.g. its own
// built-in `Event`) rather than skipping them: an `Event` constructed from
// Node's built-in class isn't recognized by a jsdom `EventTarget`'s
// `dispatchEvent` ("parameter 1 is not of type 'Event'"), so every DOM-shaped
// global must come from the same jsdom realm as `document`.
//
// Timer functions are the one deliberate exception: jsdom's own
// window.setTimeout/setInterval are implemented by calling the *real*,
// unqualified global setTimeout internally. Overwriting that global with
// jsdom's own wrapper makes jsdom call itself instead of Node's real timer,
// which recurses forever ("Maximum call stack size exceeded"). Source under
// gui/js/ always calls `window.setTimeout` explicitly (never a bare
// `setTimeout`), so skipping these here loses nothing.
const SKIP_KEYS = new Set(['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'queueMicrotask']);

import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });

for (const key of Object.getOwnPropertyNames(dom.window)) {
  if (SKIP_KEYS.has(key)) {
    continue;
  }
  try {
    globalThis[key] = dom.window[key];
  } catch {
    // A handful of window properties (e.g. some prototype getters) throw
    // when read outside a real browser context — skip those, nothing in
    // this codebase's tests needs them.
  }
}

globalThis.window = dom.window;
globalThis.document = dom.window.document;
