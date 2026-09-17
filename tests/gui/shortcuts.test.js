import assert from 'node:assert/strict';
import { beforeEach, describe, it } from 'node:test';

import './testUtils/domSetup.js';
import { initShortcuts } from '../../src/gpx2fit/gui/js/shortcuts.js';

/**
 * Builds just enough of index.html's sidebar for the shortcuts to have real
 * controls to click, and records a click log per control — the point of every
 * assertion below is that a keystroke reaches the *existing* control, not that
 * this module reimplements what the control does.
 *
 * Mounted exactly once for the whole file: initShortcuts binds to `document`,
 * and those listeners outlive any teardown of the elements, so a second mount
 * would leave the first one still reacting to every keystroke. `reset()` puts
 * the single instance back to a known state between tests instead.
 */
function mount() {
  const sportControlEl = document.createElement('div');
  sportControlEl.id = 'sportControl';
  const sportClicks = [];
  ['hiking', 'running'].forEach((value) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'segmented-option';
    button.dataset.value = value;
    button.addEventListener('click', () => sportClicks.push(value));
    sportControlEl.append(button);
  });

  const runButton = document.createElement('button');
  runButton.type = 'button';
  const runClicks = [];
  runButton.addEventListener('click', () => runClicks.push('run'));

  const gpxClicks = [];
  const photoClicks = [];
  function fileInput(log, tag) {
    const input = document.createElement('input');
    input.type = 'file';
    input.addEventListener('click', (event) => {
      // jsdom would otherwise try to open a real picker.
      event.preventDefault();
      log.push(tag);
    });
    return input;
  }
  const gpxFileInput = fileInput(gpxClicks, 'gpx');
  const photoFileInput = fileInput(photoClicks, 'photo');

  const triggerButton = document.createElement('button');
  triggerButton.type = 'button';
  const panelContainer = document.createElement('div');

  document.body.append(sportControlEl, runButton, gpxFileInput, photoFileInput, triggerButton, panelContainer);

  initShortcuts({ triggerButton, panelContainer, sportControlEl, runButton, gpxFileInput, photoFileInput });

  const panel = panelContainer.querySelector('.shortcuts-panel');

  return {
    sportControlEl,
    sportClicks,
    runButton,
    runClicks,
    gpxClicks,
    photoClicks,
    triggerButton,
    panel,
    reset() {
      [sportClicks, runClicks, gpxClicks, photoClicks].forEach((log) => log.splice(0));
      runButton.disabled = false;
      panel.classList.add('hidden');
      triggerButton.setAttribute('aria-expanded', 'false');
    },
  };
}

const ctx = mount();

function press(init, target = document.body) {
  const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(event);
  return event;
}

function isPanelOpen() {
  return !ctx.panel.classList.contains('hidden');
}

describe('initShortcuts', () => {
  beforeEach(() => ctx.reset());

  describe('sport switching', () => {
    it('Alt+H clicks the Hiking option and Alt+R the Running one', () => {
      press({ code: 'KeyH', altKey: true });
      press({ code: 'KeyR', altKey: true });
      assert.deepEqual(ctx.sportClicks, ['hiking', 'running']);
    });

    it('matches on event.code, so macOS Option+H ("˙") still works', () => {
      press({ code: 'KeyH', key: '˙', altKey: true });
      assert.deepEqual(ctx.sportClicks, ['hiking']);
    });

    it('ignores Alt+Shift+H and Ctrl+Alt+H', () => {
      press({ code: 'KeyH', altKey: true, shiftKey: true });
      press({ code: 'KeyH', altKey: true, ctrlKey: true });
      assert.deepEqual(ctx.sportClicks, []);
    });

    it('still fires while the user is focused in a text input', () => {
      // Alt-modified keys are unambiguous, unlike the plain "?" below.
      const input = document.createElement('input');
      document.body.append(input);
      press({ code: 'KeyR', altKey: true }, input);
      input.remove();
      assert.deepEqual(ctx.sportClicks, ['running']);
    });

    it('advertises the binding via aria-keyshortcuts', () => {
      const hiking = ctx.sportControlEl.querySelector('[data-value="hiking"]');
      assert.equal(hiking.getAttribute('aria-keyshortcuts'), 'Alt+H');
    });
  });

  describe('convert', () => {
    it('Ctrl+Enter clicks the convert button when it is enabled', () => {
      press({ key: 'Enter', ctrlKey: true });
      assert.deepEqual(ctx.runClicks, ['run']);
    });

    it('Cmd+Enter works too, for macOS', () => {
      press({ key: 'Enter', metaKey: true });
      assert.deepEqual(ctx.runClicks, ['run']);
    });

    it('does nothing while the button is disabled (not ready, or mid-conversion)', () => {
      ctx.runButton.disabled = true;
      const event = press({ key: 'Enter', ctrlKey: true });
      assert.deepEqual(ctx.runClicks, []);
      // Nothing happened, so the keystroke is left to the browser.
      assert.equal(event.defaultPrevented, false);
    });

    it('a bare Enter does not convert', () => {
      press({ key: 'Enter' });
      assert.deepEqual(ctx.runClicks, []);
    });
  });

  describe('file pickers', () => {
    it('Alt+U opens the GPX picker and Alt+P the photo picker', () => {
      press({ code: 'KeyU', altKey: true });
      press({ code: 'KeyP', altKey: true });
      assert.deepEqual(ctx.gpxClicks, ['gpx']);
      assert.deepEqual(ctx.photoClicks, ['photo']);
    });
  });

  describe('the shortcuts panel', () => {
    it('lists one row per bound shortcut', () => {
      assert.equal(isPanelOpen(), false);
      assert.equal(ctx.panel.querySelectorAll('.shortcut-row').length, 7);
      assert.equal(ctx.triggerButton.getAttribute('aria-expanded'), 'false');
    });

    it('opens and closes on the trigger button, keeping aria-expanded in sync', () => {
      ctx.triggerButton.click();
      assert.equal(isPanelOpen(), true);
      assert.equal(ctx.triggerButton.getAttribute('aria-expanded'), 'true');

      ctx.triggerButton.click();
      assert.equal(isPanelOpen(), false);
      assert.equal(ctx.triggerButton.getAttribute('aria-expanded'), 'false');
    });

    it('toggles on "?" and closes on Escape', () => {
      press({ key: '?' });
      assert.equal(isPanelOpen(), true);
      press({ key: 'Escape' });
      assert.equal(isPanelOpen(), false);
    });

    it('ignores "?" typed into an input, since it is a plain character', () => {
      const input = document.createElement('input');
      document.body.append(input);
      press({ key: '?' }, input);
      input.remove();
      assert.equal(isPanelOpen(), false);
    });

    it('leaves Escape to the field handlers while the panel is closed', () => {
      // dateTimeField.js blurs a time box on Escape — suppressing the event
      // here would break that.
      const event = press({ key: 'Escape' });
      assert.equal(event.defaultPrevented, false);
    });
  });

  it('ignores a keystroke another handler already consumed', () => {
    document.body.addEventListener('keydown', (event) => event.preventDefault(), { once: true });
    press({ code: 'KeyH', altKey: true });
    assert.deepEqual(ctx.sportClicks, []);
  });
});
