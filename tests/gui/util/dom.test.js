import '../testUtils/domSetup.js';

import assert from 'node:assert/strict';
import { beforeEach, describe, it } from 'node:test';

import { requireChild, requireElement } from '../../../src/gpx2fit/gui/js/util/dom.js';

describe('requireElement', () => {
  beforeEach(() => {
    document.body.innerHTML = '<button id="go">Go</button><div id="box"></div>';
  });

  it('returns the element with that id', () => {
    assert.equal(requireElement('box'), document.getElementById('box'));
  });

  it('accepts an element of the expected type', () => {
    assert.equal(requireElement('go', HTMLButtonElement).textContent, 'Go');
  });

  it('throws, naming the id, when there is no such element', () => {
    assert.throws(() => requireElement('missing'), /The page has no #missing/);
  });

  it('throws when the element is of another type', () => {
    assert.throws(() => requireElement('box', HTMLButtonElement), /#box is a <div>, not the HTMLButtonElement expected/);
  });
});

describe('requireChild', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="panel"><button class="close">×</button><p class="body"></p></div>';
  });

  it('returns the first match inside the parent, of the expected type', () => {
    const panel = requireElement('panel');
    assert.equal(requireChild(panel, '.close', HTMLButtonElement).textContent, '×');
  });

  it('throws, naming the selector, when nothing inside matches', () => {
    assert.throws(() => requireChild(requireElement('panel'), '.missing'), /The page has no \.missing/);
  });

  it('throws when the match is of another type', () => {
    assert.throws(() => requireChild(requireElement('panel'), '.body', HTMLButtonElement), /\.body is a <p>/);
  });
});
