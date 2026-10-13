// Checks on the two HTML pages themselves, for the mistakes nothing else
// would catch until the page looked wrong in a browser: a stylesheet in css/
// that no page links (or a link to one that doesn't exist), the cascade order
// the overrides rely on, and a CDN file loaded without its integrity hash.

import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { describe, it } from 'node:test';
import { fileURLToPath } from 'node:url';

import { JSDOM } from 'jsdom';

const GUI = fileURLToPath(new URL('../../src/gpx2fit/gui/', import.meta.url));

function page(name) {
  return new JSDOM(readFileSync(`${GUI}${name}`, 'utf8')).window.document;
}

function localStylesheets(doc) {
  return [...doc.querySelectorAll('link[rel="stylesheet"]')]
    .map((link) => link.getAttribute('href'))
    .filter((href) => !/^https?:/.test(href));
}

const cssFiles = readdirSync(`${GUI}css`).filter((name) => name.endsWith('.css'));

describe('stylesheets', () => {
  const index = localStylesheets(page('index.html'));
  const about = localStylesheets(page('about.html'));

  it('every page links only files that exist', () => {
    for (const href of [...index, ...about]) {
      assert.ok(href.startsWith('css/'), `${href} is outside css/`);
      assert.ok(cssFiles.includes(href.slice('css/'.length)), `${href} does not exist`);
    }
  });

  it('the app links every file in css/ but the about page\'s, each once', () => {
    const expected = cssFiles.filter((name) => name !== 'about.css').map((name) => `css/${name}`);
    assert.deepEqual([...index].sort(), expected.sort());
  });

  it('the app loads the tokens and base first, and the phone layout and glass last', () => {
    // Later files override earlier ones by order alone (same specificity).
    assert.deepEqual(index.slice(0, 2), ['css/tokens.css', 'css/base.css']);
    assert.deepEqual(index.slice(-2), ['css/responsive.css', 'css/glass.css']);
  });

  it('the about page loads the tokens, the base and its own file, in that order', () => {
    assert.deepEqual(about, ['css/tokens.css', 'css/base.css', 'css/about.css']);
  });
});

describe('CDN files', () => {
  for (const name of ['index.html', 'about.html']) {
    it(`${name} pins every one with its integrity hash`, () => {
      const doc = page(name);
      const external = [
        ...[...doc.querySelectorAll('script[src]')].map((el) => [el, el.getAttribute('src')]),
        ...[...doc.querySelectorAll('link[rel="stylesheet"]')].map((el) => [el, el.getAttribute('href')]),
      ].filter(([, url]) => /^https?:/.test(url) && !url.startsWith('https://fonts.googleapis.com/'));

      for (const [el, url] of external) {
        assert.match(el.getAttribute('integrity') ?? '', /^sha(256|384|512)-[A-Za-z0-9+/]+={0,2}$/, `${url} has no integrity`);
        assert.equal(el.getAttribute('crossorigin'), 'anonymous', `${url} needs crossorigin="anonymous" for its integrity check`);
        // An unpinned URL (leaflet@latest) changes under the hash and breaks the page.
        assert.match(url, /@\d+\.\d+\.\d+\/|\/v\d+\.\d+\.\d+\//, `${url} is not pinned to a version`);
      }
    });
  }

  it('index.html loads Leaflet and exifr, so the check above has something to check', () => {
    const scripts = [...page('index.html').querySelectorAll('script[src]')].map((el) => el.getAttribute('src'));
    assert.ok(scripts.some((src) => src.includes('/leaflet@')));
    assert.ok(scripts.some((src) => src.includes('/exifr@')));
  });
});
