import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it, mock } from 'node:test';

import '../testUtils/domSetup.js';
import { createStatus, STATUS_ERROR_TIMEOUT_MS } from '../../../src/gpx2fit/gui/js/shell/status.js';

describe('createStatus', () => {
  let statusEl;
  let setStatus;

  beforeEach(() => {
    mock.timers.enable({ apis: ['setTimeout'] });
    statusEl = document.createElement('p');
    setStatus = createStatus(statusEl);
  });

  afterEach(() => {
    mock.timers.reset();
  });

  const classes = () => [...statusEl.classList].sort();

  it('shows a plain message unstyled, and leaves it there', () => {
    setStatus('Parsing GPX…');
    assert.equal(statusEl.textContent, 'Parsing GPX…');
    assert.deepEqual(classes(), []);
    mock.timers.tick(STATUS_ERROR_TIMEOUT_MS * 2);
    assert.equal(statusEl.textContent, 'Parsing GPX…');
  });

  for (const [kind, className] of [['error', 'status-error'], ['input', 'status-input']]) {
    it(`styles ${kind} as ${className} and clears it back to "Ready." after the timeout`, () => {
      setStatus('Something is wrong', kind);
      assert.deepEqual(classes(), [className]);

      mock.timers.tick(STATUS_ERROR_TIMEOUT_MS - 1);
      assert.equal(statusEl.textContent, 'Something is wrong');
      mock.timers.tick(1);
      assert.equal(statusEl.textContent, 'Ready.');
      assert.deepEqual(classes(), []);
    });
  }

  it('keeps a warning: it explains the file just produced', () => {
    setStatus('Generated, but…', 'warning');
    assert.deepEqual(classes(), ['status-warning']);
    mock.timers.tick(STATUS_ERROR_TIMEOUT_MS * 2);
    assert.equal(statusEl.textContent, 'Generated, but…');
    assert.deepEqual(classes(), ['status-warning']);
  });

  it('a newer message cancels a pending clear, so it isn\'t wiped by an older error\'s timer', () => {
    setStatus('Bad input', 'input');
    mock.timers.tick(STATUS_ERROR_TIMEOUT_MS / 2);
    setStatus('Converting…');
    mock.timers.tick(STATUS_ERROR_TIMEOUT_MS);
    assert.equal(statusEl.textContent, 'Converting…');
  });

  it('a second error restarts the timeout', () => {
    setStatus('First', 'error');
    mock.timers.tick(STATUS_ERROR_TIMEOUT_MS / 2);
    setStatus('Second', 'error');
    mock.timers.tick(STATUS_ERROR_TIMEOUT_MS / 2 + 1);
    assert.equal(statusEl.textContent, 'Second');
    mock.timers.tick(STATUS_ERROR_TIMEOUT_MS / 2);
    assert.equal(statusEl.textContent, 'Ready.');
  });

  it('switches styles cleanly from one kind to the next', () => {
    setStatus('Oops', 'error');
    setStatus('Careful', 'warning');
    assert.deepEqual(classes(), ['status-warning']);
  });
});
